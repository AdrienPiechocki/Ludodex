#!/usr/bin/env python3
"""launch.py — construction et exécution des commandes de lancement, d'installation et
de désinstallation."""
import json, os, pathlib, re, shutil, sqlite3, subprocess, threading, time
import urllib.error, urllib.request
from urllib.parse import quote

from i18n import tr
from . import overlay, platform
from .db import LUTRIS_DBS, ITCH_DBS, _ro, get_setting
from .platform import uri_command, split_command, join_command
from .steam import steam_appid_of, champ_acf


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
    if platform.in_flatpak():
        # Dans le bac à sable, les binaires hôtes ne sont ni dans le PATH ni (sous /usr)
        # visibles : on interroge l'hôte plutôt que de fausser l'alerte.
        try:
            r = subprocess.run(["flatpak-spawn", "--host", "sh", "-c",
                                'command -v "$1" >/dev/null 2>&1', "sh", prog[0]],
                               capture_output=True, timeout=10)
            return None if r.returncode == 0 else prog[0]
        except (OSError, subprocess.SubprocessError):
            return None
    return None if (os.path.isfile(prog[0]) or shutil.which(prog[0])) else prog[0]


def which_bin(nom):
    """True si le binaire `nom` existe — chez l'utilisateur, y depuis le bac à sable.

    `shutil.which` dans le sandbox ne voit que le runtime (`/app/bin:/usr/bin`) :
    ni lutris, ni retroarch, ni legendary, ni flatpak (même absent du runtime) —
    les boutons « ouvrir le lanceur » et « désinstaller » seraient silencieusement
    faux ou muets. Hôte injoignable : False, le repli d'aujourd'hui (bouton absent)
    plutôt qu'un faux positif."""
    if platform.in_flatpak():
        try:
            r = subprocess.run(["flatpak-spawn", "--host", "sh", "-c",
                                'command -v "$1" >/dev/null 2>&1', "sh", nom],
                               capture_output=True, timeout=10)
            return r.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False
    return shutil.which(nom) is not None


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


URI_ATTENTE_JEU = 90   # s : fenêtre pour voir apparaître le jeu (wine, bwrap et Heroic démarrent lentement)
URI_POLL_SEC = 2       # s : rythme des sondes /proc
URI_SORTIE_CONFIRMES = 3   # sondes vides consécutives = sortie confirmée (un bref trou ne clôture pas)
URI_INCONNUS_MAX = 30  # sondes en échec consécutives (None) = abandon de la surveillance,
                       # sans clôture : ~1 min de scan cassé (~8,5 min si chaque sonde
                       # bloque le plein timeout de 15 s) ne doit pas tenir un fil éveillé


def filtre_motifs(motifs):
    """Les répères retenus : assez longs pour ne jamais deviner (un dossier, un
    exécutable — pas un mot quelconque)."""
    return sorted(m for m in motifs if len(m) >= 5)


