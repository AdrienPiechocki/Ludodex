#!/usr/bin/env python3
"""main.py — point d'entrée de l'overlay : fenêtre layer-shell et instance unique.

Un seul `Gtk.Application` par machine (application_id fixe) : le second
lancement met à jour `--game` sans ouvrir de deuxième fenêtre.

Modes de la fenêtre (complétés tâche 7) :
- « hidden »   : défaut, fenêtre invisible, en attente de Shift+Tab ;
- « overlay »  : voile plein écran + panneau (tâche 7) ;
- « pinned »   : seuls les widgets épinglés restent visibles (tâche 7).
"""
import os, sys

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
from gi.repository import Gio, GLib, Gtk, Gtk4LayerShell

from .i18n import tr

ID = "io.github.AdrienPiechocki.LudodexOverlay"


def _texte(morceau) -> str:
    """Un élément des arguments `aay` de GApplication, quel que soit son type
    d'emballage (bytes, str ou liste d'entiers)."""
    if isinstance(morceau, (bytes, bytearray)):
        return bytes(morceau).decode("utf-8", "replace")
    if isinstance(morceau, str):
        return morceau
    return bytes(morceau).decode("utf-8", "replace")


def notifier(resume: str, corps: str) -> None:
    """Notification bureau (org.freedesktop.Notifications) — best-effort :
    une notification ratée ne plante jamais l'overlay."""
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        bus.call_sync(
            "org.freedesktop.Notifications",
            "/org/freedesktop/notifications",
            "org.freedesktop.Notifications",
            "Notify",
            GLib.Variant("(susssasa{sv}i)",
                         (ID, 0, "", resume, corps, [], {}, -1)),
            None, Gio.DBusCallFlags.NONE, -1, None)
    except Exception:  # noqa: BLE001 — best-effort assumé
        pass


class Fenetre(Gtk.ApplicationWindow):
    """Fenêtre layer-shell unique : un seul voile pour tous les widgets."""

    def __init__(self, app: Gtk.Application):
        super().__init__(application=app, title=tr("Ludodex Overlay"))
        Gtk4LayerShell.init_for_window(self)
        Gtk4LayerShell.set_layer(self, Gtk4LayerShell.Layer.OVERLAY)
        Gtk4LayerShell.set_namespace(self, "ludodex-overlay")
        for bord in (Gtk4LayerShell.Edge.TOP, Gtk4LayerShell.Edge.RIGHT,
                     Gtk4LayerShell.Edge.BOTTOM, Gtk4LayerShell.Edge.LEFT):
            Gtk4LayerShell.set_anchor(self, bord, True)
        self._mode = "hidden"
        self.set_child(Gtk.Label(label=tr("Ludodex Overlay")))
        self.hide()

    def definer_mode(self, mode: str) -> None:
        """« hidden » → cachée ; sinon → visible (voile et clavier : tâche 7)."""
        self._mode = mode
        if mode == "hidden":
            self.hide()
        else:
            self.show()


class OverlayApp(Gtk.Application):
    """Application unique : lit `--game NOM` et `--demo`, puis active la fenêtre."""

    def __init__(self):
        super().__init__(application_id=ID,
                         flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.nom_jeu = ""
        self.demo = False
        self.fenetre: "Fenetre | None" = None

    def do_command_line(self, commande) -> int:
        # `commande` est une GApplicationCommandLine (lancement primaire ou
        # second) : get_arguments() rend argv sous forme de chaînes.
        args = [_texte(m) for m in commande.get_arguments()]
        for i, arg in enumerate(args):
            if arg == "--demo":
                self.demo = True
            elif arg == "--game" and i + 1 < len(args):
                self.nom_jeu = args[i + 1]
            elif arg.startswith("--game="):
                self.nom_jeu = arg.split("=", 1)[1]
        self.activate()
        return 0

    def do_activate(self) -> None:
        if self.fenetre is None:
            self.fenetre = Fenetre(self)
        if self.demo:
            self.fenetre.definer_mode("overlay")
        # sans --demo : ne rien changer — hidden au 1ᵉʳ lancement, et on
        # n'abîme pas un overlay déjà ouvert si un 2ᵉ jeu relance la session.


def main() -> None:
    """Session X11 (hors périmètre v1) : sortie silencieuse, le jeu suit son cours."""
    if not os.environ.get("WAYLAND_DISPLAY"):
        raise SystemExit(0)
    OverlayApp().run(sys.argv)


if __name__ == "__main__":
    main()
