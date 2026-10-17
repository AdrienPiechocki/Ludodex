#!/usr/bin/env python3
"""tests/test_host.py — bac à sable Flatpak : détection et commande hôte."""
from core import platform

SB = {"FLATPAK_ID": "io.github.AdrienPiechocki.Ludodex",
      "XDG_DATA_HOME": "/home/u/.var/app/x/data",
      "WAYLAND_DISPLAY": "wayland-0", "XDG_RUNTIME_DIR": "/run/user/1000"}


def test_in_flatpak_reconnait_le_sandbox():
    assert platform.in_flatpak(env=SB)
    assert not platform.in_flatpak(env={})


def test_host_argv_hors_flatpak_sans_changement():
    assert platform.host_argv(["steam", "steam://rungameid/1"], env={}) == \
        ["steam", "steam://rungameid/1"]


def test_host_argv_dans_flatpak_passe_par_l_hote():
    argv = platform.host_argv(["xdg-open", "steam://run/1"], env=SB)
    assert argv[:5] == ["flatpak-spawn", "--host", "sh", "-c", argv[4]]
    assert argv[5] == "sh"
    assert argv[6:] == ["xdg-open", "steam://run/1"]
    script = argv[4]
    assert "FLATPAK_" in script                      # environnement du sandbox dépouillé
    assert "unset XDG_DATA_HOME XDG_CONFIG_HOME XDG_CACHE_HOME XDG_DATA_DIRS XDG_CONFIG_DIRS;" in script
    assert "XDG_RUNTIME_DIR" not in script.split("unset", 1)[1].split(";", 1)[0]  # jamais unset
    assert 'exec "$@"' in script


def test_host_argv_transmet_les_variables_personnalisees_et_l_affichage():
    script = platform.host_argv(["jeu"], env_vars={"MA_VAR": "a b"}, env=SB)[4]
    assert "export MA_VAR='a b';" in script          # shlex.quote
    assert "export WAYLAND_DISPLAY=wayland-0;" in script
    assert "export XDG_RUNTIME_DIR=/run/user/1000;" in script


# ---------- application du préfixe aux exécutions de commandes ----------

import sqlite3

from core import db
from core import launch as L


def _con_jeu():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(db.SCHEMA)
    con.execute("INSERT INTO games (source, ext_id, name) "
                "VALUES ('custom', 'xdg-open https://example.org', 'Jeu')")
    con.commit()
    return con


def _popen_capture(monkeypatch, cap):
    def fake(argv, **kw):
        cap.setdefault("argv", argv)  # 1er Popen = le jeu (le 2e = l'overlay de session)
        return object()
    monkeypatch.setattr(L.subprocess, "Popen", fake)


def test_launch_dans_flatpak_passe_par_l_hote(monkeypatch):
    monkeypatch.setattr(platform, "in_flatpak", lambda env=None: True)
    cap = {}
    _popen_capture(monkeypatch, cap)
    con = _con_jeu()
    g = dict(con.execute("SELECT * FROM games").fetchone())
    assert L.launch(con, g) is True
    assert cap["argv"][:2] == ["flatpak-spawn", "--host"]
    assert cap["argv"][6:] == ["xdg-open", "https://example.org"]


def test_launch_hors_flatpak_sans_changement(monkeypatch):
    cap = {}
    _popen_capture(monkeypatch, cap)
    con = _con_jeu()
    g = dict(con.execute("SELECT * FROM games").fetchone())
    assert L.launch(con, g) is True
    assert cap["argv"] == ["xdg-open", "https://example.org"]


def test_detached_dans_flatpak_passe_par_l_hote(monkeypatch):
    monkeypatch.setattr(platform, "in_flatpak", lambda env=None: True)
    cap = {}
    _popen_capture(monkeypatch, cap)
    assert L._detached(["flatpak", "install", "-y", "org.foo"]) is None
    assert cap["argv"][:2] == ["flatpak-spawn", "--host"]


