#!/usr/bin/env python3
"""launch.py — construction et exécution des commandes de lancement, d'installation et
de désinstallation."""
import json, os, pathlib, re, shutil, subprocess, threading, time
import urllib.error, urllib.request
from urllib.parse import quote

from i18n import tr
from . import platform
from .db import LUTRIS_DBS, _ro
from .platform import uri_command, split_command, join_command
from .steam import steam_appid_of


def default_command(g):
    src, ext = g["source"], g["ext_id"]
    if src == "steam":
        return uri_command(f"steam://rungameid/{ext}"), None
    if src == "flatpak":
        return ["flatpak", "run", ext], None
    if src == "lutris":
        kind, gid = ext.split(":", 1)
        uri = f"lutris:rungameid/{gid}"
        return (["lutris", uri] if kind == "native" else ["flatpak", "run", "net.lutris.Lutris", uri]), None
    if src == "retroarch":
        d = json.loads(ext)
        base = ["flatpak", "run", "org.libretro.RetroArch"] if d["f"] else ["retroarch"]
        return base + ["-L", d["core"], d["content"]], None
    if src == "itch":
        return uri_command(f"itch://caves/{ext}/launch"), None
    if src == "heroic":
        return uri_command(heroic_uri(ext)), None
    if ext.startswith("custom:"):
        raise ValueError(tr("missing command"))
    return parse_command(ext)


ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def split_env(argv):
    """Sépare les 'NAME=value' placés en tête (comme un shell) -> (env, argv restant)."""
    env, i = {}, 0
    while i < len(argv) and ENV_ASSIGN.match(argv[i]):
        k, _, v = argv[i].partition("=")
        env[k] = v
        i += 1
    return env, argv[i:]


def heroic_uri(ext):
    """URI de lancement (donc d'installation) d'un jeu chez Heroic.

    Son protocole n'accepte que `ping` et `launch` — mais `launch` d'un jeu NON
    installé ouvre la fenêtre « Is Not Installed, do you wish to Install it? » de
    Heroic et s'occupe du téléchargement. La forme en requête est la seule non
    ambiguë : le nom lu par le chemin (`pathname.split('/')`) ne serait pas décoder,
    lui, tandis que `searchParams.get` l'est — et sans runner, Heroic cherche le jeu
    chez tous les siens (legendary, gog, nile).
    """
    return f"heroic://launch?appName={quote(str(ext), safe='')}"


def parse_command(text):
    """Chemin brut (même avec des espaces) ou commande complète -> (argv, dossier de travail)."""
    text = text.strip()
    path = pathlib.Path(os.path.expanduser(text))
    if text and path.is_file():
        return [str(path)], str(path.parent)
    try:
        argv = [os.path.expanduser(a) for a in split_command(text)]
    except ValueError:
        raise ValueError(tr("unclosed quotes")) from None
    if not argv:
        raise ValueError(tr("empty command"))
    _, prog = split_env(argv)  # le dossier de travail se déduit du programme, pas des variables
    first = pathlib.Path(prog[0]) if prog else pathlib.Path(argv[0])
    return argv, (str(first.parent) if first.is_file() else None)


def launch_command(g):
    """Commande personnalisée du jeu si elle existe, sinon celle de sa source."""
    ov = g["launch_override"] if "launch_override" in g.keys() else ""
    return parse_command(ov) if ov else default_command(g)


def launch_text(g, use_override=True):
    return join_command((launch_command if use_override else default_command)(g)[0])


def check_command(text):
    """None si la commande semble exister, sinon son premier mot (ValueError si elle est mal formée)."""
    argv, _ = parse_command(text)
    _, prog = split_env(argv)
    if not prog:
        raise ValueError(tr("empty command"))
    return None if (os.path.isfile(prog[0]) or shutil.which(prog[0])) else prog[0]


def set_launch_override(con, gid, text):
    """text vide = retour à la commande par défaut."""
    con.execute("UPDATE games SET launch_override=? WHERE id=?", (text.strip(), gid))
    con.commit()


PROGRESS_MIN_MINUTES = 5  # une session doit durer ce temps-là pour qu'un jeu passe en 'en_cours'
EXIT_GRACE_SEC = 5        # se terminer avant ce délai, ce n'était pas le jeu : un passe-plat
                          # (`xdg-open steam://…`) rend la main en quelques dizaines de ms


