"""hook.py — drapeau « souris libérée » partagé avec le hook LD_PRELOAD.

Ludodex injecte `libludodex-hook.so` dans le processus du jeu (façon Steam) ;
le hook sonde ce fichier toutes les 300 ms. L'overlay écrit « 1 » en s'ouvrant
(le hook relâche la capture de souris DE L'INTÉRIEUR du jeu — sans changement
de focus, donc sans miniaturisation) et « 0 » à la fermeture (le jeu reprend
la main tout seul à sa prochaine assertion de capture).
"""
import os
import pathlib

DOSSIER = "ludodex-hook"
FICHIER = "etat"


def chemin_etat(env: dict = None) -> pathlib.Path:
    home = (env or os.environ).get("HOME") or str(pathlib.Path.home())
    return pathlib.Path(home) / ".local" / "share" / DOSSIER / FICHIER


def ecrire_etat(ouvert: bool, env: dict = None) -> None:
    """Écrit l'état de façon atomique (écriture + renommage) — best-effort :
    un dossier inaccessible ne plante jamais l'overlay."""
    chemin = chemin_etat(env)
    try:
        chemin.parent.mkdir(parents=True, exist_ok=True)
        temp = chemin.with_suffix(".temp")
        temp.write_text("1" if ouvert else "0", encoding="utf-8")
        os.replace(temp, chemin)
    except OSError:
        pass
