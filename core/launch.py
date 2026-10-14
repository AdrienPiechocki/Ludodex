#!/usr/bin/env python3
"""launch.py — construction et exécution des commandes de lancement, d'installation et
de désinstallation."""
import json, os, pathlib, re, shutil, subprocess, time

from i18n import tr
from .db import LUTRIS_DBS, _ro
from .platform import uri_command, split_command, join_command, popen_kwargs, retroarch_exe
from .steam import steam_appid_of


def default_command(g, osname=None):
    src, ext = g["source"], g["ext_id"]
    if src == "steam":
        return uri_command(f"steam://rungameid/{ext}", osname), None
    if src == "flatpak":
        return ["flatpak", "run", ext], None
    if src == "lutris":
        kind, gid = ext.split(":", 1)
        uri = f"lutris:rungameid/{gid}"
        return (["lutris", uri] if kind == "native" else ["flatpak", "run", "net.lutris.Lutris", uri]), None
    if src == "retroarch":
        d = json.loads(ext)
        # sous Windows, rétroarch.exe est cherché dans les dossiers d'installation
        base = (["flatpak", "run", "org.libretro.RetroArch"] if d["f"]
                else [retroarch_exe(osname) or "retroarch"])
        return base + ["-L", d["core"], d["content"]], None
    if src == "itch":
        return uri_command(f"itch://caves/{ext}/launch", osname), None
    if src == "heroic":
        return uri_command(f"heroic://launch/{ext}", osname), None
    if src == "epic":
        return uri_command(f"com.epicgames.launcher://apps/{ext}?action=launch", osname), None
    if ext.startswith("custom:") or src == "gog":
        raise ValueError(tr("missing command"))
    return parse_command(ext, osname)


ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def split_env(argv):
    """Sépare les 'NAME=value' placés en tête (comme un shell) -> (env, argv restant)."""
    env, i = {}, 0
    while i < len(argv) and ENV_ASSIGN.match(argv[i]):
        k, _, v = argv[i].partition("=")
        env[k] = v
        i += 1
    return env, argv[i:]


def parse_command(text, osname=None):
    """Chemin brut (même avec des espaces) ou commande complète -> (argv, dossier de travail)."""
    text = text.strip()
    path = pathlib.Path(os.path.expanduser(text))
    if text and path.is_file():
        return [str(path)], str(path.parent)
    try:
        argv = [os.path.expanduser(a) for a in split_command(text, osname)]
    except ValueError:
        raise ValueError(tr("unclosed quotes")) from None
    if not argv:
        raise ValueError(tr("empty command"))
    _, prog = split_env(argv)  # le dossier de travail se déduit du programme, pas des variables
    first = pathlib.Path(prog[0]) if prog else pathlib.Path(argv[0])
    return argv, (str(first.parent) if first.is_file() else None)


def launch_command(g, osname=None):
    """Commande personnalisée du jeu si elle existe, sinon celle de sa source."""
    ov = g["launch_override"] if "launch_override" in g.keys() else ""
    return parse_command(ov, osname) if ov else default_command(g, osname)


def launch_text(g, use_override=True, osname=None):
    return join_command((launch_command if use_override else default_command)(g, osname)[0], osname)


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


def launch(con, g):
    try:
        cmd, cwd = launch_command(g)
        env, cmd = split_env(cmd)
        if not cmd:
            raise ValueError(tr("empty command"))
        subprocess.Popen(cmd, cwd=cwd, env={**os.environ, **env} if env else None, **popen_kwargs())
    except (OSError, ValueError) as e:
        print(tr("! couldn't launch: {e}", e=e))
        return False
    now = int(time.time())
    close_open_sessions(con, now=now)  # lancer un jeu clôt la session précédente
    con.execute("UPDATE games SET last_played=? WHERE id=?", (now, g["id"]))
    con.execute("INSERT OR IGNORE INTO sessions (game_id, started, ended, minutes) VALUES (?, ?, 0, 0)", (g["id"], now))
    con.commit()
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