def close_open_sessions(con, now=None):
    """Clôt les sessions encore ouvertes (ended=0) et enregistre leur durée.

    C'est ICI que se décide le statut, et non plus au lancement : c'est le seul
    moment où la durée est connue. Une session d'au moins PROGRESS_MIN_MINUTES
    fait passer le jeu en « en cours » — un aperçu de trente secondes, lui, ne
    doit pas le faire sauter de « To play » au premier rang de la bibliothèque.
    Un statut choisi à la main (« Terminé », « Abandonné ») n'est jamais écrasé,
    et un jeu déjà en cours n'est jamais rétrogradé."""
    now = int(time.time()) if now is None else now
    rows = con.execute("SELECT id, game_id, started FROM sessions WHERE ended=0").fetchall()
    for r in rows:
        minutes = max(0, (now - r["started"]) // 60)
        con.execute("UPDATE sessions SET ended=?, minutes=? WHERE id=?", (now, minutes, r["id"]))
        if minutes >= PROGRESS_MIN_MINUTES:
            con.execute("UPDATE games SET status='en_cours' "
                        "WHERE id=? AND status IN ('backlog','pause')", (r["game_id"],))
    con.commit()
    return len(rows)


def close_session(con, sid, now=None):
    """Clôt UNE session à sa vraie fin — le jeu s'est terminé (`EXIT_GRACE_SEC`).

    La règle de statut est celle de `close_open_sessions` ; sans effet si la
    session est déjà fermée (l'application a pu la clôturer avant, en se fermant,
    ou un lancement suivant l'a fait)."""
    now = int(time.time()) if now is None else now
    row = con.execute("SELECT game_id, started FROM sessions WHERE id=? AND ended=0", (sid,)).fetchone()
    if not row:
        return False
    minutes = max(0, (now - row["started"]) // 60)
    con.execute("UPDATE sessions SET ended=?, minutes=? WHERE id=?", (now, minutes, sid))
    if minutes >= PROGRESS_MIN_MINUTES:
        con.execute("UPDATE games SET status='en_cours' "
                    "WHERE id=? AND status IN ('backlog','pause')", (row["game_id"],))
    con.commit()
    return True


def _watch_game(proc, sid, on_exit, debut):
    """Attend la fin du processus de jeu, puis referme sa session — par le rappel
    fourni par l'APPLICATION (le fil principal doit faire ça : un autre fil ne
    touche jamais à sa connexion). Une fin dans la grâce n'était pas le jeu."""
    proc.wait()
    if time.monotonic() - debut >= EXIT_GRACE_SEC:
        on_exit(sid)


def launch(con, g, on_exit=None):
    """Lance le jeu et ouvre sa session. Avec `on_exit`, un fil d'attente referme
    cette session à la vraie sortie du jeu — sans quoi la fin n'était que le
    prochain lancement ou la fermeture de l'application, et la frise affichait
    (« 13h14 → 13h25 ») une partie qui en avait coûté deux."""
    try:
        cmd, cwd = launch_command(g)
        env, argv = split_env(cmd)
        if not argv:
            raise ValueError(tr("empty command"))
        proc = subprocess.Popen(argv, cwd=cwd, env={**os.environ, **env} if env else None,
                                start_new_session=True)
    except (OSError, ValueError) as e:
        print(tr("! couldn't launch: {e}", e=e))
        return False
    now = int(time.time())
    close_open_sessions(con, now=now)  # lancer un jeu clôt la session précédente
    con.execute("UPDATE games SET last_played=? WHERE id=?", (now, g["id"]))
    cur = con.execute("INSERT OR IGNORE INTO sessions (game_id, started, ended, minutes) VALUES (?, ?, 0, 0)",
                      (g["id"], now))
    con.commit()
    if on_exit is not None and cur.rowcount:  # INSERT ignoré = rien d'ouvert à suivre
        threading.Thread(target=_watch_game,
                         args=(proc, cur.lastrowid, on_exit, time.monotonic()), daemon=True).start()
    print(f"▶ {g['name']}.")
    return True


def lutris_install_uri(ext):
    """`lutris:install/{slug}` du jeu, ou None.

    Lutris n'installe pas à partir d'un id numérique : `lutris:rungameid/{id}` ne fait
    que lancer, et seul son help annonce `lutris:install/game-identifier`. Le slug
    n'est pas dans notre base — il est relu dans `pga.db`, la même que l'import ouvre
    déjà (c'est ainsi que « native:10 » se résout en « hedgewars »)."""
    try:
        kind, gid = str(ext).split(":", 1)
        gid = int(gid)
    except ValueError:  # pas de deux-points, ou id non numérique
        return None
    for k, path in LUTRIS_DBS:
        if k != kind or not path.exists():
            continue
        src = _ro(path)
        try:
            row = src.execute("SELECT slug FROM games WHERE id=?", (gid,)).fetchone()
        finally:
            src.close()
        if row is not None and row["slug"]:
            return f"lutris:install/{row['slug']}"
    return None


LUTRIS_CATALOGUE = "https://lutris.net/api/installers/"


def lutris_installer_count(slug, timeout=4):
    """Nombre d'installateurs que lutris.net range derrière `slug`, ou None si on ne
    sait pas.

    Lutris se tait quand ce compte est nul : `get_installers()` renvoie `[]`, aucune
    fenêtre d'installation n'apparaît et l'application sort en 0 sans un mot (constaté
    sur `lutris lutris:install/hedgewars`, dont le catalogue ne compte rien). Seule une
    certitude — la réponse JSON du catalogue — bloque ; tout le reste, réseau mort ou
    page illisible, laisse passer : Lutris restant juge de sa maison."""
    try:
        with urllib.request.urlopen(LUTRIS_CATALOGUE + quote(slug), timeout=timeout) as r:
            return int(json.load(r)["count"])
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError, TypeError):
        return None


def install_command(g):
    """Commande installant le jeu, ou None : sa source ne sait pas installer.

    LE point de décision : l'interface n'affiche « Installer » que si cette fonction
    renvoie un argv, donc jamais de bouton muet. Steam passe par son appid (`steam_appid_of`,
    seule source de la règle « c'est un appid »), Flatpak par une vraie commande —
    d'où un argv rendu plutôt qu'une URI — et Lutris par le slug relu plus haut.
    Une ROM ne se « télécharge » pas."""
    try:
        src, ext = g["source"], g["ext_id"]
    except (IndexError, KeyError, TypeError):
        return None
    if src == "steam":
        appid = steam_appid_of(g)
        return uri_command(f"steam://install/{appid}") if appid else None
    if src == "flatpak":
        return ["flatpak", "install", "-y", str(ext)]
    if src == "heroic":
        return uri_command(heroic_uri(ext))
    if src == "lutris":
        uri = lutris_install_uri(ext)
        if uri is None:
            return None
        # Lutris est lui-même un flatpak : le chemin est celui du lancement.
        return (["flatpak", "run", "net.lutris.Lutris", uri] if str(ext).split(":", 1)[0] == "flatpak"
                else uri_command(uri))
    return None


def _detached(cmd):
    """Lance `cmd` hors de la session de la fenêtre et sans capter sa sortie : un
    téléchargement ou une désinstallation dure bien plus que l'application, qui ne doit
    ni s'arrêter avec elle ni se bloquer dessus. Renvoie l'erreur éventuelle, ou None.

    Les 'NAME=value' de tête deviennent de vraies variables d'environnement : Popen ne
    passe pas par un shell, sans quoi le binaire recevrait ces paires en ARGUMENTS."""
    env, argv = split_env(cmd)
    try:
        subprocess.Popen(argv, env={**os.environ, **env} if env else None,
                         start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return None
    except OSError as e:
        return e


def install(g):
    """Lance l'installation chez la source du jeu. False s'il n'est pas installable.

    Contrairement à `launch`, rien n'est enregistré : le jeu n'a pas démarré, une
    session serait fausse. Ludodex apprend l'installation à la prochaine importation,
    quand la source a écrit ses fichiers.

    Lutris est la seule source qu'on INTERROGE avant d'ouvrir : sans installateur au
    slug, il ne montre rien et sort en 0 (`lutris_installer_count`), et le «⬇» de
    complaisance ferait croire à une fenêtre jamais ouverte. La question ne coûte que
    quelques centaines de millisecondes, au clic — jamais au dressage d'un menu."""
    cmd = install_command(g)
    if cmd is None:
        return False
    if g["source"] == "lutris":
        uri = lutris_install_uri(g["ext_id"])
        if uri and lutris_installer_count(uri.rsplit("/", 1)[-1]) == 0:
            print(tr("! no Lutris installer for this game"))
            return False
    e = _detached(cmd)
    if e is not None:
        print(tr("! couldn't open the installation: {e}", e=e))
        return False
    print(f"⬇ {g['name']}.")
    return True


# Ce que le play d'une collection ouvre pour sa source : son CLIENT par le protocole
# qu'il a déclaré lui-même (gestionnaire de schéma, Flatpak compris) — Steam ouvre sa
# fenêtre par `steam://nav/games` (« Opens a Steam window » — Steam browser protocol) ;
# Flatpak, qui n'a pas de client, ouvre le catalogue flathub.org dans le navigateur.
LAUNCHER_URIS = {"steam": "steam://nav/games", "itch": "itch://",
                 "heroic": "heroic://", "flatpak": "https://flathub.org"}


def launcher_command(source):
    """Commande ouvrant le launcher de `source` (son client lui-même, pas un jeu),
    ou None : cette source n'a rien à ouvrir — Imported est une commande arbitraire
    de l'utilisateur. L'interface n'affiche le bouton que si ce rendu est non vide,
    donc jamais de bouton muet."""
    uri = LAUNCHER_URIS.get(source)
    if uri:
        return uri_command(uri)
    if source == "lutris":  # son protocole est rejeté (`lutris lutris://` sort en 1) : son binaire
        exe = shutil.which("lutris")
        return [exe] if exe else ["flatpak", "run", "net.lutris.Lutris"]
    if source == "retroarch":  # aucun protocole : son exécutable, PATH puis flatpak
        exe = shutil.which("retroarch")
        return [exe] if exe else ["flatpak", "run", "org.libretro.RetroArch"]
    return None


def open_launcher(source):
    """Ouvre le launcher de la plateforme de `source`. False si elle n'en a pas ou si
    le spawn casse. Comme `install`, on ne peut pas savoir si le gestionnaire d'URI a
    réellement trouvé le client — seule la sortie du processus le dirait, et l'attendre
    bloquerait l'interface (xdg-open peut suivre le client jusqu'à la fermer)."""
    cmd = launcher_command(source)
    if cmd is None:
        return False
    e = _detached(cmd)
    if e is not None:
        print(tr("! couldn't open the launcher: {e}", e=e))
        return False
    return True


_LEG_CACHE = {}


def legendary_install_dir(ext):
    """Dossier de config de Legendary où `ext` figure comme INSTALLÉ, ou None.

    Deux pièges que cette recherche écarte :
    - Heroic garde sa config À PART de celle de Legendary autonome — sans
      `LEGENDARY_CONFIG_PATH`, le binaire ne verrait aucun des jeux de Heroic ;
    - un jeu GOG n'est pas dans ces fichiers (c'est `gogdl` qui l'a installé, et ce
      binaire n'a aucune commande `uninstall`) : `legendary uninstall 999` ne
      retirerait rien, si ce n'est valider un bouton muet.

    Cache à la volée, indexé sur mtime+taille : ces fichiers ne bougent que pendant
    une installation, et la fonction est appelée pour chaque carte affichée."""
    ext = str(ext)
    from .importers import parse_legendary  # import local : évite un cycle de chargement
    for d in platform.legendary_dirs():
        f = d / "installed.json"
        try:
            st = os.stat(f)
        except OSError:
            continue
        key = (str(f), st.st_mtime_ns, st.st_size)
        if key not in _LEG_CACHE:
            try:
                data = json.loads(f.read_text(encoding="utf-8", errors="ignore"))
            except (OSError, ValueError):
                data = []
            _LEG_CACHE[key] = frozenset(g["ext_id"] for g in parse_legendary(data))
        if ext in _LEG_CACHE[key]:
            return d
    return None


# Ce que la fenêtre « Désinstaller » de Lutris exécute chez elle — seule voie de
# retrait : ni `lutris:uninstall/` (absent de son parseur), ni option de CLI. Trois
# relevés de sa source, sans quoi rien ne fonctionne :
# 1. le bootstrap GTK3 de son point d'entrée : sans lui, `game.py` importe Gtk sans
#    version, charge Gdk 4.0 par défaut et saute sur `require_version("Gdk", "3.0")` ;
# 2. une boucle GLib : son corbeillage est une chaîne de callbacks DBus, et un
#    processus qui se termine meurt avant même que le message ne parte ;
# 3. un achèvement : ce portail (`org.freedesktop.portal.Trash`) est REFUSÉ sur
#    nombreux bureaux (« could not be moved to the trash — you will need to delete it
#    yourself », constaté ici), auquel cas Lutris laisse les fichiers sur le disque.
#    Notre contrat à l'écran dit « Ses fichiers quittent votre ordinateur » : le
#    dossier qui tient encore après la pompe part pour de bon, par la garde de Lutris
#    lui-même (`delete_folder` refuse `~`).
LUTRIS_UNINSTALL = (
    "import os, time, gi\n"
    "gi.require_version('Gtk', '3.0')\n"
    "gi.require_version('Gdk', '3.0')\n"
    "from lutris.game import Game\n"
    "from lutris.util.system import delete_folder\n"
    "from gi.repository import GLib\n"
    "g = Game({gid})\n"
    "d = g.directory\n"
    "g.uninstall(delete_files=True)\n"
    "ctx = GLib.MainContext.default()\n"
    "for _ in range(500):\n"
    "    while ctx.pending():\n"
    "        ctx.iteration(False)\n"
    "    time.sleep(0.01)\n"
    "if d and os.path.isdir(d):\n"
    "    delete_folder(d)\n"
)


def uninstall_command(g):
    """Commande désinstallant le jeu, ou None : sa source ne sait pas désinstaller.

    Le miroir exact de `install_command` — même rôle, même mécanisme — mais la liste des
    sources est plus courte, parce qu'un retrait se prouve moins bien qu'une URI
    d'installation :

    - **Steam** — `steam://uninstall/{appid}`, listé par Valve parmi ses commandes
      implémentées, et le client réclame sa propre confirmation (« uninstall … from
      this device? ») : jamais de retrait silencieux. L'appid vient de `steam_appid_of`.
    - **Flatpak** — `flatpak uninstall -y {id}`, qui ouvre la place mais laisse les
      données `~/.var/app` en place.
    - **Lutris** — par sa propre porte : ni `lutris:uninstall/` (absent de son parseur,
      lu dans sa source) ni option de CLI (`--help` complet) ; seule sa méthode Python
      `Game.uninstall` sait retirer les fichiers et le marqueur `installed`, rappelée
      dans un python qui l'importe (voir `LUTRIS_UNINSTALL`).

    RetroArch est écarté parce que `ext_id` est le chemin d'une ROM : ce fichier
    appartient à l'utilisateur, pas à Ludodex. itch n'a chez nous aucune commande
    de retrait documentée.
    Heroic passe par `legendary uninstall`, à condition que le binaire soit là et
    que ce soit LUI qui ait installé le jeu (voir `legendary_install_dir`)."""
    try:
        src, ext = g["source"], g["ext_id"]
    except (IndexError, KeyError, TypeError):
        return None
    if src == "steam":
        appid = steam_appid_of(g)
        return uri_command(f"steam://uninstall/{appid}") if appid else None
    if src == "flatpak":
        return ["flatpak", "uninstall", "-y", str(ext)]
    if src == "lutris":
        try:
            kind, gid = str(ext).split(":", 1)
            gid = int(gid)
        except ValueError:  # pas de deux-points, ou id non numérique
            return None
        script = LUTRIS_UNINSTALL.format(gid=gid)
        if kind == "native":  # le python qui importe Lutris, pas celui de Ludodex
            py = shutil.which("python3")
            return [py, "-c", script] if py and shutil.which("lutris") else None
        return (["flatpak", "run", "--command=python3", "net.lutris.Lutris", "-c", script]
                if shutil.which("flatpak") else None)
    if src == "heroic":
        d = legendary_install_dir(ext)
        if d is None or shutil.which("legendary") is None:
            return None
        return [f"LEGENDARY_CONFIG_PATH={d}", "legendary", "uninstall", str(ext)]
    return None


def uninstall(g):
    """Désinstalle le jeu chez sa source. False si sa source ne sait pas.

    Comme `install`, rien n'est écrit en base : Steam affiche sa propre confirmation et
    on ne peut pas savoir si elle sera validée — c'est l'importation suivante, quand le
    fichier `appmanifest` aura disparu, qui bascule la fiche. Une désinstallation
    anticipée mentirait jusqu'alors sur un jeu encore présent sur le disque."""
    cmd = uninstall_command(g)
    if cmd is None:
        return False
    e = _detached(cmd)
    if e is not None:
        print(tr("! couldn't open the uninstall: {e}", e=e))
        return False
    print(f"⌫ {g['name']}.")
    return True
