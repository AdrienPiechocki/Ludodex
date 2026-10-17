#!/usr/bin/env python3
"""tests/test_launch.py — injection du hook LD_PRELOAD au lancement.

Le shim ne vaut que pour un processus de JEU ; injecté dans un passe-plat
URI (`xdg-open itch://…`), il tue le lancement — le client itch (Electron)
ne démarre plus avec un LD_PRELOAD étranger (test A/B du 2026-10-18 : sans
hook le client s'ouvre en 2 s, avec hook rien ne se passe)."""
import sqlite3

from core import launch as L

JEU_ITCH = {"id": 1, "name": "Cave", "source": "itch", "ext_id": "cave1"}
JEU_FLATPAK = {"id": 2, "name": "Appli", "source": "flatpak", "ext_id": "org.test.App"}


def _con():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    con.execute("CREATE TABLE games (id INTEGER PRIMARY KEY, name TEXT, "
                "status TEXT DEFAULT '', last_played INTEGER DEFAULT 0)")
    con.execute("CREATE TABLE sessions (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "game_id INTEGER, started INTEGER, ended INTEGER DEFAULT 0, "
                "minutes INTEGER DEFAULT 0)")
    con.execute("INSERT INTO games (id, name) VALUES (1, 'Cave'), (2, 'Appli')")
    con.execute("INSERT INTO settings VALUES ('overlay', '1')")
    con.commit()
    return con


def _instrumente(monkeypatch):
    appels = {"hook": [], "spawn": [], "lancer": []}
    monkeypatch.setattr(L.overlay, "preparer_hook",
                        lambda env=None: appels["hook"].append(1) or "/x/shim.so")
    monkeypatch.setattr(L.overlay, "lancer_overlay",
                        lambda *a, **kw: appels["lancer"].append((a, kw)) or True)
    monkeypatch.setattr(L.subprocess, "Popen",
                        lambda cmd, **kw: appels["spawn"].append(cmd) or object())
    monkeypatch.setattr(L, "motifs_jeu", lambda g: [])  # pas de watch /proc ici
    return appels


def test_lancement_par_uri_ne_charge_pas_le_hook(monkeypatch):
    """`xdg-open` est un passe-plat : le jeu sera lancé par son client
    (itch/Steam/Heroic) — le shim ne lui sert à rien et empêche même le
    client de démarrer. Aucune injection sur un argv contenant `://`."""
    appels = _instrumente(monkeypatch)
    assert L.launch(_con(), dict(JEU_ITCH), on_exit=lambda sid: None)
    assert appels["hook"] == []
    assert appels["spawn"][0] == ["xdg-open", "itch://caves/cave1/launch"]


def test_lancement_direct_charge_le_hook(monkeypatch):
    """Un vrai binaire d'application/jeu reçoit le shim (principe Steam) :
    la libération de souris reste de mise là où Ludodex tient le jeu."""
    appels = _instrumente(monkeypatch)
    assert L.launch(_con(), dict(JEU_FLATPAK), on_exit=lambda sid: None)
    assert appels["hook"] == [1]
    assert appels["spawn"][0][:2] == ["env", "LD_PRELOAD=/x/shim.so"]
