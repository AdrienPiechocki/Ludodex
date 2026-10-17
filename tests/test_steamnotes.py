#!/usr/bin/env python3
"""tests/test_steamnotes.py — carnet de l'overlay Steam : conversion, fichiers, décision."""
import json
import sqlite3

import pytest

from core import db
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


def test_blocs_ne_collent_jamais():
    # un bloc qui suit une liste ne doit pas s'y coller, et une liste Steam
    # multi-lignes (retours à la ligne dans le BBCode) reste une seule liste
    assert sn.bbcode_en_markdown("[list][*]a[/list][p]b[/p]") == "- a\n\nb"
    assert sn.bbcode_en_markdown("[list]\n[*]un\n[*]deux\n[/list]") == "- un\n- deux"
    # aller-retour d'une liste à lignes blanches (Markdown valide) : stable
    assert sn.bbcode_en_markdown(sn.markdown_en_bbcode("- un\n\n- deux")) == "- un\n\n- deux"


def test_lien_texte_minuscule_survit():
    # le marqueur [lien] généré ne doit PAS être avalé par le nettoyage des balises
    assert sn.bbcode_en_markdown("[url=https://x.fr]lien[/url]") == "[lien](https://x.fr)"
    assert sn.bbcode_en_markdown("[url=https://x.fr]X[/url]") == "[X](https://x.fr)"
    assert sn.bbcode_en_markdown("[url]https://x.fr[/url]") == "[https://x.fr](https://x.fr)"


def test_titres_h1_h3():
    assert sn.bbcode_en_markdown("[h1]Test[/h1]") == "# Test"
    assert sn.bbcode_en_markdown("[h2]Suite[/h2]") == "## Suite"
    assert sn.bbcode_en_markdown("[h3]Détail[/h3]") == "### Détail"
    assert sn.bbcode_en_markdown("[h1]T[/h1][p]x[/p]") == "# T\n\nx"
    assert sn.markdown_en_bbcode("# Test") == "[h1]Test[/h1]"
    assert sn.markdown_en_bbcode("## Test") == "[h2]Test[/h2]"
    assert sn.markdown_en_bbcode("###### Test") == "[h3]Test[/h3]"  # Steam n'a que trois niveaux


def test_strike_comme_l_overlay():
    assert sn.bbcode_en_markdown("[strike]barré[/strike]") == "~~barré~~"
    assert sn.bbcode_en_markdown("[s]barré[/s]") == "~~barré~~"
    assert sn.markdown_en_bbcode("~~barré~~") == "[p][s]barré[/s][/p]"


def test_note_reelle_sulfur_simporte_proprement():
    # contenu exact de l'overlay tel qu'écrit par l'utilisateur le 2026-10-17 :
    # titre [h1], éléments de liste enveloppés dans [p], fermetures [/*],
    # checkbox échappée \[ ] — tout doit devenir du Markdown lisible…
    brut = ("[h1]Test[/h1][p][/p][p]ceci est un test[/p][p][/p]"
            "[list][*][p]\\[ ] [/p][/*][*][p]test 2[/p][/*][/list]")
    md = sn.bbcode_en_markdown(brut)
    assert md == "# Test\n\nceci est un test\n\n- [ ]\n- test 2"
    # …et le cycle de repoussoir complet (titre -> champ `title`, corps -> contenu)
    # doit rester stable et propre (jamais de [/*] ni de [/[i]])
    titre, corps = sn.titre_depuis_markdown(md)
    repousse = sn.markdown_en_bbcode(corps)
    assert "[/*]" not in repousse and "[/[i]]" not in repousse
    assert "\\[ ]" in repousse                    # la checkbox repart échappée, comme Steam
    cycle = sn.notes_steam_en_markdown([{"title": titre, "content": repousse}])
    assert cycle == md


def test_notes_reelles_fusionnent_proprement():
    notes = [
        {"id": "mvcbnnzb", "title": "ceci est un test",
         "content": "[h1]Test[/h1][p][/p][p]ceci est un test[/p]",
         "time_modified": 1792237439},
        {"id": "mvcbr588", "title": "", "content": "[p][/p]", "time_modified": 1792237405},
    ]
    assert sn.notes_steam_en_markdown(notes) == "# ceci est un test\n\n# Test\n\nceci est un test"


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


def test_fichier_malforme_classe_illisible(steam_fake):
    # parseable mais inutilisable : ces fichiers doivent aussi être « illisibles »
    (steam_fake / "notes_1").write_text(json.dumps({"notes": ["boom"]}), encoding="utf-8")
    assert sn.lire_notes(1) is None
    (steam_fake / "notes_2").write_text(
        json.dumps({"notes": [{"title": "", "content": "[p]x[/p]"}]}), encoding="utf-8")
    assert sn.lire_notes(2) is None                  # pas d'`id` : entrée inutilisable
    (steam_fake / "notes_3").write_text(
        json.dumps({"notes": [{"id": "a", "content": 42}]}), encoding="utf-8")
    assert sn.lire_notes(3) is None                  # content non-texte


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


# ---------- décision ----------

def _steam(tid, contenu="[p]s[/p]", titre=""):
    return [{"id": "n1", "title": titre, "content": contenu, "time_modified": tid}]


def test_resoudre_steam_illisible_ne_fait_rien():
    assert sn.resoudre("note", 100, None) == ("rien", "", 0)