def motifs_jeu(g, env=None, home=None):
    """Mots reconnaissant le VRAI processus du jeu dans /proc, lus aux sources
    mêmes du jeu :

    - Heroic : enveloppe gogdl / dict legendary (`install_path`) et manifeste
      (`launch_exe`) ;
    - Steam : `installdir` de l'appmanifest -> dossier `common/…` ;
    - itch : cave -> `install_locations.path` + `install_folder_name`, ou le
      dossier personnalisé ;
    - Lutris : `directory` et `executable` de pga.db (id préfixé du runner).

    Pour un lancement par URI (`xdg-open steam://…`), le processus que `launch`
    tient en main est le passe-plat : c'est le jeu lui-même qu'il faut surveiller,
    sinon la session reste ouverte jusqu'à la fermeture de Ludodex et la frise
    affiche des heures jouées là où il y en a eu une. [] = inconnu : pas de
    devinettes, l'ancien repli s'applique (jamais de clôture inventée)."""
    src, ext = g["source"], str(g["ext_id"])
    motifs = set()
    if src == "steam":
        for racine in platform.steam_roots(home):
            try:
                t = (racine / f"appmanifest_{ext}.acf").read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue  # pas installé dans cette bibliothèque
            dossier = champ_acf(t, "installdir")
            if dossier:
                motifs.add(str(racine / "common" / dossier))
        return filtre_motifs(motifs)
    if src == "itch":
        for path in ITCH_DBS:
            if not path.exists():
                continue
            try:
                base = _ro(path)
                try:
                    row = base.execute(
                        "SELECT COALESCE(NULLIF(c.custom_install_folder, ''), "
                        "l.path || '/' || c.install_folder_name) "
                        "FROM caves c LEFT JOIN install_locations l ON l.id = c.install_location_id "
                        "WHERE c.id = ?", (ext,)).fetchone()
                finally:
                    base.close()
            except sqlite3.Error:
                continue  # schéma différent : repli plutôt que plantage
            if row and row[0]:
                motifs.add(str(row[0]))
        return filtre_motifs(motifs)
    if src == "lutris":
        try:
            kind, gid = ext.split(":", 1)
            gid = int(gid)
        except ValueError:
            return []
        for k, path in LUTRIS_DBS:
            if k != kind or not path.exists():
                continue
            try:
                base = _ro(path)
                try:
                    row = base.execute("SELECT directory, executable FROM games WHERE id=?",
                                       (gid,)).fetchone()
                finally:
                    base.close()
            except sqlite3.Error:
                continue
            if row:
                motifs.update(v for v in row if isinstance(v, str) and v)
        return filtre_motifs(motifs)
    if src != "heroic":
        return []
    fichiers = [d / "gog_store" / "installed.json" for d in platform.heroic_dirs(env, home)] + \
               [d / "installed.json" for d in platform.legendary_dirs(env, home)]
    for path in fichiers:
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue  # forme inattendue : rien à y lire, le lancement ne doit pas caser
        # gogdl enveloppe sous "installed" ; legendary écrit un dict plat {appName: …}
        entrees = data.get("installed") if isinstance(data.get("installed"), list) else data.values()
        for e in entrees:
            if not isinstance(e, dict) or str(e.get("appName") or e.get("app_name") or "") != ext:
                continue
            for k in ("install_path", "executable", "launch_exe"):
                if isinstance(e.get(k), str) and e[k]:
                    motifs.add(e[k])
    # Heroic garde aussi le manifeste du lanceur (launch_exe) quand installed.json est vide
    for d in platform.heroic_dirs(env, home):
        try:
            data = json.loads((d / "store_cache" / "legendary_install_info.json")
                              .read_text(encoding="utf-8", errors="ignore"))
        except (OSError, ValueError):
            continue
        ent = data.get(ext) if isinstance(data, dict) else None
        manifeste = ent.get("manifest") if isinstance(ent, dict) else None
        if isinstance(manifeste, dict) and manifeste.get("launch_exe"):
            motifs.add(str(manifeste["launch_exe"]))
    return filtre_motifs(motifs)


def _pids_sous(motifs):
    """PIDs dont cmdline, cwd ou exe contiennent l'un des motifs (lecture /proc).

    None = inconnu (échec de lecture) : à ne pas confondre avec la liste vide,
    qui signifie « le jeu n'est pas là »."""
    if not motifs:
        return []
    if platform.in_flatpak():
        # /proc du bac à sable ne voit que nos processus : le scan est délégué à l'hôte.
        return platform.host_pids_sous(motifs)
    bas = [m.lower() for m in motifs]
    moi, trouves = os.getpid(), []
    try:
        # list() : sur les Python où iterdir est paresseux (générateur, 3.12),
        # l'OSError ne sortirait qu'à l'itération, CEE try — ici, toujours.
        entrees = list(pathlib.Path("/proc").iterdir())
    except OSError:
        return None
    for ent in entrees:
        if not ent.name.isdigit() or int(ent.name) == moi:
            continue
        try:
            texte = (ent / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "ignore")
            texte += " " + os.readlink(ent / "cwd") + " " + os.readlink(ent / "exe")
        except OSError:
            continue  # processus disparu entre-temps
        if any(m in texte.lower() for m in bas):
            trouves.append(int(ent.name))
    return trouves


