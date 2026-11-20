#!/usr/bin/env python3
"""overlay.py — lancement de l'overlay en jeu (`ludodex-overlay`, processus séparé).

Best effort : si l'overlay est désactivé, absent ou ne démarre pas (pas de
gtk4-layer-shell, compositeur sans layer-shell…), le jeu se lance quand même, sans
bruit. L'overlay s'éteint seul quand la session du jeu est close (il lit la base)."""
import os, pathlib, shutil, subprocess, sys

from .db import get_setting

ROOT = pathlib.Path(__file__).resolve().parent.parent


def enabled(con):
    return get_setting(con, "overlay_enabled") != "0"


def command():
    """argv de base de `ludodex-overlay`, ou None si introuvable."""
    exe = shutil.which("ludodex-overlay")   # flatpak (/app/bin) ou installation système
    if exe:
        return [exe]
    if getattr(sys, "frozen", False):
        beside = pathlib.Path(sys.executable).with_name("ludodex-overlay")
        return [str(beside)] if beside.is_file() else None
    script = ROOT / "overlay_main.py"
    return [sys.executable, str(script)] if script.is_file() else None


def _spawn(args):
    cmd = command()
    if cmd is None or os.environ.get("LUDODEX_NO_OVERLAY"):
        return False
    try:
        subprocess.Popen(cmd + args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
        return True
    except OSError:
        return False


def start(con, gid):
    """Démarre l'overlay pour le jeu `gid` (ou le fait passer à ce jeu s'il tourne déjà)."""
    return enabled(con) and _spawn(["--game", str(gid)])


def toggle():
    return _spawn(["--toggle"])
