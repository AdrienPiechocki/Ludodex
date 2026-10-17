#!/usr/bin/env python3
"""tests/test_steamnotes.py — carnet de l'overlay Steam : conversion, fichiers, décision."""
import json

import pytest

from core import steamnotes as sn


def test_paires_de_base():
    assert sn.bbcode_en_markdown("[p]Salut [b]toi[/b][/p]") == "Salut **toi**"
    assert sn.markdown_en_bbcode("Salut **toi**") == "[p]Salut [b]toi[/b][/p]"
    assert sn.bbcode_en_markdown("[i]a[/i] [u]b[/u] [s]c[/s]") == "*a* __b__ ~~c~~"
    assert sn.markdown_en_bbcode("*a* __b__ ~~c~~") == "[p][i]a[/i] [u]b[/u] [s]c[/s][/p]"
    assert sn.bbcode_en_markdown("[p]a[/p][p]b[/p]") == "a\n\nb"
    assert sn.bbcode_en_markdown("[p][/p]") == ""


def test_listes_et_liens():
    assert sn.bbcode_en_markdown("[list][*]un[*]deux[/list]") == "- un\n- deux"
    assert sn.bbcode_en_markdown("[olist][*]un[*]deux[/olist]") == "1. un\n2. deux"
    assert sn.markdown_en_bbcode("- un\n- deux") == "[list][*]un[*]deux[/list]"
    assert sn.markdown_en_bbcode("1. un\n2. deux") == "[olist][*]un[*]deux[/olist]"
    assert sn.bbcode_en_markdown("[url=https://x.fr]X[/url]") == "[X](https://x.fr)"
    assert sn.markdown_en_bbcode("[X](https://x.fr)") == "[p][url=https://x.fr]X[/url][/p]"
    assert sn.bbcode_en_markdown("[url]https://x.fr[/url]") == "[https://x.fr](https://x.fr)"


def test_tags_inconnus_gardent_le_texte():
    assert sn.bbcode_en_markdown("[zz]gardi[/zz]") == "gardi"
    assert sn.bbcode_en_markdown("[b]gardi") == "gardi"   # balise non fermée : texte gardé


def test_titre_premier_heading_sort_du_corps():
    assert sn.titre_depuis_markdown("# Titre\n\ncorps") == ("Titre", "corps")
    assert sn.titre_depuis_markdown("sans titre") == ("", "sans titre")
    assert sn.titre_depuis_markdown("") == ("", "")


def test_fusion_de_notes_avec_titres():
    notes = [{"title": "A", "content": "[p]a[/p]"}, {"title": "", "content": "[p]b[/p]"}]
    assert sn.notes_steam_en_markdown(notes) == "# A\n\na\n\nb"
    assert sn.notes_steam_en_markdown([]) == ""


# ---------- fichiers overlay ----------

@pytest.fixture
def steam_fake(tmp_path, monkeypatch):
    """Faux dossier Steam : un compte 123 avec carnet, un 999 sans carnet."""
    racine = tmp_path / "Steam"
    (racine / "steamapps").mkdir(parents=True)
    base = tmp_path / "userdata"
    (base / "123" / "2371090" / "remote").mkdir(parents=True)
    (base / "999").mkdir()
    monkeypatch.setattr(sn, "STEAM_ROOTS", [racine])
    return base / "123" / "2371090" / "remote"


def test_compte_notes_retient_le_dossier_avec_remote(steam_fake):
    assert sn.compte_notes() == steam_fake.parent.parent   # userdata/<id>, pas le sous-dossier


def test_lire_notes_fichier_absent(steam_fake):
    assert sn.lire_notes(999) == []


def test_fichier_casse_ignore(steam_fake):
    cible = steam_fake / "notes_1"
    cible.write_bytes(b"{pas du json")
    assert sn.lire_notes(1) is None
    assert cible.read_bytes() == b"{pas du json"      # jamais écrasé


def test_ecriture_atomique(steam_fake):
    (steam_fake / "notes_7").write_text(
        json.dumps({"notes": [{"id": "vieux", "appid": 7, "ordinal": 0,
                               "time_created": 1500000000, "time_modified": 1500000000,
                               "title": "", "content": "[p]a[/p]"}]}), encoding="utf-8")
    sn.ecrire_notes(7, "vieux", "Titre", "[p]x[/p]", 1700000000)
    brut = (steam_fake / "notes_7").read_text(encoding="utf-8")
    notes = json.loads(brut)["notes"]
    assert len(notes) == 1                                   # les autres entrées sont remplacées
    assert notes[0]["id"] == "vieux"
    assert notes[0]["time_created"] == 1500000000            # l'histoire de la note survive
    assert notes[0]["time_modified"] == 1700000000
    assert notes[0]["title"] == "Titre" and notes[0]["content"] == "[p]x[/p]"
    assert list(steam_fake.glob("*.tmp")) == []              # aucun déchet d'atomique


def test_poussee_deux_fois_ne_change_rien(steam_fake):
    sn.ecrire_notes(7, "memeid", "", "[p]x[/p]", 1700000000)
    premier = (steam_fake / "notes_7").read_bytes()
    sn.ecrire_notes(7, "memeid", "", "[p]x[/p]", 1700000000)
    assert (steam_fake / "notes_7").read_bytes() == premier
