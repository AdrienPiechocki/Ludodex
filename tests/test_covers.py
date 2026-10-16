#!/usr/bin/env python3
"""tests/test_covers.py — origine des couvertures (sgdb/steam), préservation, nettoyage."""
import sqlite3

import pytest

from core import covers


@pytest.fixture
def couv(tmp_path, monkeypatch):
    """Base jetable + dossier de jaquettes + clé absente (chaque test la pose)."""
    monkeypatch.setattr(covers, "DB_PATH", tmp_path / "ludodex.db")
    (tmp_path / "covers").mkdir()
    monkeypatch.delenv("STEAMGRIDDB_API_KEY", raising=False)
    return tmp_path


def _con_vide():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE games(id INTEGER PRIMARY KEY, name TEXT, source TEXT,
                   ext_id TEXT, cover_path TEXT, cover_fetched INTEGER, sgdb_id INTEGER,
                   cover_src TEXT, custom_cover TEXT, hidden INTEGER, installed INTEGER)""")
    con.execute("CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '')")
    return con


def _insere(con, gid, source="steam", ext_id="10", cover_path="", cover_src="", custom_cover=""):
    con.execute("INSERT INTO games VALUES(?,?,?,?,?,0,0,?,?,0,1)",
                (gid, "Jeu", source, ext_id, cover_path, cover_src, custom_cover))


def _jeu(couv, gid=7, source="steam", ext_id="10", cover_path="", cover_src=""):
    con = _con_vide()
    _insere(con, gid, source=source, ext_id=ext_id, cover_path=cover_path, cover_src=cover_src)
    return con


def test_fetch_cover_note_l_origine_sgdb(couv, monkeypatch):
    monkeypatch.setenv("STEAMGRIDDB_API_KEY", "cle")
    monkeypatch.setattr(covers, "sgdb_get", lambda path, params=None: [{"url": "https://sgdb/gr.png"}])
    con = _jeu(couv)
    g = con.execute("SELECT * FROM games WHERE id=7").fetchone()
    ok = covers.fetch_cover(con, g, fetch=lambda u: b"IMG")
    row = con.execute("SELECT * FROM games WHERE id=7").fetchone()
    assert ok is True
    assert row["cover_src"] == "sgdb"
    assert (covers.covers_dir() / "7.png").exists()


def test_fetch_cover_note_l_origine_steam(couv):
    con = _jeu(couv)                       # sans clé : le CDN de Steam s'en charge
    g = con.execute("SELECT * FROM games WHERE id=7").fetchone()
    ok = covers.fetch_cover(con, g, fetch=lambda u: b"IMG")
    row = con.execute("SELECT * FROM games WHERE id=7").fetchone()
    assert ok is True
    assert row["cover_src"] == "steam"
    assert (covers.covers_dir() / "7.jpg").exists()


def test_echec_sgdb_conserve_la_cover_existante(couv, monkeypatch):
    monkeypatch.setenv("STEAMGRIDDB_API_KEY", "cle")
    monkeypatch.setattr(covers, "sgdb_get", lambda path, params=None: None)
    con = _jeu(couv, source="custom", ext_id="", cover_path="7.jpg")  # pas d'appid Steam
    (covers.covers_dir() / "7.jpg").write_bytes(b"Ancienne")
    g = con.execute("SELECT * FROM games WHERE id=7").fetchone()
    ok = covers.fetch_cover(con, g, fetch=lambda u: b"IMG")
    row = con.execute("SELECT * FROM games WHERE id=7").fetchone()
    assert ok is True                      # une couverture reste affichable
    assert row["cover_path"] == "7.jpg"
    assert (covers.covers_dir() / "7.jpg").read_bytes() == b"Ancienne"
    assert row["cover_src"] == "steam"     # origine désormais connue : CDN
    assert row["cover_fetched"] != 0       # la garde 30 jours jouera


def test_maj_supprime_l_ancienne_extension(couv, monkeypatch):
    monkeypatch.setenv("STEAMGRIDDB_API_KEY", "cle")
    monkeypatch.setattr(covers, "sgdb_get", lambda path, params=None: [{"url": "https://sgdb/gr.png"}])
    con = _jeu(couv)
    (covers.covers_dir() / "7.jpg").write_bytes(b"Ancienne")
    g = con.execute("SELECT * FROM games WHERE id=7").fetchone()
    ok = covers.fetch_cover(con, g, fetch=lambda u: b"IMG")
    row = con.execute("SELECT * FROM games WHERE id=7").fetchone()
    assert ok is True
    assert row["cover_path"] == "7.png"
    assert (covers.covers_dir() / "7.png").exists()
    assert not (covers.covers_dir() / "7.jpg").exists()


def test_upgrade_remplace_une_cover_d_origine_inconnue(couv, monkeypatch):
    monkeypatch.setenv("STEAMGRIDDB_API_KEY", "cle")
    monkeypatch.setattr(covers, "sgdb_get", lambda path, params=None: [{"url": "https://sgdb/gr.png"}])
    con = _con_vide()
    _insere(con, 1, ext_id="11", cover_path="1.jpg")                # héritage : origine inconnue
    _insere(con, 2, ext_id="22", cover_path="2.jpg", cover_src="sgdb")
    _insere(con, 3, ext_id="33", cover_path="3.jpg", cover_src="steam", custom_cover="c.png")
    for n in (1, 2, 3):
        (covers.covers_dir() / f"{n}.jpg").write_bytes(f"Ancienne{n}".encode())
    urls = []
    covers.fetch_missing_covers(con, delay=0, fetch=lambda u: urls.append(u) or b"IMG")
    a, b, c = (con.execute("SELECT * FROM games WHERE id=?", (n,)).fetchone() for n in (1, 2, 3))
    assert a["cover_src"] == "sgdb" and a["cover_path"] == "1.png"
    assert (covers.covers_dir() / "1.png").exists()
    assert not (covers.covers_dir() / "1.jpg").exists()
    # déjà SGDB, ou couverte par la perso : intacts, aucune requête pour eux
    assert b["cover_src"] == "sgdb" and (covers.covers_dir() / "2.jpg").read_bytes() == b"Ancienne2"
    assert c["cover_src"] == "steam" and (covers.covers_dir() / "3.jpg").read_bytes() == b"Ancienne3"
    assert urls == ["https://sgdb/gr.png"]


def test_sans_cle_les_covers_existantes_ne_sont_pas_rechargees(couv):
    con = _con_vide()
    _insere(con, 1, ext_id="11", cover_path="1.jpg", cover_src="steam")
    (covers.covers_dir() / "1.jpg").write_bytes(b"Ancienne")
    urls = []
    n = covers.fetch_missing_covers(con, delay=0, fetch=lambda u: urls.append(u) or b"IMG")
    g = con.execute("SELECT * FROM games WHERE id=1").fetchone()
    assert n == 0
    assert urls == []
    assert g["cover_src"] == "steam"
    assert (covers.covers_dir() / "1.jpg").read_bytes() == b"Ancienne"
