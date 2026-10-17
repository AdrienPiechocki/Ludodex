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
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Gtk4LayerShell

from . import config
from .i18n import tr
from .widgets import REGISTRE, options_defauts

ID = "io.github.AdrienPiechocki.LudodexOverlay"

# Style des widgets (classes CSS auto-ajoutées par leur fabrique).
CSS = b".clock { font-family: monospace; font-size: 48px; font-weight: bold; }"


def _installer_css() -> None:
    """CSS d'application (une seule fois au démarrage de la fenêtre)."""
    fournisseur = Gtk.CssProvider()
    fournisseur.load_from_data(CSS)
    Gtk.StyleContext.add_provider_for_display(
        Gdk.Display.get_default(), fournisseur,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)


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
        self.conf = config.charger(config.chemin_defaut())
        self.instances: "list[tuple]" = []  # [(spécimen Widget, rendu)]
        _installer_css()
        boite = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        boite.set_margin_top(24)
        boite.set_margin_start(24)
        for cle_cfg, entree in self.conf["widgets"].items():
            if not entree.get("actif") or cle_cfg not in REGISTRE:
                continue
            spec = REGISTRE[cle_cfg]
            options = {**options_defauts(spec), **entree.get("options", {})}
            specimen = spec(**options)
            rendu = specimen.fabrique()
            boite.append(rendu)
            self.instances.append((specimen, rendu))
        self.set_child(boite)
        self._tick_id = GLib.timeout_add_seconds(1, self._tick)
        self.set_visible(False)

    def _tick(self) -> bool:
        """Rafraîchit les widgets visibles (source retirée en mode hidden —
        la tâche 7 affine cette vie)."""
        for specimen, _conteneur in self.instances:
            specimen.tick()
        return True

    def definer_mode(self, mode: str) -> None:
        """« hidden » → cachée ; sinon → visible (voile et clavier : tâche 7)."""
        self._mode = mode
        self.set_visible(mode != "hidden")


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