def test_open_url_dans_flatpak_passe_par_l_hote(monkeypatch):
    monkeypatch.setattr(platform, "in_flatpak", lambda env=None: True)
    cap = {}
    monkeypatch.setattr(platform.subprocess, "Popen", lambda argv, **kw: cap.update(argv=argv))
    assert platform.open_url("https://example.org") is True
    assert cap["argv"][:2] == ["flatpak-spawn", "--host"]


class _Rep:
    def __init__(self, code):
        self.returncode = code


def test_check_command_interroge_l_hote(monkeypatch):
    monkeypatch.setattr(platform, "in_flatpak", lambda env=None: True)
    monkeypatch.setattr(L.subprocess, "run", lambda *a, **kw: _Rep(0))
    assert L.check_command("steam") is None          # présent sur l'hôte
    monkeypatch.setattr(L.subprocess, "run", lambda *a, **kw: _Rep(1))
    assert L.check_command("steam") == "steam"       # réellement absent
    def _ko(*a, **kw):
        raise OSError("hôte injoignable")
    monkeypatch.setattr(L.subprocess, "run", _ko)
    assert L.check_command("steam") is None          # pas de fausse alerte


# ---------- suivi des processus de jeu depuis le bac à sable ----------

def test_host_pids_sous_delègue_le_scan_a_l_hote(monkeypatch):
    cap = {}
    def fake(argv, **kw):
        cap["argv"] = argv
        cap["input"] = kw.get("input")
        class _R:
            stdout = b"42\n7\n42\n"
            returncode = 0
        return _R()
    monkeypatch.setattr(platform.subprocess, "run", fake)
    motifs = ["/home/u/jeu/dossier", "/home/u/second jeu"]
    assert platform.host_pids_sous(motifs) == [7, 42]      # dédoublonné, trié
    assert cap["argv"][:4] == ["flatpak-spawn", "--host", "--env-fd=0", "sh"]
    # Les motifs passent par l'FD d'env (env -0), JAMAIS par l'argv : le client
    # flatpak-spawn est visible dans le /proc hôte, son argv y serait un match
    # permanent du scan sur lui-même.
    assert cap["input"] == b"LUDEX_MOTIFS=/home/u/jeu/dossier\n/home/u/second jeu\0"
    assert not any(m in a for a in cap["argv"] for m in motifs)
    cap.clear()
    assert platform.host_pids_sous([]) == []               # sans appel inutile
    assert "argv" not in cap


def test_scan_script_sans_auto_correspondance():
    """Témoin local : le script réel ne doit ni se détecter lui-même, ni rater un vrai match."""
    import os
    import subprocess
    import time
    script = platform._SCAN_SCRIPT

    def scan(motif):
        r = subprocess.run(["sh", "-c", script, "sh"],
                           capture_output=True, text=True, timeout=60,
                           env={**os.environ, "LUDEX_MOTIFS": motif})
        assert r.returncode == 0, r.stderr
        return [int(x) for x in r.stdout.split()]

    # Motif absent de tout processus : sortie vide (l'ancien script y voyait son propre sh).
    assert scan("/zzz/unique-9xyz-scan") == []
    # Motif présent dans la cmdline d'un processus réel : son PID, et lui seul.
    # (Un `sh -c "sleep 5"` ne convient pas : dash remplace son argv par exec.)
    import sys
    marqueur = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(5)", "/zzz-present-9xyz"])
    try:
        time.sleep(0.3)
        trouves = scan("/zzz-present-9xyz")
        assert marqueur.pid in trouves
    finally:
        marqueur.terminate()
        marqueur.wait()
    assert scan("/zzz-present-9xyz") == []                 # plus là = plus de match


def test_host_pids_sous_hote_injoignable_renvoie_inconnu(monkeypatch):
    def _ko(*a, **kw):
        raise OSError
    monkeypatch.setattr(platform.subprocess, "run", _ko)
    # L'échec du scan est INCONNU, jamais « le jeu n'est plus là ».
    assert platform.host_pids_sous(["/home/u/jeu/dossier"]) is None