def _watch_uri(motifs, sid, on_exit, debut, fenetre=None, lister=None, dormir=None):
    """Attend que le JEU apparaisse (motifs dans /proc), puis attende sa sortie —
    par le rappel de l'APPLICATION, sur son fil principal, comme `_watch_game`.

    Jamais vu dans la fenêtre : on ne clôture rien (l'ancien repli — fermeture à la
    fermeture de l'appli — vaut mieux qu'une durée inventée). Une sonde en échec
    (None) est INCONNUE : elle n'avance ni l'apparition ni la sortie ; trop
    d'échecs consécutifs (URI_INCONNUS_MAX) abandonnent la surveillance sans
    jamais clôturer — mieux vaut l'ancien repli qu'une session fermée à tort.
    Résidu connu : si Ludodex est démarré avec un cwd sur un motif de jeu, les
    sondes ne seront jamais vides — repli documenté, jamais une clôture fausse."""
    fenetre = URI_ATTENTE_JEU if fenetre is None else fenetre
    lister = _pids_sous if lister is None else lister
    dormir = time.sleep if dormir is None else dormir
    vu = False
    limite = debut + fenetre
    while time.monotonic() < limite:
        if lister(motifs):
            vu = True
            break
        dormir(URI_POLL_SEC)
    if not vu:
        return
    vides, inconnus = 0, 0
    while vides < URI_SORTIE_CONFIRMES:
        dormir(URI_POLL_SEC)
        pids = lister(motifs)
        if pids is None:                     # échec du scan : inconnu
            inconnus += 1
            if inconnus >= URI_INCONNUS_MAX:
                return                       # abandon, sans clôture inventée
            continue
        inconnus = 0                          # un scan fiable remet le compteur
        vides = vides + 1 if not pids else 0
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
        proc = subprocess.Popen(platform.host_argv(argv, env), cwd=cwd,
                                env={**os.environ, **env} if env else None,
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
    if on_exit is not None:  # l'overlay meurt à la fin de session, comme le jeu
        origine = on_exit
        def on_exit(sid, _o=origine):
            overlay.arreter_overlay()
            _o(sid)
    if get_setting(con, "overlay", "1") == "1":
        overlay.lancer_overlay(str(g["name"]))
    if on_exit is not None and cur.rowcount:  # INSERT ignoré = rien d'ouvert à suivre
        # Un argv contenant "://" est remis à xdg-open : le processus tenu n'est que
        # le passe-plat, qui rend la main en ms. Pour un jeu connu (Heroic), on suit
        # alors le JEU lui-même (motifs /proc) — c'est cette clôture qui manquait.
        uri = any("://" in a for a in argv)
        try:
            motifs = motifs_jeu(g) if uri else []
        except (OSError, ValueError):   # fichiers Heroic illisibles : l'ancien repli vaut mieux qu'un plantage
            motifs = []
        if motifs:
            threading.Thread(target=_watch_uri,
                             args=(motifs, cur.lastrowid, on_exit, time.monotonic()),
                             daemon=True).start()
        else:
            threading.Thread(target=_watch_game,
                             args=(proc, cur.lastrowid, on_exit, time.monotonic()),
                             daemon=True).start()
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
        subprocess.Popen(platform.host_argv(argv, env), env={**os.environ, **env} if env else None,
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
        if which_bin("lutris"):
            exe = shutil.which("lutris")
            # Sous le sandbox, le chemin natif est inconnu : le nom brut suffit,
            # host_argv le résout sur l'hôte.
            return [exe] if exe else ["lutris"]
        return ["flatpak", "run", "net.lutris.Lutris"]
    if source == "retroarch":  # aucun protocole : son exécutable, PATH puis flatpak
        if which_bin("retroarch"):
            exe = shutil.which("retroarch")
            return [exe] if exe else ["retroarch"]
        return ["flatpak", "run", "org.libretro.RetroArch"]
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
            if py and shutil.which("lutris"):
                return [py, "-c", script]
            # Sous le sandbox : binaires hôtes, nom brut résolu par host_argv.
            if platform.in_flatpak() and which_bin("python3") and which_bin("lutris"):
                return ["python3", "-c", script]
            return None
        return ["flatpak", "run", "--command=python3", "net.lutris.Lutris", "-c", script] \
            if (platform.in_flatpak() or shutil.which("flatpak")) else None
    if src == "heroic":
        d = legendary_install_dir(ext)
        if d is None or not which_bin("legendary"):
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
