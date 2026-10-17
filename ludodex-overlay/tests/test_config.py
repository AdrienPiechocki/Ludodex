"""tests/test_config.py — configuration JSON globale de l'overlay."""
import pathlib

from ludodex_overlay import config


def test_defauts_pour_horloge():
    d = config.defauts()
    assert d["ecran"] == "auto"
    h = d["widgets"]["horloge"]
    assert h["actif"] is True and h["epingle"] is False
    assert h["pos"] == {"x": 0.03, "y": 0.06}
    assert h["options"] == {"format_24h": True, "secondes": False}


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
    assert d["ecran"] == "auto"


def test_sauver_puis_charger(tmp_path):
    p = tmp_path / "sous" / "c.json"
    config.sauver(p, config.defauts())
    assert config.charger(p) == config.defauts()


def test_chemin_defaut_suit_xdg():
    assert config.chemin_defaut({"XDG_CONFIG_HOME": "/x"}) == pathlib.Path("/x/ludodex-overlay/config.json")
    assert config.chemin_defaut({}) == pathlib.Path.home() / ".config/ludodex-overlay/config.json"


def test_choix_sortie():
    assert config.choix_sortie("auto", ["DP-1", "eDP-2"]) is None
    assert config.choix_sortie("eDP-2", ["DP-1", "eDP-2"]) == "eDP-2"
    assert config.choix_sortie("HDMI-3", ["DP-1", "eDP-2"]) is None
