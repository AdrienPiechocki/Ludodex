#!/usr/bin/env python3
"""tests/test_notes_db.py — modèle `game_notes` : migration et helpers."""
import pytest  # noqa: F401  (fixtures locales possibles plus tard)

from core import db


@pytest.fixture
def con(tmp_path, monkeypatch):
    """Base éphémère : schéma complet, aucun jeu."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "j.db")
    con = db.db()
    yield con
    con.close()


def test_migration_une_ligne_par_jeu(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "j.db")
    con = db.db()
    con.execute("INSERT INTO games (id, name, source, ext_id, notes, notes_modified) "
                "VALUES (1, 'J', 'steam', '7', '# A\n\ntexte', 500)")
    # une base d'avant ne porte pas le marqueur : on le retire pour la réouverture
    con.execute("DELETE FROM settings WHERE key='game_notes_migrated'")
    con.commit()
    con.close()
    con = db.db()                      # réouverture = migration de l'ancienne note unique
    lignes = db.notes_of_game(con, 1)
    assert len(lignes) == 1
    l = lignes[0]
    assert l["steam_id"] == "" and l["title"] == ""
    assert l["content"] == "# A\n\ntexte" and l["modified"] == 500
    mig = con.execute("SELECT migrated FROM game_notes WHERE id=?", (l["id"],)).fetchone()
    assert mig["migrated"] == 1        # la ligne est MARQUÉE : seule elle se déduplique
    con.close()
    con = db.db()                      # migration idempotente : jamais de doublon
    assert len(db.notes_of_game(con, 1)) == 1
    con.close()


def test_jeu_sans_note_sans_ligne(con):
    con.execute("INSERT INTO games (id, name, source, ext_id, notes) VALUES (1, 'J', 'steam', '7', '')")
    con.commit()
    assert db.notes_of_game(con, 1) == []


def test_create_et_set_horodate(con):
    con.execute("INSERT INTO games (id, name, source, ext_id, notes) VALUES (1, 'J', 'steam', '7', '')")
    con.commit()
    rowid = db.create_note(con, 1, "T", "c")
    l = db.notes_of_game(con, 1)[0]
    assert l["id"] == rowid and l["title"] == "T" and l["content"] == "c"
    db.set_note_row(con, rowid, "T2", "c2")
    l = db.notes_of_game(con, 1)[0]
    assert l["title"] == "T2" and l["content"] == "c2" and l["modified"] > 0


def test_tombstone_et_purge(con):
    con.execute("INSERT INTO games (id, name, source, ext_id, notes) VALUES (1, 'J', 'steam', '7', '')")
    con.commit()
    rowid = db.create_note(con, 1, steam_id="abc")
    db.tombstone_note(con, rowid)
    assert db.notes_of_game(con, 1) == []          # plus visible pour l'utilisateur
    assert db.note_row(con, 1, "abc") is not None  # mais la tombe existe encore
    db.purge_tombstones(con, 1, [])            # poussée : l'écrit ne la contient pas
    assert db.note_row(con, 1, "abc") is None      # purgée de partout


def test_vanished_rows(con):
    con.execute("INSERT INTO games (id, name, source, ext_id, notes) VALUES (1, 'J', 'steam', '7', '')")
    con.commit()
    a = db.create_note(con, 1, steam_id="a")
    b = db.create_note(con, 1, steam_id="b")
    db.create_note(con, 1, "locale", "texte")      # ligne locale : jamais « vanished »
    assert db.vanished_rows(con, 1, ["a"]) == [b]
    assert db.vanished_rows(con, 1, ["a", "b"]) == []


def test_supprimer_note_locale_moins_immédiat_sync_devient_tombe(con):
    con.execute("INSERT INTO games (id, name, source, ext_id, notes) VALUES (1, 'J', 'steam', '7', '')")
    con.commit()
    locale = db.create_note(con, 1, "Locale", "texte")
    sync = db.create_note(con, 1, "Sync", "texte", steam_id="abc")
    db.supprimer_note(con, locale)
    # locale : oubliée tout de suite
    assert [r["id"] for r in db.notes_of_game(con, 1)] == [db.note_row(con, 1, "abc")["id"]]
    assert con.execute("SELECT COUNT(*) c FROM game_notes WHERE id=?", (locale,)).fetchone()["c"] == 0
    db.supprimer_note(con, sync)
    assert db.notes_of_game(con, 1) == []          # sync : cachée par la tombe…
    assert db.note_row(con, 1, "abc") is not None  # …mais vivante, servie par la poussée


def test_purge_tombstones_avec_presents(con):
    con.execute("INSERT INTO games (id, name, source, ext_id, notes) VALUES (1, 'J', 'steam', '7', '')")
    con.commit()
    a = db.create_note(con, 1, steam_id="a")
    b = db.create_note(con, 1, steam_id="b")
    db.tombstone_note(con, a)
    db.tombstone_note(con, b)
    db.purge_tombstones(con, 1, ["b"])       # a nulle part dans le carnet connu : oubliée
    assert db.note_row(con, 1, "a") is None
    assert db.note_row(con, 1, "b") is not None
    db.purge_tombstones(con, 1, None)        # carnet inconnu : seules les locales partent
    assert db.note_row(con, 1, "b") is not None
