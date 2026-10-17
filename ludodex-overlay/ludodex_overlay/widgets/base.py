"""base.py — contrat partagé par tous les widgets de l'overlay."""
import typing

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk


class Widget:
    """Contrat d'un widget : `id` (clé de config), `nom` (clé `tr()`),
    `schema` (réglages exposés au popover — tâche 8) et `fabrique()` (rendu).

    `tick()` est optionnel : il n'est appelé que quand la fenêtre est visible
    (la tâche 7 gère cette vie, ici la fumée le démarre à la construction)."""

    id = ""
    nom = ""
    schema: "list[dict]" = []

    def __init__(self, **options):
        self.options = options

    def fabrique(self) -> Gtk.Widget:
        raise NotImplementedError

    def tick(self) -> None:
        """Mise à jour périodique (vide par défaut : widget statique)."""