def test_host_pids_sous_code_de_sortie(monkeypatch):
    class _R:
        def __init__(self, code, out=b""):
            self.returncode, self.stdout = code, out
    monkeypatch.setattr(platform.subprocess, "run", lambda *a, **kw: _R(0, b""))
    assert platform.host_pids_sous(["/home/u/jeu/dossier"]) == []   # aucun match
    monkeypatch.setattr(platform.subprocess, "run", lambda *a, **kw: _R(0, b"7\n"))
    assert platform.host_pids_sous(["/home/u/jeu/dossier"]) == [7]
    # Le script sort toujours 0 ou 2 : tout autre code est un échec de transport
    # (portail, chdir du cwd sandbox) — inconnu, jamais « plus de jeu ».
    monkeypatch.setattr(platform.subprocess, "run", lambda *a, **kw: _R(1))
    assert platform.host_pids_sous(["/home/u/jeu/dossier"]) is None
    monkeypatch.setattr(platform.subprocess, "run", lambda *a, **kw: _R(2, b"7\n"))
    assert platform.host_pids_sous(["/home/u/jeu/dossier"]) is None


def test_pids_sous_natif_proc_illisible_renvoie_inconnu(monkeypatch):
    import pathlib
    def iterdir_leveur():        # sur Python 3.12, iterdir est un générateur :
        def gen():               # l'OSError ne sort qu'à l'itération, pas à l'appel
            raise OSError("proc illisible")
        return gen()
    class _Faux:
        def iterdir(self):
            return iterdir_leveur()
    monkeypatch.setattr(pathlib, "Path", lambda *a: _Faux())
    monkeypatch.setattr(platform, "in_flatpak", lambda env=None: False)
    assert L._pids_sous(["/jeu/x"]) is None


# ---------- _watch_uri : un échec de scan n'est pas une sortie de jeu ----------

def _watch(seq, cap, monkeypatch, fenetre=20, max_inconnus=None, age=10):
    """Exécute _watch_uri avec une séquence de réponses de `lister` (finie puis
    None). `cap` reçoit les appels de on_exit ; `age` rend la grâce satisfaisante."""
    if max_inconnus is not None:
        monkeypatch.setattr(L, "URI_INCONNUS_MAX", max_inconnus)
    it = iter(seq)
    n = {"lister": 0}
    def lister(m):
        n["lister"] += 1
        return next(it, None)
    L._watch_uri(["/jeu/x"], 7, lambda sid: cap.append(sid),
                 debut=L.time.monotonic() - age,         # largement dans la grâce
                 fenetre=fenetre, lister=lister, dormir=lambda s: None)
    return n["lister"]


def test_watch_uri_scan_en_echec_ne_cloture_rien(monkeypatch):
    cap = []
    # Vu, puis que des échecs : abandon après URI_INCONNUS_MAX, jamais on_exit.
    _watch([[1], None, None, None, None], cap, monkeypatch, max_inconnus=3)
    assert cap == []


def test_watch_uri_sortie_confirmee_cloture(monkeypatch):
    cap = []
    _watch([[1], [], [], []], cap, monkeypatch)
    assert cap == [7]                                  # parité inchangée


def test_watch_uri_jamais_vu_ne_cloture_rien(monkeypatch):
    cap = []
    _watch([None], cap, monkeypatch, fenetre=0.02, age=0)   # échecs dans la fenêtre
    assert cap == []


# ---------- résolution des binaires hôtes (sites hors plan) ----------

class _Rep2:
    def __init__(self, code):
        self.returncode = code


def _sable(monkeypatch):
    """Sandbox : aucun binaire local, l'hôte répond par flatpak-spawn."""
    monkeypatch.setattr(platform, "in_flatpak", lambda env=None: True)
    monkeypatch.setattr(L.shutil, "which", lambda n: None)
    cap = {"rc": 0}
    def fake(argv, **kw):
        cap["argv"] = argv
        return _Rep2(cap["rc"])
    monkeypatch.setattr(L.subprocess, "run", fake)
    return cap


