#!/usr/bin/env python3
"""tests/test_cli_notes.py — commande `note` sur le modèle notes multiples."""
import sys

import pytest

from core import cli, db
from core import steamnotes as sn


@pytest.fixture
def con(tmp_path, monkeypatch):
    """Base éphémère + un jeu, la vraie racine Steam jamais touchée."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "j.db")
    monkeypatch.setattr(sn, "STEAM_ROOTS", [])  # pas de compte : la synchro se tait
    con = db.db()
    con.execute("INSERT INTO games (id, name, source, ext_id, notes, notes_modified) "
                "VALUES (1, 'Jeu Test', 'steam', '7', '', 0)")
    con.commit()
    return con


def _note(monkeypatch, capsys, *argv):
    monkeypatch.setattr(sys, "argv", ["ludodex", *argv])
    cli.main()
    return capsys.readouterr().out


def test_note_lecture_imprime_toutes_les_notes(con, monkeypatch, capsys):
    db.create_note(con, 1, "Titre A", "texte a")
    db.create_note(con, 1, "", "note sans titre")
    out = _note(monkeypatch, capsys, "note", "Jeu Test")
    assert "[Titre A]" in out                       # le titre part entre crochets
    assert "texte a" in out and "note sans titre" in out
    assert out.index("[Titre A]") < out.index("texte a") < out.index("note sans titre")


def test_note_ecriture_cree_une_note_locale(con, monkeypatch, capsys):
    db.create_note(con, 1, "Existante", "déjà là")
    out = _note(monkeypatch, capsys, "note", "Jeu Test", "texte neuf")
    assert out.startswith("OK:")
    lignes = db.notes_of_game(con, 1)
    assert len(lignes) == 2                         # l'existe toujours, la neuve s'ajoute
    nouvelle = lignes[-1]
    assert nouvelle["steam_id"] == ""               # locale : elle sera poussée à la synchro
    assert nouvelle["title"] == "" and nouvelle["content"] == "texte neuf"


def test_note_texte_vide_ne_cree_rien(con, monkeypatch, capsys):
    # l'ancien « effacer » n'a plus de sens : une note se supprime depuis l'app
    with pytest.raises(SystemExit):
        _note(monkeypatch, capsys, "note", "Jeu Test", "   ")
    assert db.notes_of_game(con, 1) == []
