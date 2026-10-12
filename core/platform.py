#!/usr/bin/env python3
"""platform.py — seule couche dépendante de l'OS (chemins, lancement, commandes).

Chaque fonction accepte `osname` / `env` / `home` injectés : la branche Windows
se teste donc depuis n'importe quelle machine. Le reste du code ne fait que
consommer ces fonctions, jamais `os.name` directement.
"""
import os, pathlib, shlex, shutil, subprocess

# Nom du dossier de données de l'application ; `LEGACY_DIR_NAME` sert à reprendre
# automatiquement les données de l'ancien nom (Game Picker) une seule fois.
APP_DIR_NAME = "ludodex"
LEGACY_DIR_NAME = "gamepicker"

# Emplacements d'installation habituels de Steam sous Windows (utilisés si le registre ne répond pas).
STEAM_WINDOWS_FALLBACKS = ["C:/Program Files (x86)/Steam", "C:/Program Files/Steam"]


def is_windows(osname=None):
    """Vrai sur Windows (osname injectable pour les tests)."""
    return (osname or os.name) == "nt"


def _env(env):
    return os.environ if env is None else env


def _home(home):
    return pathlib.Path.home() if home is None else pathlib.Path(home)


def clean_path(value):
    """Normalise un chemin textuel venu d'un fichier de config : sous Windows, Steam écrit
    les chemins avec des antislashs (échappés en `\\\\` dans les fichiers VDF). On les
    ramène à des « / », que `pathlib` comprend sur toutes les plateformes."""
    return pathlib.Path(str(value).replace("\\", "/"))


def _data_dir(name, osname=None, env=None, home=None):
    """Dossier de données pour un nom donné, selon l'OS (logique partagée)."""
    env, home = _env(env), _home(home)
    if is_windows(osname):
        base = env.get("LOCALAPPDATA") or env.get("APPDATA")
        return (pathlib.Path(base) if base else home / "AppData" / "Local") / name
    return home / ".local" / "share" / name


def data_dir(osname=None, env=None, home=None):
    """Dossier de données de l'application (base, couvertures, clé API)."""
    return _data_dir(APP_DIR_NAME, osname, env, home)


def migrate_data_dir(osname=None, env=None, home=None):
    """Reprend une seule fois le dossier de données historique (`gamepicker`) vers
    `ludodex`. Sans ancien dossier, ne fait rien ; si `ludodex` existe déjà, on ne
    touche à rien. Renvoie le dossier de données courant (non créé s'il n'y a rien
    à reprendre : l'ouverture de la base s'en charge)."""
    new = data_dir(osname, env, home)
    if new.exists():
        return new
    old = _data_dir(LEGACY_DIR_NAME, osname, env, home)
    if old.exists():
        new.parent.mkdir(parents=True, exist_ok=True)
        try:
            old.rename(new)          # même volume : déplacement atomique
        except OSError:
            shutil.copytree(old, new)  # repli : copie si le renommage échoue
    return new


