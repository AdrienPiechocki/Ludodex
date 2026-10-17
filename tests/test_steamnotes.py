#!/usr/bin/env python3
"""tests/test_steamnotes.py — carnet de l'overlay Steam : conversion, fichiers, synchronisation."""
import json

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
    # …et le cycle de repoussoir complet doit rester stable et propre (jamais de
    # [/*] ni de [/[i]]) : le titre vit dans le champ `title` de la note, le
    # corps markdown repart tel quel en BBCode
    repousse = sn.markdown_en_bbcode(md)
    assert "[/*]" not in repousse and "[/[i]]" not in repousse
    assert "\\[ ]" in repousse                    # la checkbox repart échappée, comme Steam
    assert sn.bbcode_en_markdown(repousse) == md


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


def test_ecrire_notes_multi_ecrit_le_fichier_entier(steam_fake):
    (steam_fake / "notes_7").write_text(
        json.dumps({"notes": [{"id": "vieux", "appid": 7, "ordinal": 3,
                               "time_created": 1500000000, "time_modified": 1500000000,
                               "title": "", "content": "[p]a[/p]"}]}), encoding="utf-8")
    sn.ecrire_notes_multi(7, [
        {"id": "vieux", "appid": 7, "ordinal": 3,
         "time_created": 1500000000, "time_modified": 1700000000,
         "title": "Titre", "content": "[p]x[/p]"},
        {"id": "neuf", "appid": 7, "ordinal": 4,
         "time_created": 1700000001, "time_modified": 1700000001,
         "title": "", "content": "[p]n[/p]"}])
    notes = json.loads((steam_fake / "notes_7").read_text(encoding="utf-8"))["notes"]
    assert [n["id"] for n in notes] == ["vieux", "neuf"]     # l'ordre du carnet suit la liste
    assert notes[0]["time_created"] == 1500000000            # l'histoire de la note survive
    assert notes[0]["time_modified"] == 1700000000
    assert list(steam_fake.glob("*.tmp")) == []              # aucun déchet d'atomique


def test_set_note_horodate(tmp_path, monkeypatch):
    # les colonnes héritées restent stables (gelées : la migration seule les lit)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "j.db")
    con = db.db()
    con.execute("INSERT INTO games (id, name, source, ext_id, notes) VALUES (1, 'J', 'steam', '7', '')")
    db.set_note(con, 1, "coucou")
    g = con.execute("SELECT notes, notes_modified FROM games WHERE id=1").fetchone()
    assert g["notes"] == "coucou" and g["notes_modified"] > 0
    con.close()


# ---------- synchronisation ----------

