#!/usr/bin/env python3
"""overlay.py — lancement de l'overlay en jeu (`ludodex-overlay`, processus séparé).

Best effort : si l'overlay est désactivé, absent ou ne démarre pas (pas de
gtk4-layer-shell, compositeur sans layer-shell…), le jeu se lance quand même, sans
bruit. L'overlay s'éteint seul quand la session du jeu est close (il lit la base)."""
import os, pathlib, shutil, subprocess, sys

from . import platform
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


def _cyberrealm_on_host():
    """Flatpak : le jeu CyberRealm tourne-t-il sur l'HÔTE ? Le bac à sable ne voit pas son socket
    (flatpak filtre `$XDG_RUNTIME_DIR`) ; `$XDG_RUNTIME_DIR` a le même chemin des deux côtés."""
    rt = os.environ.get("XDG_RUNTIME_DIR")
    if not (platform.in_flatpak() and rt and platform.cyberrealm_wrapper()):
        return False
    try:
        return subprocess.run(["flatpak-spawn", "--host", "test", "-e", f"{rt}/cyberrealm-0"],
                              capture_output=True, timeout=3).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _spawn(args):
    cmd = command()
    if cmd is None or os.environ.get("LUDODEX_NO_OVERLAY"):
        return False
    if _cyberrealm_on_host():
        # Flatpak sous CyberRealm : l'overlay doit se dessiner DANS le bureau 3D, donc partir de l'hôte,
        # derrière `cyberrealm-launch` (affichage et bus D-Bus du jeu), dans son propre bac à sable.
        # Là-dedans flatpak rebaptise l'affichage (`wayland-0`) : le suivi des fenêtres est forcé.
        argv = platform.host_argv(["flatpak", "run", "--env=LUDODEX_OVERLAY_TRACKER=cyberrealm",
                                   "--command=ludodex-overlay", os.environ["FLATPAK_ID"], *args])
        try:
            subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
            return True
        except OSError:
            return False
    try:
        # Comme les jeux (platform.host_argv) : derrière `cyberrealm-launch`, qui bascule l'affichage et
        # le bus D-Bus vers CyberRealm quand il tourne — l'overlay se dessine alors DANS le bureau 3D, à
        # côté du jeu, et non sur le bureau hôte par-dessus la fenêtre de CyberRealm. Sans CyberRealm, le
        # wrapper exécute la commande telle quelle. (Flatpak : le bac à sable ne voit pas le socket du jeu.)
        wrapper = [] if platform.in_flatpak() else platform.cyberrealm_wrapper()
        subprocess.Popen(wrapper + cmd + args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
        return True
    except OSError:
        return False


def start(con, gid, pid=None):
    """Démarre l'overlay pour le jeu `gid` (ou le fait passer à ce jeu s'il tourne déjà).
    `pid` : processus lancé pour le jeu, qui sert à retrouver sa fenêtre (taille, réduite)."""
    return enabled(con) and _spawn(["--game", str(gid)] + (["--pid", str(pid)] if pid else []))


def toggle():
    return _spawn(["--toggle"])
