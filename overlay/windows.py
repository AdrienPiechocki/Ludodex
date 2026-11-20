"""windows.py — suivi de la fenêtre du jeu : son état « réduite » (l'overlay se cache avec elle).

Wayland n'offre à un client AUCUN moyen de voir les fenêtres des autres : chaque
compositeur s'interroge à sa façon. Trois sont pris en charge :

  * CyberRealm : `cyberrealm-exec windows` (sondage) ;
  * Hyprland   : `hyprctl clients -j` (sondage) ;
  * KDE/KWin   : un script KWin chargé par D-Bus, qui POUSSE l'état à l'overlay
                 (méthode `Update` de io.github.AdrienPiechocki.Ludodex.Overlay.Windows).

Ailleurs (sway, GNOME…), rien n'est suivi : échelle 1 et jamais réduit.

La fenêtre du jeu est reconnue par son PID (le processus lancé par Ludodex, ses
descendants, et les processus que `core.launch.motifs_jeu` rattache au jeu), à défaut
par le nom du jeu dans le titre ou l'app_id. Le calcul est pur (testable sans GTK)."""
import json, os, pathlib, re, shutil, time
from collections import namedtuple

from core import platform

Win = namedtuple("Win", "pid title app_id width height minimized active")

BUS_PATH = "/io/github/AdrienPiechocki/Ludodex/Overlay/Windows"
BUS_IFACE = "io.github.AdrienPiechocki.Ludodex.Overlay.Windows"
KWIN_SCRIPT = "ludodex-overlay-windows"


# ---------------------------------------------------------------- calcul pur
def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def parse_cyberrealm(text):
    """Sortie de `cyberrealm-exec windows` (tableau JSON) -> [Win]."""
    return [Win(_int(w.get("pid")), str(w.get("title") or ""), str(w.get("app_id") or ""),
                _int(w.get("width")), _int(w.get("height")), bool(w.get("hidden")), False)
            for w in _array(text)]


def parse_hyprland(text):
    """Sortie de `hyprctl clients -j` -> [Win]. Hyprland n'a pas de « réduire » : une fenêtre
    envoyée dans un workspace spécial (scratchpad) ou cachée est traitée comme réduite."""
    out = []
    for w in _array(text):
        if not w.get("mapped", True):
            continue
        size = w.get("size") or [0, 0]
        ws = (w.get("workspace") or {}).get("name") or ""
        out.append(Win(_int(w.get("pid")), str(w.get("title") or ""), str(w.get("class") or ""),
                       _int(size[0]), _int(size[1]),
                       bool(w.get("hidden")) or ws.startswith("special"),
                       w.get("focusHistoryID") == 0))     # 0 = la fenêtre qui a le focus
    return out


def parse_kwin(text):
    """Tableau JSON poussé par le script KWin -> [Win]."""
    return [Win(_int(w.get("pid")), str(w.get("title") or ""), str(w.get("app_id") or ""),
                _int(w.get("width")), _int(w.get("height")), bool(w.get("minimized")),
                bool(w.get("active"))) for w in _array(text)]


def _array(text):
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return []
    return [w for w in data if isinstance(w, dict)] if isinstance(data, list) else []


def descendants(root, children):
    """`root` et tous ses descendants, d'après la table {pid: [enfants]}."""
    seen, todo = set(), [root]
    while todo:
        p = todo.pop()
        if p in seen:
            continue
        seen.add(p)
        todo.extend(children.get(p, ()))
    return seen


def process_children():
    """{ppid: [pid]} lu dans /proc (vide si illisible)."""
    out = {}
    try:
        entries = [e for e in os.listdir("/proc") if e.isdigit()]
    except OSError:
        return out
    for e in entries:
        try:
            stat = pathlib.Path(f"/proc/{e}/stat").read_text()
            ppid = int(stat.rsplit(")", 1)[1].split()[1])   # après « (comm) » : état, ppid, …
        except (OSError, ValueError, IndexError):
            continue
        out.setdefault(ppid, []).append(int(e))
    return out


