"""horloge.py — widget horloge : texte formaté et classe Widget.

La conversion est pure (pas de `strftime`) : le rendu ne dépend donc d'aucune
locale, les tests sont déterministes et le format 12 h reste prévisible.
"""
import datetime, typing

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from .base import Widget


def heure_texte(moment: datetime.datetime, format_24h: bool, secondes: bool) -> str:
    """Heure de `moment` : « 13:05 », « 13:05:07 », « 1:05 PM », « 12:05 AM »."""
    h, m, s = moment.hour, moment.minute, moment.second
    base = f"{h:02d}:{m:02d}"
    if not format_24h:
        h12 = h % 12 or 12
        base = f"{h12}:{m:02d}"
    if secondes:
        base += f":{s:02d}"
    if not format_24h:
        base += " PM" if h >= 12 else " AM"
    return base


class Horloge(Widget):
    """Horloge digitale, réglable 24 h/12 h et secondes."""

    id = "horloge"
    nom = "Clock"
    schema = [
        {"cle": "format_24h", "type": "bool", "label": "24-hour format"},
        {"cle": "secondes", "type": "bool", "label": "Show seconds"},
    ]

    def __init__(self, **options):
        super().__init__(**options)
        self.format_24h = bool(options.get("format_24h", True))
        self.secondes = bool(options.get("secondes", False))
        self._label: "Gtk.Label | None" = None

    def fabrique(self) -> Gtk.Widget:
        self._label = Gtk.Label(css_classes=["clock"])
        self.tick()  # affiche l'heure dès l'apparition
        return self._label

    def tick(self) -> None:
        if self._label is not None:
            self._label.set_text(
                heure_texte(datetime.datetime.now(), self.format_24h, self.secondes))