def _windows_steam_paths():
    """Dossiers d'installation de Steam sous Windows : registre, puis emplacements usuels."""
    out = []
    try:
        import winreg  # présent uniquement sous Windows
    except ImportError:
        winreg = None
    if winreg is not None:
        for hive, key, name in (
            (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
        ):
            try:
                with winreg.OpenKey(hive, key) as k:
                    value, _ = winreg.QueryValueEx(k, name)
                    if value:
                        out.append(str(value))
            except OSError:
                pass
    out.extend(STEAM_WINDOWS_FALLBACKS)
    seen, paths = set(), []
    for p in out:  # dédoublonne en gardant l'ordre (registre d'abord)
        p = p.replace("\\", "/")
        if p not in seen:
            seen.add(p)
            paths.append(pathlib.Path(p))
    return paths


def steam_roots(osname=None, env=None, home=None):
    """Racines « steamapps » de Steam (jeux installés) pour l'OS courant."""
    if is_windows(osname):
        return [p / "steamapps" for p in _windows_steam_paths()]
    home = _home(home)
    return [home / ".local/share/Steam/steamapps", home / ".steam/steam/steamapps"]


def itch_dbs(osname=None, env=None, home=None):
    """Bases de l'application itch.io (jeux installés)."""
    home = _home(home)
    if is_windows(osname):
        appdata = _env(env).get("APPDATA") or home / "AppData" / "Roaming"
        return [pathlib.Path(appdata) / "itch" / "db" / "butler.db"]
    return [home / ".config/itch/db/butler.db",
            home / ".var/app/io.itch.itch/config/itch/db/butler.db"]


def lutris_dbs(osname=None, env=None, home=None):
    """Bases Lutris (native, flatpak). Lutris n'existe pas sous Windows."""
    if is_windows(osname):
        return []
    home = _home(home)
    return [("native", home / ".local/share/lutris/pga.db"),
            ("flatpak", home / ".var/app/net.lutris.Lutris/data/lutris/pga.db")]


def flatpak_dirs(osname=None, env=None, home=None):
    """Dossiers d'exports .desktop de Flatpak. Flatpak n'existe pas sous Windows."""
    if is_windows(osname):
        return []
    home = _home(home)
    return [pathlib.Path("/var/lib/flatpak/exports/share/applications"),
            home / ".local/share/flatpak/exports/share/applications"]


def retro_dirs(osname=None, env=None, home=None):
    """Dossiers RetroArch : (drapeau flatpak, chemin)."""
    home = _home(home)
    if is_windows(osname):
        appdata = _env(env).get("APPDATA") or home / "AppData" / "Roaming"
        return [(0, pathlib.Path(appdata) / "RetroArch")]
    return [(0, home / ".config/retroarch"),
            (1, home / ".var/app/org.libretro.RetroArch/config/retroarch")]


def heroic_dirs(osname=None, env=None, home=None):
    """Dossiers de configuration de Heroic Games Launcher (natif + Flatpak)."""
    home = _home(home)
    if is_windows(osname):
        base = _env(env).get("APPDATA") or home / "AppData" / "Roaming"
        return [pathlib.Path(base) / "heroic"]
    return [home / ".config/heroic",
            home / ".var/app/com.heroicgameslauncher.hgl/config/heroic"]


def legendary_dirs(osname=None, env=None, home=None):
    """Dossiers de config de Legendary (Epic) : ceux embarqués par Heroic + l'installation autonome."""
    home = _home(home)
    dirs = [d / "legendaryConfig" / "legendary" for d in heroic_dirs(osname, env, home)]
    if is_windows(osname):
        base = _env(env).get("APPDATA") or home / "AppData" / "Roaming"
        dirs.append(pathlib.Path(base) / "legendary")
    else:
        dirs.append(home / ".config/legendary")
    return dirs


def epic_programdata(osname=None, env=None, home=None):
    """Dossiers « .../Epic » du lanceur Epic officiel (Windows natif, ou Wine sous Linux)."""
    if is_windows(osname):
        base = _env(env).get("PROGRAMDATA") or "C:/ProgramData"
        return [pathlib.Path(base) / "Epic"]
    return [_home(home) / ".wine/drive_c/ProgramData/Epic"]


def gog_registry_games():
    """Jeux installés vus par GOG Galaxy (registre Windows). Liste vide hors Windows.

    Chaque entrée est un dict des valeurs de la clé (`gameName`, `path`, `exe`,
    `launchCommand`, ...) ; `gameID` est complété par le nom de la sous-clé.
    """
    out = []
    try:
        import winreg  # présent uniquement sous Windows
    except ImportError:
        return out
    for hive, key in ((winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\GOG.com\Games"),
                      (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\GOG.com\Games"),
                      (winreg.HKEY_CURRENT_USER, r"SOFTWARE\GOG.com\Games")):
        try:
            with winreg.OpenKey(hive, key) as k:
                for i in range(winreg.QueryInfoKey(k)[0]):
                    sub = winreg.EnumKey(k, i)
                    with winreg.OpenKey(k, sub) as gk:
                        vals = {}
                        for j in range(winreg.QueryInfoKey(gk)[1]):
                            name, data, _ = winreg.EnumValue(gk, j)
                            vals[name] = data
                    vals.setdefault("gameID", sub)
                    out.append(vals)
        except OSError:
            pass
    return out


def retroarch_exe(osname=None, roots=None):
    """`retroarch.exe` sous Windows (souvent hors PATH) ; None ailleurs, où le PATH suffit.

    Cherche dans les dossiers d'installation habituels. `roots` sert aux tests."""
    if not is_windows(osname):
        return None
    if roots is None:
        roots = [pathlib.Path(p) / "RetroArch"
                 for p in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")) if p]
    for base in roots:
        exe = base / "retroarch.exe"
        if exe.is_file():
            return str(exe)
    return None


def uri_command(uri, osname=None):
    """Commande ouvrant un URI (steam://, itch://). « cmd start » pour l'association Windows."""
    return ["cmd", "/c", "start", "", uri] if is_windows(osname) else ["xdg-open", uri]


def open_url_command(url, osname=None):
    """Commande ouvrant une URL ; None = os.startfile (Windows : évite les « & » avalés par cmd)."""
    return None if is_windows(osname) else ["xdg-open", url]


def open_url(url):
    """Ouvre une URL dans le navigateur du bureau ; False si l'ouverture échoue."""
    try:
        cmd = open_url_command(url)
        if cmd is None:
            os.startfile(url)  # type: ignore[attr-defined]  # Windows uniquement
        else:
            subprocess.Popen(cmd, start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError:
        return False


def split_command(text, osname=None):
    """Découpe une ligne de commande. Sous Windows, mode non-POSIX : les antislashs des
    chemins (C:\\...) sont conservés et les guillemets englobants sont retirés."""
    if is_windows(osname):
        lex = shlex.shlex(text, posix=False)
        lex.whitespace_split = True
        lex.commenters = ""
        return [t[1:-1] if len(t) >= 2 and t[0] == t[-1] == '"' else t for t in lex]
    return shlex.split(text)


def join_command(argv, osname=None):
    """Rassemble une ligne de commande pour l'affichage ; inverse de split_command."""
    return subprocess.list2cmdline(list(argv)) if is_windows(osname) else shlex.join(list(argv))


def popen_kwargs(osname=None):
    """Options Popen selon l'OS : `start_new_session` est réservé à POSIX."""
    if is_windows(osname):
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    return {"start_new_session": True}