@pytest.fixture
def con(tmp_path, monkeypatch):
    """Base éphémère réelle : jeu 1 = Steam appid 7, synchro activée."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "j.db")
    con = db.db()
    con.execute("INSERT INTO games (id, name, source, ext_id, notes, notes_modified) "
                "VALUES (1, 'J', 'steam', '7', '', 0)")
    con.commit()
    return con


def _fichier(steam_fake, appid, notes):
    (steam_fake / f"notes_{appid}").write_text(
        json.dumps({"notes": notes}), encoding="utf-8")


def _lire(steam_fake, appid):
    return json.loads((steam_fake / f"notes_{appid}").read_text(encoding="utf-8"))["notes"]


def _steam(tid, contenu="[p]s[/p]", titre=""):
    return [{"id": "n1", "title": titre, "content": contenu, "time_modified": tid}]


def test_desactive_ignore(steam_fake, con, monkeypatch):
    con.execute("INSERT INTO settings (key, value) VALUES ('steam_notes_sync', '0')")
    _fichier(steam_fake, 7, _steam(500))
    monkeypatch.setattr(sn, "lire_notes",
                        lambda appid: (_ for _ in ()).throw(AssertionError("lu alors que désactivé")))
    assert sn.synchroniser(con, 1) == "ignore"
    assert db.notes_of_game(con, 1) == []


def test_synchroniser_ignore_les_jeux_non_steam(steam_fake, con):
    con.execute("UPDATE games SET source='lutris', ext_id='run/jeu' WHERE id=1")
    assert sn.synchroniser(con, 1) == "ignore"


def test_synchro_fichier_malforme_ne_crash_et_ne_ecrit_rien(steam_fake, con):
    cible = steam_fake / "notes_7"
    cible.write_text(json.dumps({"notes": ["boom"]}), encoding="utf-8")
    brut = cible.read_bytes()
    assert sn.synchroniser(con, 1) == "ignore"       # jamais de crash de la page Notes
    assert cible.read_bytes() == brut


def test_importe_une_ligne_par_note(steam_fake, con):
    _fichier(steam_fake, 7, [
        {"id": "n1", "title": "A", "content": "[p]a[/p]", "time_modified": 500},
        {"id": "n2", "title": "", "content": "[h1]T[/h1][p]b[/p]", "time_modified": 600},
        {"id": "n3", "title": "", "content": "[p][/p]", "time_modified": 700},  # placeholder
    ])
    assert sn.synchroniser(con, 1) == "importer"
    lignes = db.notes_of_game(con, 1)
    assert [(l["steam_id"], l["title"], l["content"], l["modified"]) for l in lignes] == [
        ("n1", "A", "a", 500),
        ("n2", "", "# T\n\nb", 600),
    ]                                            # le placeholder vides n'est pas importé
    assert sn.synchroniser(con, 1) == "rien"     # réouverture : no-op stable


def test_arbitrage_par_note_deux_sens(steam_fake, con):
    _fichier(steam_fake, 7, [
        {"id": "n1", "title": "SteamA", "content": "[p]steam a[/p]", "time_modified": 500},
        {"id": "n2", "title": "SteamB", "content": "[p]b[/p]", "time_modified": 900},
    ])
    db.create_note(con, 1, "LocalA", "note locale A", steam_id="n1", modified=700)
    db.create_note(con, 1, "AncienB", "ancien", steam_id="n2", modified=300)
    assert sn.synchroniser(con, 1) == "pousser"
    notes = {n["id"]: n for n in _lire(steam_fake, 7)}
    # plus récent côté Ludodex (n1) : poussé
    assert (notes["n1"]["title"], notes["n1"]["content"], notes["n1"]["time_modified"]) == (
        "LocalA", "[p]note locale A[/p]", 700)
    # plus récent côté Steam (n2) : importé, le fichier ne bouge pas
    assert (notes["n2"]["title"], notes["n2"]["content"], notes["n2"]["time_modified"]) == (
        "SteamB", "[p]b[/p]", 900)
    l2 = db.note_row(con, 1, "n2")
    assert (l2["title"], l2["content"], l2["modified"]) == ("SteamB", "b", 900)
    assert sn.synchroniser(con, 1) == "rien"


def test_poussee_idempotence(steam_fake, con):
    _fichier(steam_fake, 7, _steam(500))
    assert sn.synchroniser(con, 1) == "importer"
    db.set_note_row(con, db.note_row(con, 1, "n1")["id"], "Titre", "texte modifié")
    assert sn.synchroniser(con, 1) == "pousser"
    notes = _lire(steam_fake, 7)
    assert len(notes) == 1 and notes[0]["title"] == "Titre"
    assert notes[0]["content"] == "[p]texte modifié[/p]"
    assert notes[0]["time_modified"] == db.note_row(con, 1, "n1")["modified"]  # invariant
    premier = (steam_fake / "notes_7").read_bytes()
    assert sn.synchroniser(con, 1) == "rien"       # la seconde poussée n'existe pas
    assert (steam_fake / "notes_7").read_bytes() == premier


def test_note_locale_poussee_ne_se_duplique_pas(steam_fake, con):
    _fichier(steam_fake, 7, _steam(500))
    db.create_note(con, 1, "Locale", "note neuve")     # steam_id vide : locale
    assert sn.synchroniser(con, 1) == "pousser"
    assert len(_lire(steam_fake, 7)) == 2              # n1 + la note locale
    assert all(l["steam_id"] for l in db.notes_of_game(con, 1))  # la locale a son id overlay
    assert sn.synchroniser(con, 1) == "rien"           # seconde poussée : aucune copie en plus
    assert len(_lire(steam_fake, 7)) == 2


def test_poussee_conserve_les_notes_inconnues(steam_fake, con):
    # le placeholder que l'overlay crée n'est pas importé en base, mais la
    # poussée ne doit jamais le supprimer du fichier
    _fichier(steam_fake, 7, [
        {"id": "vide", "title": "", "content": "[p][/p]", "time_modified": 1}])
    db.create_note(con, 1, "Locale", "texte")
    assert sn.synchroniser(con, 1) == "pousser"
    notes = _lire(steam_fake, 7)
    assert len(notes) == 2
    assert notes[0]["id"] == "vide" and notes[0]["content"] == "[p][/p]"  # jamais touchée


def test_poussee_met_a_jour_sans_perdre_histoire(steam_fake, con):
    _fichier(steam_fake, 7, [
        {"id": "n1", "appid": 7, "ordinal": 5, "time_created": 1500000000,
         "time_modified": 500, "title": "Vieux", "content": "[p]v[/p]",
         "champ_inconnu": "kiwi"}])
    db.create_note(con, 1, "Neuf", "nouveau texte", steam_id="n1", modified=999999)
    assert sn.synchroniser(con, 1) == "pousser"
    note = _lire(steam_fake, 7)[0]
    assert note["title"] == "Neuf" and note["content"] == "[p]nouveau texte[/p]"
    assert note["time_modified"] == 999999               # invariant de synchro
    assert note["ordinal"] == 5 and note["time_created"] == 1500000000
    assert note["champ_inconnu"] == "kiwi"               # l'inconnu de Steam survit


def test_suppression_retire_du_fichier_puis_purge(steam_fake, con):
    _fichier(steam_fake, 7, [
        {"id": "n1", "title": "", "content": "[p]a[/p]", "time_modified": 500},
        {"id": "n2", "title": "", "content": "[p]b[/p]", "time_modified": 500}])
    assert sn.synchroniser(con, 1) == "importer"
    db.tombstone_note(con, db.note_row(con, 1, "n1")["id"])
    assert sn.synchroniser(con, 1) == "pousser"
    assert [n["id"] for n in _lire(steam_fake, 7)] == ["n2"]
    assert db.note_row(con, 1, "n1") is None             # tombe purgée APRÈS l'écriture
    assert db.note_row(con, 1, "n2") is not None


def test_echec_poussage_conserve_la_tombe(steam_fake, con, monkeypatch):
    _fichier(steam_fake, 7, [
        {"id": "n1", "title": "", "content": "[p]a[/p]", "time_modified": 500},
        {"id": "n2", "title": "", "content": "[p]b[/p]", "time_modified": 500}])
    assert sn.synchroniser(con, 1) == "importer"
    db.tombstone_note(con, db.note_row(con, 1, "n1")["id"])
    racine = steam_fake.parents[3] / "Steam"
    monkeypatch.setattr(sn, "STEAM_ROOTS", [])          # plus de compte : poussage impossible
    assert sn.synchroniser(con, 1) == "ignore"
    assert db.note_row(con, 1, "n1") is not None         # la tombe survit à l'échec
    monkeypatch.setattr(sn, "STEAM_ROOTS", [racine])     # le compte revient
    assert sn.synchroniser(con, 1) == "pousser"
    assert [n["id"] for n in _lire(steam_fake, 7)] == ["n2"]
    assert db.note_row(con, 1, "n1") is None


def test_fichier_absent_ne_supprime_rien(steam_fake, con):
    db.create_note(con, 1, "A", "a", steam_id="a", modified=500)
    db.create_note(con, 1, "B", "b", steam_id="b", modified=500)
    assert sn.synchroniser(con, 1) == "rien"   # pas de fichier : ni miroir, ni poussée
    assert len(db.notes_of_game(con, 1)) == 2


def test_disparition_cote_steam_supprime_localement(steam_fake, con):
    _fichier(steam_fake, 7, [
        {"id": "n2", "title": "", "content": "[p]b[/p]", "time_modified": 500}])
    db.create_note(con, 1, "Reste", "reste", steam_id="a", modified=500)  # retirée du carnet
    assert sn.synchroniser(con, 1) == "importer"
    assert db.note_row(con, 1, "a") is None              # miroir : supprimée localement
    assert db.note_row(con, 1, "n2") is not None


def test_dedup_migration_stricte(steam_fake, con):
    _fichier(steam_fake, 7, [{"id": "n1", "title": "T", "content": "[p]s[/p]", "time_modified": 500}])
    # ligne migrée dont le texte est EXACTEMENT la fusion du fichier : retirée
    db.create_note(con, 1, "", "# T\n\ns", modified=999)
    assert sn.synchroniser(con, 1) == "importer"
    lignes = db.notes_of_game(con, 1)
    assert len(lignes) == 1 and lignes[0]["steam_id"] == "n1"
    # une ligne héritée modifiée entre-temps (texte différent) : conservée
    con.execute("INSERT INTO games (id, name, source, ext_id, notes, notes_modified) "
                "VALUES (2, 'K', 'steam', '8', '', 0)")
    con.commit()
    _fichier(steam_fake, 8, [{"id": "m1", "title": "T", "content": "[p]s[/p]", "time_modified": 500}])
    db.create_note(con, 2, "", "texte modifié depuis", modified=999)
    assert sn.synchroniser(con, 2) == "pousser"
    assert len(db.notes_of_game(con, 2)) == 2            # la ligne modifiée n'est PAS retirée
