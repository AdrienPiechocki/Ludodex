import sqlite3

import pytest

from core import migrations as M
from core.db import SCHEMA


def _con(path):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    return con


def test_fresh_db_reaches_latest(tmp_path):
    p = tmp_path / "games.db"
    con = _con(p)
    assert M.migrate(con, p) == [n for n, _ in M.MIGRATIONS]
    assert M.get_version(con) == M.LATEST
    assert {"hltb_main", "hltb_id"} <= M.columns(con, "games")
    assert "state" in M.columns(con, "achievements")
    assert not list(tmp_path.glob("games.db.pre-v*"))      # base neuve : rien à sauvegarder


def test_idempotent(tmp_path):
    p = tmp_path / "games.db"
    con = _con(p)
    M.migrate(con, p)
    assert M.migrate(con, p) == []


def test_legacy_db_is_upgraded_and_backed_up(tmp_path):
    """Base d'avant le versionnage : schéma d'origine, user_version = 0, données présentes."""
    p = tmp_path / "games.db"
    con = _con(p)
    for stmt in M.statements(SCHEMA):
        con.execute(stmt)
    con.execute("INSERT INTO games (source, ext_id, name) VALUES ('steam', '1', 'Old game')")
    con.commit()
    assert M.get_version(con) == 0
    assert M.migrate(con, p)[0] == 1
    assert con.execute("SELECT name FROM games").fetchone()["name"] == "Old game"
    assert {"cover_src", "hltb_main", "playtime_min"} <= M.columns(con, "games")
    assert len(list(tmp_path.glob("games.db.pre-v0-*"))) == 1


def test_failed_migration_rolls_back(tmp_path, monkeypatch):
    p = tmp_path / "games.db"
    con = _con(p)
    M.migrate(con, p)

    def boom(c):
        c.execute("ALTER TABLE games ADD COLUMN zzz TEXT")
        raise RuntimeError("boom")

    monkeypatch.setattr(M, "MIGRATIONS", M.MIGRATIONS + [(99, boom)])
    monkeypatch.setattr(M, "LATEST", 99)
    with pytest.raises(RuntimeError):
        M.migrate(con, p)
    assert M.get_version(con) == 4
    assert "zzz" not in M.columns(con, "games")


def test_newer_db_refused(tmp_path):
    p = tmp_path / "games.db"
    con = _con(p)
    con.execute("PRAGMA user_version = 999")
    with pytest.raises(M.DatabaseTooNew):
        M.migrate(con, p)


def test_backups_pruned(tmp_path):
    p = tmp_path / "games.db"
    con = _con(p)
    con.execute("CREATE TABLE t (x)")
    for i in range(5):
        M._backup(con, p, i)
    assert len(list(tmp_path.glob("games.db.pre-v*"))) == 3