def install_command(g, osname=None):
    """Commande installant le jeu, ou None : sa source ne sait pas installer.

    LE point de décision : l'interface n'affiche « Installer » que si cette fonction
    renvoie un argv, donc jamais de bouton muet. Steam passe par son appid (`steam_appid_of`,
    seule source de la règle « c'est un appid »), Flatpak par une vraie commande —
    d'où un argv rendu plutôt qu'une URI — et Lutris par le slug relu plus haut.
    GOG n'a aucune commande de lancement, une ROM ne se « télécharge » pas, et
    Epic/Heroic/itch n'ont rien de documenté de façon sûre."""
    try:
        src, ext = g["source"], g["ext_id"]
    except (IndexError, KeyError, TypeError):
        return None
    if src == "steam":
        appid = steam_appid_of(g)
        return uri_command(f"steam://install/{appid}", osname) if appid else None
    if src == "flatpak":
        return ["flatpak", "install", "-y", str(ext)]
    if src == "lutris":
        uri = lutris_install_uri(ext)
        if uri is None:
            return None
        # Lutris est lui-même un flatpak : le chemin est celui du lancement.
        return (["flatpak", "run", "net.lutris.Lutris", uri] if str(ext).split(":", 1)[0] == "flatpak"
                else uri_command(uri, osname))
    return None


def _detached(cmd):
    """Lance `cmd` hors de la session de la fenêtre et sans capter sa sortie : un
    téléchargement ou une désinstallation dure bien plus que l'application, qui ne doit
    ni s'arrêter avec elle ni se bloquer dessus. Renvoie l'erreur éventuelle, ou None."""
    try:
        subprocess.Popen(cmd, start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return None
    except OSError as e:
        return e


def install(g):
    """Lance l'installation chez la source du jeu. False s'il n'est pas installable.

    Contrairement à `launch`, rien n'est enregistré : le jeu n'a pas démarré, une
    session serait fausse. Ludodex apprend l'installation à la prochaine importation,
    quand la source a écrit ses fichiers."""
    cmd = install_command(g)
    if cmd is None:
        return False
    e = _detached(cmd)
    if e is not None:
        print(tr("! couldn't open the installation: {e}", e=e))
        return False
    print(f"⬇ {g['name']}.")
    return True


def uninstall_command(g, osname=None):
    """Commande désinstallant le jeu, ou None : sa source ne sait pas désinstaller.

    Le miroir exact de `install_command` — même rôle, même mécanisme — mais la liste des
    sources est plus courte, parce qu'un retrait se prouve moins bien qu'une URI
    d'installation :

    - **Steam** — `steam://uninstall/{appid}`, listé par Valve parmi ses commandes
      implémentées, et le client réclame sa propre confirmation (« uninstall … from
      this device? ») : jamais de retrait silencieux. L'appid vient de `steam_appid_of`.
    - **Flatpak** — `flatpak uninstall -y {id}`, qui ouvre la place mais laisse les
      données `~/.var/app` en place.

    Lutris est écarté pour l'inverse de la raison qui l'admettait côté installation :
    son parseur (`lutris/gui/application.py`) n'accepte que `rungame`, `rungameid` et
    `install` — le slug existe, l'action non. RetroArch l'est parce que `ext_id` est le
    chemin d'une ROM : ce fichier appartient à l'utilisateur, pas à Ludodex. itch, GOG,
    Epic et Heroic n'ont chez nous ni jeu installé ni commande documentée."""
    try:
        src, ext = g["source"], g["ext_id"]
    except (IndexError, KeyError, TypeError):
        return None
    if src == "steam":
        appid = steam_appid_of(g)
        return uri_command(f"steam://uninstall/{appid}", osname) if appid else None
    if src == "flatpak":
        return ["flatpak", "uninstall", "-y", str(ext)]
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
