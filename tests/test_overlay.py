#!/usr/bin/env python3
"""tests/test_overlay.py — addon flatpak Ludodex Overlay : commande, lancement silencieux, mort."""
import signal

from core import overlay


def test_command_overlay_sur_hote():
    """Aucun flag souris : l'overlay libère TOUJOURS la souris dès l'ouverture.
    Le réglage « Libérer la souris des jeux » a été retiré — il ne servait à
    rien : la porte anti-re-capture doit être active dès l'ouverture pour que
    la libération tienne (sans porte, le jeu re-capture aussitôt)."""
    assert overlay.command_overlay("SULFUR") == [
        "flatpak", "run", "io.github.AdrienPiechocki.LudodexOverlay",
        "--game", "SULFUR"]


def test_command_overlay_dans_le_sandbox():
    cmd = overlay.command_overlay("SULFUR", env={"FLATPAK_ID": "io.github.AdrienPiechocki.Ludodex"})
    assert cmd[0] == "flatpak-spawn" and "--host" in cmd
    assert cmd[-2:] == ["--game", "SULFUR"]


def test_command_overlay_transmet_la_langue():
    """Réglage de langue de Ludodex transmis à l'overlay (--lang en queue) :
    sans lui, l'overlay retomberait sur la langue du système hôte."""
    assert overlay.command_overlay("SULFUR", langue="fr") == [
        "flatpak", "run", "io.github.AdrienPiechocki.LudodexOverlay",
        "--game", "SULFUR", "--lang", "fr"]


def test_lancer_transmet_la_langue(monkeypatch):
    """`lancer_overlay` porte `--lang` jusqu'à la commande réellelement spawnée."""
    commandes = []

    class FauxProc:
        pid = None

    monkeypatch.setattr(overlay.subprocess, "Popen",
                        lambda cmd, **kw: commandes.append(cmd) or FauxProc())
    assert overlay.lancer_overlay("X", langue="fr") is True
    assert commandes[0][-2:] == ["--lang", "fr"]


def test_chemin_hook_user_et_systeme():
    utilisateur = overlay.chemin_hook({"HOME": "/h"})
    assert utilisateur == "/h/.local/share/flatpak/app/%s/current/active/files/lib/libludodex-hook.so" \
        % overlay.OVERLAY_APP_ID
    assert overlay.chemin_hook_systeme() == \
        "/var/lib/flatpak/app/%s/current/active/files/lib/libludodex-hook.so" % overlay.OVERLAY_APP_ID


def test_preparer_hook_copie_le_shim(tmp_path):
    env = {"HOME": str(tmp_path)}
    src = tmp_path / ".local/share/flatpak/app" / overlay.OVERLAY_APP_ID / \
        "current/active/files/lib"
    src.mkdir(parents=True)
    (src / "libludodex-hook.so").write_bytes(b"shim")
    chemin = overlay.preparer_hook(env=env)
    assert chemin == str(tmp_path / ".local/share/ludodex-hook/libludodex-hook.so")
    assert (tmp_path / ".local/share/ludodex-hook/libludodex-hook.so").read_bytes() == b"shim"
    assert (tmp_path / ".local/share/ludodex-hook/etat").read_text() == "0"


def test_preparer_hook_sans_addon_renvoie_none(tmp_path):
    assert overlay.preparer_hook(env={"HOME": str(tmp_path)}) is None


def test_command_avec_hook_prefixe_env():
    cmd = overlay.command_avec_hook(["jeu", "--fullscreen"], "/x/libludodex-hook.so")
    assert cmd[:2] == ["env", "LD_PRELOAD=/x/libludodex-hook.so"]
    assert cmd[2:] == ["jeu", "--fullscreen"]


def test_command_avec_hook_cumule_le_preload_existant(monkeypatch):
    monkeypatch.setenv("LD_PRELOAD", "/ancien/shim.so")
    cmd = overlay.command_avec_hook(["jeu"], "/x/shim.so")
    assert cmd[1] == "LD_PRELOAD=/x/shim.so:/ancien/shim.so"


def test_lancer_sans_addon_est_silencieux(monkeypatch):
    """Addon non installé : False, aucune alerte (spec : échec silencieux)."""
    def faux_popen(*args, **kwargs):
        raise FileNotFoundError
    monkeypatch.setattr(overlay.subprocess, "Popen", faux_popen)
    assert overlay.lancer_overlay("X") is False


def test_arreter_utilise_terminate(monkeypatch):
    class FauxProc:
        termine = False

        def terminate(self):
            self.termine = True

        def poll(self):
            return 0 if self.termine else None

    faux = FauxProc()
    monkeypatch.setattr(overlay, "_proc", faux)
    overlay.arreter_overlay()
    assert faux.termine


def test_arreter_vise_le_groupe_de_processus(monkeypatch):
    """L'app Python de la sandbox SURVIT à la mort du seul `flatpak run`
    (constat du 2026-10-17 : processus repris par l'init, scope systemd et
    instance flatpak restés actifs) — le signal part donc sur le GROUPE."""
    signaux = []
    monkeypatch.setattr(overlay.os, "killpg",
                        lambda pid, num: signaux.append((pid, num)))
    monkeypatch.setattr(overlay.subprocess, "run",
                        lambda argv, **kw: None)  # surtout pas de vrai `flatpak kill` ici

    class FauxProc:
        pid = 4242

        def poll(self):
            return 0  # déjà terminé : le kill différé (3 s) ne part pas

    monkeypatch.setattr(overlay, "_proc", FauxProc())
    overlay.arreter_overlay()
    assert signaux == [(4242, signal.SIGTERM)]


def test_arreter_tue_l_instance_flatpak(monkeypatch):
    """Chaîne sandboxée : killpg ne tue que flatpak-spawn côté sandbox, la
    chaîne HÔTE survit (constat du 2026-10-17) — `flatpak kill` complète."""
    appels = []
    monkeypatch.setattr(overlay.os, "killpg", lambda pid, num: None)
    monkeypatch.setattr(overlay.subprocess, "run",
                        lambda argv, **kw: appels.append(argv))

    class FauxProc:
        pid = 4243

        def poll(self):
            return 0

    monkeypatch.setattr(overlay, "_proc", FauxProc())
    overlay.arreter_overlay()
    assert appels == [["flatpak", "kill", overlay.OVERLAY_APP_ID]]
