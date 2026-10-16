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
        cap["argv"] = argv
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
        class _R:
            stdout = "42\n7\n42\n"
        return _R()
    monkeypatch.setattr(platform.subprocess, "run", fake)
    motifs = ["/home/u/jeu/dossier"]
    assert platform.host_pids_sous(motifs) == [7, 42]      # dédoublonné, trié
    assert cap["argv"][:2] == ["flatpak-spawn", "--host"]
    assert cap["argv"][6:] == motifs
    cap.clear()
    assert platform.host_pids_sous([]) == []               # sans appel inutile
    assert "argv" not in cap


def test_host_pids_sous_hote_injoignable_renvoie_vide(monkeypatch):
    def _ko(*a, **kw):
        raise OSError
    monkeypatch.setattr(platform.subprocess, "run", _ko)
    assert platform.host_pids_sous(["/home/u/jeu/dossier"]) == []