def _norm(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


_STARTS = {}     # pid -> epoch de démarrage (ne change jamais pour un pid donné)


def _start_epoch(stat_text, uptime_text, now):
    """Epoch de démarrage d'après le texte de /proc/<pid>/stat et de /proc/uptime."""
    ticks = int(stat_text.rsplit(")", 1)[1].split()[19])               # starttime, en ticks depuis le boot
    boot = now - float(uptime_text.split()[0])
    return boot + ticks / os.sysconf("SC_CLK_TCK")


def process_started(pid):
    """Date de démarrage (epoch, s) du processus `pid`, ou None.

    Dans le bac à sable Flatpak, /proc ne voit pas les processus de l'HÔTE — or les PID des fenêtres
    sont ceux de l'hôte : la lecture passe alors par `flatpak-spawn --host`. Résultat mis en cache."""
    if pid in _STARTS:
        return _STARTS[pid]
    try:
        if platform.in_flatpak():
            import subprocess
            r = subprocess.run(platform.host_argv(["sh", "-c", 'cat /proc/uptime /proc/"$1"/stat', "sh", str(pid)]),
                               capture_output=True, text=True, timeout=3)
            if r.returncode != 0:
                return None
            uptime, _, stat = r.stdout.partition("\n")
            start = _start_epoch(stat, uptime, time.time())
        else:
            start = _start_epoch(pathlib.Path(f"/proc/{pid}/stat").read_text(),
                                 pathlib.Path("/proc/uptime").read_text(), time.time())
    except (OSError, ValueError, IndexError, Exception):
        return None
    _STARTS[pid] = start
    return start


def pick_window(wins, pids, name, since=None, started=process_started):
    """La fenêtre du jeu parmi `wins`, ou None.

    1. PID parmi `pids` (le plus sûr) ; plusieurs : la plus grande (une fenêtre de jeu est
       presque toujours la plus grande de son processus : le lanceur, les dialogues sont plus petits) ;
    2. sinon le nom du jeu (≥ 4 caractères) contenu dans le titre ou l'app_id ;
    3. sinon, la plus grande fenêtre dont le processus est né APRÈS le début de la session : une
       fenêtre qui n'existait pas avant le lancement n'est pas Ludodex, ni le terminal, ni le bureau.
       Rattrape les jeux dont le titre n'est pas le nom de la bibliothèque (OpenMW pour Morrowind)
       et les lanceurs qui se détachent (Proton, AppImage, Steam)."""
    by_pid = [w for w in wins if w.pid and w.pid in pids]
    if by_pid:
        return max(by_pid, key=lambda w: (w.active, w.width * w.height))
    n = _norm(name)
    if len(n) >= 4:
        named = [w for w in wins if n in _norm(w.title) or n in _norm(w.app_id)]
        if named:
            return max(named, key=lambda w: (w.active, w.width * w.height))
    if since:
        fresh = []
        for w in wins:
            born = started(w.pid) if w.pid else None
            if born is not None and born >= since - 3:
                fresh.append(w)
        if fresh:
            return max(fresh, key=lambda w: (w.active, w.width * w.height))
    return None


# ---------------------------------------------------------------- environnement
def detect(env=None):
    """« cyberrealm » | « hyprland » | « kwin » | None, d'après l'environnement."""
    env = os.environ if env is None else env
    if env.get("LUDODEX_OVERLAY_TRACKER") in ("cyberrealm", "hyprland", "kwin"):   # essais à la main
        return env["LUDODEX_OVERLAY_TRACKER"]
    if os.path.basename(env.get("WAYLAND_DISPLAY", "")).startswith("cyberrealm"):
        return "cyberrealm"
    if env.get("HYPRLAND_INSTANCE_SIGNATURE"):
        return "hyprland"
    if "KDE" in env.get("XDG_CURRENT_DESKTOP", "").upper().split(":"):
        return "kwin"
    return None


def _cyberrealm_exec():
    p = shutil.which("cyberrealm-exec")
    if p:
        return p
    home = pathlib.Path.home() / ".local" / "bin" / "cyberrealm-exec"
    return str(home) if os.access(home, os.X_OK) else None


# ---------------------------------------------------------------- suivi (GLib)
class GameWindow:
    """Suit la fenêtre du jeu et appelle `on_change(Win | None)` à chaque changement de
    taille ou d'état. `get_pids()` : PIDs du jeu (mis en cache par l'appelant)."""

    def __init__(self, get_pids, name, on_change, get_since=lambda: None):
        self.get_pids, self.name, self.on_change, self.get_since = get_pids, name, on_change, get_since
        self.backend = None
        self.last = False          # ≠ de tout résultat possible : le premier est toujours notifié
        self._busy = False
        self._kwin_loaded = False

    def start(self, app, kind=None):
        """Démarre le suivi ; `app` : Gio.Application (connexion D-Bus pour KWin)."""
        from gi.repository import GLib
        self.kind = detect() if kind is None else kind
        if self.kind in ("cyberrealm", "hyprland"):
            exe = _cyberrealm_exec() if self.kind == "cyberrealm" else shutil.which("hyprctl")
            if exe is None and not platform.in_flatpak():
                self.kind = None
                return False
            self._argv = [exe or self.kind, *(["windows"] if self.kind == "cyberrealm" else ["clients", "-j"])]
            self._parse = parse_cyberrealm if self.kind == "cyberrealm" else parse_hyprland
            GLib.timeout_add(1500, self._poll)
            return True
        if self.kind == "kwin":
            return self._start_kwin(app)
        return False

    def feed(self, wins):
        pick = pick_window(wins, self.get_pids(), self.name, self.get_since())
        key = None if pick is None else (pick.width, pick.height, pick.minimized)
        if key != self.last:
            self.last = key
            self.on_change(pick)

    # -- sondage (CyberRealm, Hyprland)
    def _poll(self):
        from gi.repository import Gio
        if not self._busy:
            self._busy = True
            try:
                proc = Gio.Subprocess.new(platform.host_argv(self._argv),
                                          Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE)
                proc.communicate_utf8_async(None, None, self._polled)
            except Exception:
                self._busy = False
        return True

    def _polled(self, proc, res):
        self._busy = False
        try:
            ok, out, _err = proc.communicate_utf8_finish(res)
        except Exception:
            return
        if ok and proc.get_successful():
            self.feed(self._parse(out))

    # -- KWin : le script pousse l'état
    def _start_kwin(self, app):
        from gi.repository import Gio, GLib
        conn = app.get_dbus_connection()
        if conn is None:
            return False
        node = Gio.DBusNodeInfo.new_for_xml(
            f'<node><interface name="{BUS_IFACE}"><method name="Update">'
            '<arg type="s" name="json" direction="in"/></method></interface></node>')

        def call(_c, _sender, _path, _iface, method, params, inv):
            if method == "Update":
                self.feed(parse_kwin(params.unpack()[0]))
            inv.return_value(None)
        conn.register_object(BUS_PATH, node.interfaces[0], call, None, None)
        js = pathlib.Path(__file__).with_name("kwin_windows.js")
        # KWin est sur l'hôte : le script doit être lisible PAR LUI. Dans le bac à sable
        # Flatpak, $XDG_RUNTIME_DIR/app/<id> est le même dossier des deux côtés.
        run = pathlib.Path(GLib.get_user_runtime_dir())
        if platform.in_flatpak():
            run = run / "app" / os.environ["FLATPAK_ID"]
        try:
            dst = run / f"{KWIN_SCRIPT}.js"
            dst.write_text(js.read_text(encoding="utf-8"), encoding="utf-8")
        except OSError:
            return False
        self._kwin_path = str(dst)
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self._bus = bus

        def loaded(b, res):
            try:
                sid = b.call_finish(res).unpack()[0]
            except GLib.Error:
                return       # pas de KWin joignable : on reste à l'échelle 1
            self._kwin_loaded = True
            b.call("org.kde.KWin", f"/Scripting/Script{sid}", "org.kde.kwin.Script", "run",
                   None, None, Gio.DBusCallFlags.NONE, -1, None, None, None)
        # un éventuel script d'une exécution précédente est d'abord déchargé
        bus.call("org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting", "unloadScript",
                 GLib.Variant("(s)", (KWIN_SCRIPT,)), None, Gio.DBusCallFlags.NONE, -1, None,
                 lambda b, r: b.call("org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting", "loadScript",
                                     GLib.Variant("(ss)", (self._kwin_path, KWIN_SCRIPT)), None,
                                     Gio.DBusCallFlags.NONE, -1, None, loaded))
        return True

    def stop(self):
        from gi.repository import Gio, GLib
        if self._kwin_loaded:
            try:
                self._bus.call_sync("org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting", "unloadScript",
                                    GLib.Variant("(s)", (KWIN_SCRIPT,)), None, Gio.DBusCallFlags.NONE, 1000, None)
            except GLib.Error:
                pass
            self._kwin_loaded = False
