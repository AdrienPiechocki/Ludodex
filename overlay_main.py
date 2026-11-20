#!/usr/bin/env python3
"""overlay_main.py — point d'entrée de `ludodex-overlay`.

    ludodex-overlay --game ID    démarre l'overlay pour ce jeu (lancé par Ludodex)
    ludodex-overlay --toggle     ouvre / ferme l'overlay en cours (touche du compositeur)
    ludodex-overlay --quit       l'arrête

Il se relance lui-même avec LD_PRELOAD=libgtk4-layer-shell : la bibliothèque doit être
chargée AVANT libwayland-client, ce que Python ne garantit pas (doc de gtk4-layer-shell).
C'est le processus de l'overlay seul qui est concerné — jamais celui d'un jeu."""
import glob, os, sys

_DIRS = ("/app/lib", "/usr/lib", "/usr/lib64", "/usr/local/lib", "/usr/lib/x86_64-linux-gnu",
         "/usr/lib/aarch64-linux-gnu")


def _layer_shell_lib():
    for d in _DIRS:
        hits = sorted(glob.glob(os.path.join(d, "libgtk4-layer-shell.so*")))
        if hits:
            return hits[0]
    return None


def main():
    lib = _layer_shell_lib()
    if lib and lib not in os.environ.get("LD_PRELOAD", ""):
        env = dict(os.environ, LD_PRELOAD=(os.environ.get("LD_PRELOAD", "") + " " + lib).strip())
        os.execve(sys.executable, [sys.executable] + sys.argv, env)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from overlay.app import LS, Overlay
    if LS is None and not os.environ.get("LUDODEX_OVERLAY_WINDOWED"):
        print("ludodex-overlay: gtk4-layer-shell is not installed — no overlay.", file=sys.stderr)
        return 2
    return Overlay().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
