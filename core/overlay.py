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


def chemin_hook(env: dict = None) -> str:
    """Emplacement du shim dans l'installation UTILISATRICE de l'addon
    (`current/active` suit la déploiement active, quel que soit le binaire)."""
    home = (env or os.environ).get("HOME") or os.path.expanduser("~")
    return (f"{home}/.local/share/flatpak/app/{OVERLAY_APP_ID}"
            "/current/active/files/lib/libludodex-hook.so")


def chemin_hook_systeme() -> str:
    return (f"/var/lib/flatpak/app/{OVERLAY_APP_ID}"
            "/current/active/files/lib/libludodex-hook.so")


def preparer_hook(env: dict = None) -> "str | None":
    """Copie le shim de l'addon dans un dossier HÔTE lisible par le jeu et
    remet l'état à « fermé ». None si l'addon est absent (best-effort).

    Le jeu recevra ce chemin par LD_PRELOAD (façon Steam) : le hook relâche
    la capture de souris de l'intérieur du processus — sans changement de
    focus, donc sans miniaturisation (spike du 2026-10-17)."""
    home = (env or os.environ).get("HOME") or os.path.expanduser("~")
    source = chemin_hook(env)
    if not os.path.isfile(source):
        source = chemin_hook_systeme()
        if not os.path.isfile(source):
            return None
    dossier = f"{home}/.local/share/ludodex-hook"
    chemin = f"{dossier}/libludodex-hook.so"
    try:
        os.makedirs(dossier, exist_ok=True)
        if (not os.path.isfile(chemin)
                or os.path.getsize(chemin) != os.path.getsize(source)):
            with open(source, "rb") as src, open(chemin, "wb") as dst:
                dst.write(src.read())
        with open(f"{dossier}/etat", "w", encoding="utf-8") as f:
            f.write("0")  # chaque session démarre « souris capturée »
    except OSError:
        return None
    return chemin


def command_avec_hook(argv: list, chemin: str) -> list:
    """Préfixe `env LD_PRELOAD=…` — cumule un préload déjà présent."""
    existant = os.environ.get("LD_PRELOAD", "").strip(":")
    charge = f"{chemin}:{existant}" if existant else chemin
    return ["env", f"LD_PRELOAD={charge}", *argv]


def command_overlay(nom_jeu: str, souris_libre: bool = True, langue: str = None,
                    env: dict = None) -> list:
    """Commande hôte qui démarre l'overlay pour la session d'un jeu.

    `souris_libre` (réglage de Ludodex) : l'overlay relâche la capture souris
    du jeu à l'ouverture ; à False il ne le fait pas. Le flag part DUPLICATA
    (`--souris-libre` / `--sans-souris-libre`) car une instance déjà vivante
    reçoit la commande par `do_command_line` : c'est la dernière valeur qui
    gagne, et il faut pouvoir repasser à False.

    `langue` : langue choisie dans Ludodex — sans `--lang`, l'overlay ne
    verrait que les variables POSIX du processus, donc la langue SYSTÈME
    (pas forcément celle de Ludodex, spec du 2026-10-18).

    Sur l'hôte : `flatpak run … --game NOM`. Dans le sandbox Ludodex :
    enveloppée par le `sh -c` de dépouillement d'`host_argv` — les
    arguments `--game`/nom, le flag souris et `--lang` restent en queue."""
    argv = ["flatpak", "run", OVERLAY_APP_ID, "--game", nom_jeu,
            "--souris-libre" if souris_libre else "--sans-souris-libre"]
    if langue:
        argv += ["--lang", langue]
    return platform.host_argv(argv, env=env)


def lancer_overlay(nom_jeu: str, souris_libre: bool = True, langue: str = None) -> bool:
    """Démarre l'overlay — False si l'addon est absent (silencieux)."""
    global _proc, _atexit_fait
    try:
        _proc = subprocess.Popen(
            command_overlay(nom_jeu, souris_libre=souris_libre, langue=langue),
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
