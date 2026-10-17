#!/usr/bin/env python3
"""tests/test_steamnotes.py — carnet de l'overlay Steam : conversion, fichiers, décision."""
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
