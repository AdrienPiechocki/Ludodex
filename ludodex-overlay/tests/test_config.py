"""tests/test_config.py — configuration JSON globale de l'overlay."""
import pathlib

from ludodex_overlay import config


def test_defauts_pour_horloge():
    d = config.defauts()
    h = d["widgets"]["horloge"]
    assert h["actif"] is True and h["epingle"] is False
    assert h["pos"] == {"x": 0.03, "y": 0.06}
    assert h["options"] == {"format_24h": True, "secondes": False}
    assert set(d) == {"widgets"}  # écran et souris ne sont plus réglés ici


def test_charger_fichier_absent_renvoie_les_defauts(tmp_path):
    assert config.charger(tmp_path / "manquant.json") == config.defauts()


def test_charger_json_casse_renvoie_les_defauts(tmp_path):
    p = tmp_path / "c.json"
    p.write_text("{pas du json")
    assert config.charger(p) == config.defauts()


def test_charger_fusionne_les_defauts(tmp_path):
    p = tmp_path / "c.json"
    p.write_text('{"widgets": {"horloge": {"epingle": true}}}')
    d = config.charger(p)
    assert d["widgets"]["horloge"]["epingle"] is True
    assert d["widgets"]["horloge"]["actif"] is True      # clé absente → défaut
    assert set(d) == {"widgets"}


def test_sauver_puis_charger(tmp_path):
    p = tmp_path / "sous" / "c.json"
    config.sauver(p, config.defauts())
    assert config.charger(p) == config.defauts()


def test_chemin_defaut_suit_xdg():
    assert config.chemin_defaut({"XDG_CONFIG_HOME": "/x"}) == pathlib.Path("/x/ludodex-overlay/config.json")
    assert config.chemin_defaut({}) == pathlib.Path.home() / ".config/ludodex-overlay/config.json"


def test_charger_rejette_les_types_errones(tmp_path):
    """Config JSON éditée à la main : `null`, chaîne à la place d'un nombre,
    clé inconnue — jamais de plantage au démarrage, le défaut tient."""
    p = tmp_path / "c.json"
    p.write_text('{"ecran": 5, "truc": "inconnu", '
                 '"widgets": {"horloge": {"options": null, "transparence": "plein", "epingle": 1}}}')
    d = config.charger(p)
    assert "ecran" not in d and "truc" not in d
    h = d["widgets"]["horloge"]
    assert h["options"] == {"format_24h": True, "secondes": False}
    assert h["transparence"] == 0.0
    assert h["epingle"] is False


def test_epingle_actif_ignore_les_widgets_desactives():
    """Mode « pinned » : seul un widget ACTIF et épinglé compte.

    Un widget épinglé puis désactivé ne doit pas détourner la
    fermeture (bug du 2026-10-18 : ✕ Fermer envoyait en « pinned » à
    vide — l'overlay ne se fermait plus jamais)."""
    conf = {"widgets": {"horloge": {"actif": False, "epingle": True}}}
    assert config.epingle_actif(conf) is False
    conf["widgets"]["horloge"]["actif"] = True
    assert config.epingle_actif(conf) is True
    assert config.epingle_actif({"widgets": {"horloge": {"actif": True}}}) is False