def test_resoudre_placeholder_compte_comme_vide():
    # le `[p][/p]` que l'overlay crée à la simple ouverture ne doit PAS écraser
    # la vraie note au premier contact : il pousse la note Ludodex à la place
    assert sn.resoudre("ma note", 0, _steam(999, "[p][/p]")) == ("pousser", "ma note", 0)
    assert sn.resoudre("", 0, _steam(999, "[p][/p]")) == ("rien", "", 0)


def test_resoudre_un_cote_vide_remplit_l_autre():
    assert sn.resoudre("", 0, _steam(500)) == ("importer", "s", 500)
    assert sn.resoudre("ma note", 50, []) == ("pousser", "ma note", 0)


def test_resoudre_le_plus_recent_gagne():
    assert sn.resoudre("ma note", 100, _steam(500)) == ("importer", "s", 500)
    assert sn.resoudre("ma note", 700, _steam(500)) == ("pousser", "ma note", 0)


def test_resoudre_premier_contact_et_egalite_steam_gagnent():
    assert sn.resoudre("ma note", 0, _steam(500)) == ("importer", "s", 500)
    assert sn.resoudre("s", 500, _steam(500)) == ("rien", "", 0)   # même texte : rien à faire


def test_set_note_horodate(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "j.db")
    con = db.db()
    con.execute("INSERT INTO games (id, name, source, ext_id, notes) VALUES (1, 'J', 'steam', '7', '')")
    db.set_note(con, 1, "coucou")
    g = con.execute("SELECT notes, notes_modified FROM games WHERE id=1").fetchone()
    assert g["notes"] == "coucou" and g["notes_modified"] > 0
    con.close()


def test_synchro_desactivee_ne_lit_rien(steam_fake, monkeypatch):
    con = _con_notes()
    con.execute("INSERT INTO games VALUES (1, 'J', 'steam', '7', '', 0)")
    con.execute("INSERT INTO settings (key, value) VALUES ('steam_notes_sync', '0')")
    (steam_fake / "notes_7").write_text(
        json.dumps({"notes": _steam(500)}), encoding="utf-8")
    monkeypatch.setattr(sn, "lire_notes",
                        lambda appid: (_ for _ in ()).throw(AssertionError("lu alors que désactivé")))
    assert sn.synchroniser(con, 1) == "ignore"
    assert con.execute("SELECT notes FROM games WHERE id=1").fetchone()["notes"] == ""


def test_synchroniser_importe_puis_pousse(steam_fake):
    con = _con_notes()
    con.execute("INSERT INTO games VALUES (1, 'J', 'steam', '7', '', 0)")
    (steam_fake / "notes_7").write_text(json.dumps({"notes": _steam(500)}), encoding="utf-8")
    assert sn.synchroniser(con, 1) == "importer"
    g = con.execute("SELECT notes, notes_modified FROM games WHERE id=1").fetchone()
    assert g["notes"] == "s" and g["notes_modified"] == 500   # horodatage Steam hérité
    assert sn.synchroniser(con, 1) == "rien"                  # réouverture : no-op stable

    db.set_note(con, 1, "# Titre\n\nnote modifiée")           # l'utilisateur tape dans Ludodex
    assert sn.synchroniser(con, 1) == "pousser"
    notes = json.loads((steam_fake / "notes_7").read_text(encoding="utf-8"))["notes"]
    assert len(notes) == 1 and notes[0]["title"] == "Titre"
    assert notes[0]["content"] == "[p]note modifiée[/p]"
    g2 = con.execute("SELECT notes_modified FROM games WHERE id=1").fetchone()
    assert notes[0]["time_modified"] == g2["notes_modified"]  # invariant de synchro
    assert sn.synchroniser(con, 1) == "rien"                  # poussé : réouverture = no-op


def test_synchroniser_ignore_les_jeux_non_steam(steam_fake):
    con = _con_notes()
    con.execute("INSERT INTO games VALUES (1, 'J', 'lutris', 'run/jeu', '', 0)")
    assert sn.synchroniser(con, 1) == "ignore"


def test_synchro_fichier_malforme_ne_crash_et_ne_ecrit_rien(steam_fake):
    con = _con_notes()
    con.execute("INSERT INTO games VALUES (1, 'J', 'steam', '1', 'note locale', 100)")
    cible = steam_fake / "notes_1"
    cible.write_text(json.dumps({"notes": ["boom"]}), encoding="utf-8")
    brut = cible.read_bytes()
    assert sn.synchroniser(con, 1) == "ignore"       # jamais de crash de la page Notes
    assert cible.read_bytes() == brut


def test_poussage_depuis_notes_modified_nul_referme_l_invariant(steam_fake):
    # ligne héritée de la migration (colonne à 0) : le poussage doit quand même
    # laisser time_modified == notes_modified, sinon la stabilité saute
    con = _con_notes()
    con.execute("INSERT INTO games VALUES (1, 'J', 'steam', '7', 'note locale', 0)")
    assert sn.synchroniser(con, 1) == "pousser"
    g = con.execute("SELECT notes_modified FROM games WHERE id=1").fetchone()
    assert g["notes_modified"] > 0
    notes = json.loads((steam_fake / "notes_7").read_text(encoding="utf-8"))["notes"]
    assert notes[0]["time_modified"] == g["notes_modified"]
    assert sn.synchroniser(con, 1) == "rien"


def _con_notes():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE games(id INTEGER PRIMARY KEY, name TEXT, source TEXT,
                   ext_id TEXT, notes TEXT, notes_modified INTEGER)""")
    con.execute("CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '')")
    return con
