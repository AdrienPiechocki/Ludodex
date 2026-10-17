"""panel.py — panneau central de l'overlay (mode « overlay » uniquement).

Une ligne par widget activable (interrupteur + réglages), un bouton
« Ajouter une widget » insensible (un seul widget en v1) et, en bas, le
raccourci de fermeture et le bouton Quitter (l'overlay est orphelin : il
doit toujours pouvoir être fermé à la main).
"""
from gi.repository import Gtk

from .i18n import tr


class Panneau(Gtk.Box):
    """Panneau « Ludodex Overlay » : interrupteurs de widgets et action de sortie."""

    def __init__(self, entrees: "list[tuple[str, str, bool]]",
                 on_toggle: callable, on_quitter: callable):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                         css_classes=["panneau"])
        self.append(Gtk.Label(label=tr("Ludodex Overlay"), css_classes=["panneau-titre"]))
        for ident, nom, actif in entrees:
            self.append(self._ligne(ident, nom, actif, on_toggle))
        self.append(Gtk.Button(label=tr("Add widget"), sensitive=False))
        bas = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12,
                      margin_top=8)
        bas.append(Gtk.Label(label=tr("Close: Shift+Tab / Esc")))
        quitter = Gtk.Button(label="⏻ " + tr("Quit"))
        quitter.connect("clicked", lambda *_: on_quitter())
        bas.append(quitter)
        self.append(bas)

    def _ligne(self, ident: str, nom: str, actif: bool, on_toggle: callable) -> Gtk.Box:
        """Ligne d'un widget : nom + interrupteur + réglages (⚙)."""
        ligne = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        ligne.append(Gtk.Label(label=nom, hexpand=True, halign=Gtk.Align.START))
        interrupteur = Gtk.Switch(active=actif)
        # Connecté APRÈS la valeur initiale : construction sans faux signal.
        interrupteur.connect(
            "notify::active",
            lambda sw, _ps, i=ident: on_toggle(i, sw.get_active()))
        ligne.append(interrupteur)
        reglages = Gtk.Button(label="⚙")
        reglages.set_tooltip_text(tr("Settings"))
        reglages.connect("clicked", self._ouvrir_reglages)
        ligne.append(reglages)
        return ligne

    def _ouvrir_reglages(self, bouton: Gtk.Button) -> None:
        """Popover réglages — rempli à la tâche 8 (transparence, options)."""
        pop = Gtk.Popover()
        pop.set_child(Gtk.Label(
            label=tr("Settings"), margin_top=12, margin_bottom=12,
            margin_start=16, margin_end=16))
        pop.set_parent(bouton)
        pop.popup()
