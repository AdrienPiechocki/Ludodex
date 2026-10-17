"""tests/test_horloge.py — format de l'horloge (24 h/12 h, secondes)."""
import datetime

from ludodex_overlay.widgets import horloge

MOMENT = datetime.datetime(2026, 10, 17, 13, 5, 7)


def test_24h_sans_secondes():
    assert horloge.heure_texte(MOMENT, format_24h=True, secondes=False) == "13:05"


def test_24h_avec_secondes():
    assert horloge.heure_texte(MOMENT, format_24h=True, secondes=True) == "13:05:07"


def test_12h_matin_soir():
    assert horloge.heure_texte(MOMENT, format_24h=False, secondes=False) == "1:05 PM"
    assert horloge.heure_texte(datetime.datetime(2026, 1, 1, 0, 5, 0), False, False) == "12:05 AM"
    assert horloge.heure_texte(datetime.datetime(2026, 1, 1, 12, 0, 0), False, False) == "12:00 PM"
