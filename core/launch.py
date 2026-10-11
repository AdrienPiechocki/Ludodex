#!/usr/bin/env python3
"""launch.py — construction et exécution des commandes de lancement."""
import json, os, pathlib, re, shutil, subprocess, time

from i18n import tr
from .platform import uri_command, split_command, join_command, popen_kwargs


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
        base = ["flatpak", "run", "org.libretro.RetroArch"] if d["f"] else ["retroarch"]
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


def close_open_sessions(con, now=None):
    """Clôt les sessions encore ouvertes (ended=0) et enregistre leur durée."""
    now = int(time.time()) if now is None else now
    rows = con.execute("SELECT id, started FROM sessions WHERE ended=0").fetchall()
    for r in rows:
        con.execute("UPDATE sessions SET ended=?, minutes=? WHERE id=?",
                    (now, max(0, (now - r["started"]) // 60), r["id"]))
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
    con.execute("UPDATE games SET last_played=?, status=CASE WHEN status IN ('backlog','pause') THEN 'en_cours' ELSE status END WHERE id=?",
                (now, g["id"]))
    con.execute("INSERT OR IGNORE INTO sessions (game_id, started, ended, minutes) VALUES (?, ?, 0, 0)", (g["id"], now))
    con.commit()
    print(f"▶ {g['name']}.")
    return True
