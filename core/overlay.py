#!/usr/bin/env python3
"""overlay.py — lancement et arrêt de l'addon flatpak « Ludodex Overlay ».

L'addon est un flatpak SÉPARÉ, installé à côté : s'il manque, le jeu se
lance simplement, sans overlay ni alerte (best-effort silencieux, spec).
L'overlay vit le temps de la session de jeu : tué à la fin de celle-ci
(le même rappel qui clôt la session) et au `atexit` de Ludodex.
"""
import atexit, os, signal, subprocess, threading

from . import platform

OVERLAY_APP_ID = "io.github.AdrienPiechocki.LudodexOverlay"

_proc = None            # processus overlay de la session en cours
_atexit_fait = False    # atexit enregistré une seule fois


def command_overlay(nom_jeu: str, env: dict = None) -> list:
    """Commande hôte qui démarre l'overlay pour la session d'un jeu.

    Sur l'hôte : `flatpak run … --game NOM`. Dans le sandbox Ludodex :
    enveloppée par le `sh -c` de dépouillement d'`host_argv` — les deux
    derniers arguments restent `--game` / le nom."""
    return platform.host_argv(
        ["flatpak", "run", OVERLAY_APP_ID, "--game", nom_jeu], env=env)


def lancer_overlay(nom_jeu: str) -> bool:
    """Démarre l'overlay — False si l'addon est absent (silencieux)."""
    global _proc, _atexit_fait
    try:
        _proc = subprocess.Popen(command_overlay(nom_jeu),
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL,
                                 start_new_session=True)
    except OSError:  # addon non installé, flatpak absent : rien à signaler
        return False
    if not _atexit_fait:
        atexit.register(arreter_overlay)
        _atexit_fait = True
    return True


def arreter_overlay() -> None:
    """Ferme l'overlay de la session : SIGTERM sur le GROUPE de processus,
    puis SIGKILL sur le groupe après 3 s si le wrapper survit encore.

    Le groupe est celui créé par `start_new_session=True` au lancement : l'app
    Python de la sandbox en fait partie (constat du 2026-10-17 — elle SURVIVAIT
    à la mort du seul `flatpak run`, repris par l'init : instance flatpak et
    scope systemd restaient actifs). Sans processus : rien."""
    global _proc
    proc, _proc = _proc, None
    if proc is None:
        return
    _signaler(proc, signal.SIGTERM)
    _tuer_instance_flatpak(proc)

    def tuer():
        poll = getattr(proc, "poll", None)
        if poll is not None and poll() is None:  # wrapper encore vivant : pid fiable
            _signaler(proc, signal.SIGKILL)

    chronometre = threading.Timer(3.0, tuer)
    chronometre.daemon = True  # un kill retardé ne retient pas Ludodex
    chronometre.start()


def _tuer_instance_flatpak(proc) -> None:
    """`flatpak kill` sur l'instance de l'addon : indispensable quand Ludodex
    est lui-même sandboxé — killpg ne tue alors que `flatpak-spawn`, la chaîne
    HÔTE (sh → flatpak run → bwrap → app) survit (constat du 2026-10-17), et
    `flatpak kill` nettoie aussi l'instance et son scope systemd.

    Réservé aux vrais processus (`pid` présent) : un processus factice de
    test ne déclencherait jamais une vraie commande flatpak."""
    if getattr(proc, "pid", None) is None:
        return
    try:
        subprocess.run(platform.host_argv(["flatpak", "kill", OVERLAY_APP_ID]),
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        pass  # flatpak absent ou gelé : la fermeture continue sans lui


def _signaler(proc, num) -> None:
    """SIGTERM/SIGKILL sur le groupe entier (wrapper, bwrap, app) ; repli sur
    le processus seul si le groupe a déjà disparu ou n'est pas identifiable
    (processus factice des tests). Les erreurs sont absorbées : on ferme."""
    pid = getattr(proc, "pid", None)
    if pid is not None:
        try:
            os.killpg(pid, num)
            return
        except OSError:
            pass
    appel = getattr(proc, "terminate" if num == signal.SIGTERM else "kill", None)
    if appel is not None:
        try:
            appel()
        except OSError:
            pass