def test_which_bin_interroge_l_hote(monkeypatch):
    monkeypatch.setattr(platform, "in_flatpak", lambda env=None: True)
    cap = {}
    def fake(argv, **kw):
        cap["argv"] = argv
        return _Rep2(0)
    monkeypatch.setattr(L.subprocess, "run", fake)
    assert L.which_bin("lutris") is True
    assert cap["argv"][:2] == ["flatpak-spawn", "--host"]
    assert cap["argv"][6] == "lutris"
    monkeypatch.setattr(L.subprocess, "run", lambda *a, **kw: _Rep2(1))
    assert L.which_bin("lutris") is False
    def _ko(*a, **kw):
        raise OSError
    monkeypatch.setattr(L.subprocess, "run", _ko)
    assert L.which_bin("lutris") is False              # pas de faux positif
    monkeypatch.setattr(platform, "in_flatpak", lambda env=None: False)
    monkeypatch.setattr(L.shutil, "which", lambda n: f"/usr/bin/{n}")
    assert L.which_bin("lutris") is True               # natif : inchangé


def test_launcher_command_sous_flatpak(monkeypatch):
    cap = _sable(monkeypatch)
    assert L.launcher_command("lutris") == ["lutris"]          # présent sur l'hôte
    assert L.launcher_command("retroarch") == ["retroarch"]
    cap["rc"] = 1
    assert L.launcher_command("lutris") == ["flatpak", "run", "net.lutris.Lutris"]
    assert L.launcher_command("retroarch") == ["flatpak", "run", "org.libretro.RetroArch"]


def test_uninstall_command_sous_flatpak(monkeypatch):
    cap = _sable(monkeypatch)
    g = {"source": "flatpak", "ext_id": "org.jeu.Jeu"}
    assert L.uninstall_command(g) == ["flatpak", "uninstall", "-y", "org.jeu.Jeu"]
    g = {"source": "lutris", "ext_id": "native:10"}
    assert L.uninstall_command(g) == \
        ["python3", "-c", L.LUTRIS_UNINSTALL.format(gid=10)]   # pythons et lutris hôtes
    cap["rc"] = 1                                              # lutris absent de l'hôte
    assert L.uninstall_command(g) is None
    g = {"source": "lutris", "ext_id": "wine:7"}
    assert L.uninstall_command(g) == \
        ["flatpak", "run", "--command=python3", "net.lutris.Lutris", "-c",
         L.LUTRIS_UNINSTALL.format(gid=7)]                     # plus de gate which("flatpak")
    monkeypatch.setattr(L, "legendary_install_dir", lambda ext: "/home/u/Heroic")
    g = {"source": "heroic", "ext_id": "12345"}
    cap["rc"] = 0
    assert L.uninstall_command(g) == \
        ["LEGENDARY_CONFIG_PATH=/home/u/Heroic", "legendary", "uninstall", "12345"]
    cap["rc"] = 1                                              # legendary absent de l'hôte
    assert L.uninstall_command(g) is None


def test_uninstall_launcher_natifs_inchangés(monkeypatch):
    monkeypatch.setattr(platform, "in_flatpak", lambda env=None: False)
    monkeypatch.setattr(L.shutil, "which", lambda n: None)      # rien d'installé
    cap = {}
    def fake(argv, **kw):
        cap.setdefault("appels", []).append(argv)
        return _Rep2(0)
    monkeypatch.setattr(L.subprocess, "run", fake)
    assert L.uninstall_command({"source": "flatpak", "ext_id": "org.jeu.Jeu"}) == \
        ["flatpak", "uninstall", "-y", "org.jeu.Jeu"]
    assert L.uninstall_command({"source": "lutris", "ext_id": "native:10"}) is None
    assert "appels" not in cap                               # aucun probe hôte en natif
    monkeypatch.setattr(L.shutil, "which", lambda n: f"/usr/bin/{n}")
    assert L.launcher_command("lutris") == ["/usr/bin/lutris"]
