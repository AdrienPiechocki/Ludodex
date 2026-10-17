"""panel.py — panneau central de l'overlay (mode « overlay » uniquement).

Une ligne par widget activable (interrupteur + réglages ⚙), un bouton
« Ajouter une widget » insensible (un seul widget en v1) et, en bas, le
bouton ✕ Fermer (Shift+Tab aussi) — l'overlay ne prend jamais le focus
clavier (le jeu le garderait sinon, et plein écran se minimiserait) : tout
se pilote à la souris.

Pas de bouton « Quitter » : l'overlay est orphelin, il vit le temps de la
session de jeu et Ludodex le tue à la fin de celle-ci ; un « Quitter »
manuel ne laisserait que la sortie par ✕ Fermer ou Shift+Tab.
"""
from gi.repository import Gtk

from .i18n import tr


class Panneau(Gtk.Box):
    """Panneau « Ludodex Overlay » : interrupteurs de widgets et fermeture."""

    def __init__(self, entrees: "list[tuple[str, str, bool]]",
                 on_toggle: callable,
                 on_reglages: "callable | None" = None,
                 on_fermer: "callable | None" = None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                         css_classes=["panneau"])
        self.append(Gtk.Label(label=tr("Ludodex Overlay"), css_classes=["panneau-titre"]))
        for ident, nom, actif in entrees:
            self.append(self._ligne(ident, nom, actif, on_toggle, on_reglages))
        self.append(Gtk.Button(label=tr("Add widget"), sensitive=False))
        bas = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12,
                      margin_top=8)
        bas.append(Gtk.Label(label=tr("Close: Shift+Tab")))
        fermer = Gtk.Button(label="✕ " + tr("Close"))
        if on_fermer is not None:
            fermer.connect("clicked", lambda *_: on_fermer())
        bas.append(fermer)
        self.append(bas)

    def _ligne(self, ident: str, nom: str, actif: bool, on_toggle: callable,
               on_reglages: "callable | None") -> Gtk.Box:
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
        if on_reglages is not None:
            reglages.connect("clicked", lambda b, i=ident: on_reglages(i))
        ligne.append(reglages)
        return ligne


def conteneur_reglages(ident: str, schema: "list[dict]", valeurs: dict,
                       on_changement: callable) -> Gtk.Box:
    """Boîte de réglages d'un widget SANS surface propre : options du `schema`
    (bool → case, scale → curseur), puis transparence et épinglage communs.

    Construite hors popover (bug du 2026-10-18 : un `Gtk.Popover` ouvre une
    nouvelle surface que KWin ACTIVE, ce qui fait perdre le focus au jeu plein
    écran et le minimise). Rendue INLINE dans le conteneur du widget, elle
    n'a pas de surface à activer — le jeu garde le focus.

    `valeurs` : options fusionnées + `transparence` + `epingle`. Chaque
    changement appelle `on_changement(ident, cle, valeur)`."""
    contenu = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                      margin_top=12, margin_bottom=12,
                      margin_start=16, margin_end=16)

    def _curseur(borne_min, borne_max, valeur, pas=0.05):
        return Gtk.Scale(
            orientation=Gtk.Orientation.HORIZONTAL, draw_value=True, digits=2,
            hexpand=True,
            adjustment=Gtk.Adjustment(
                lower=borne_min, upper=borne_max,
                step_increment=pas, page_increment=pas * 2, value=valeur))

    for reglage in schema:
        cle = reglage["cle"]
        etiquette = tr(reglage["label"])
        if reglage["type"] == "bool":
            case = Gtk.CheckButton(label=etiquette, active=bool(valeurs.get(cle)))
            case.connect("toggled",
                         lambda c, i=ident, k=cle: on_changement(i, k, c.get_active()))
            contenu.append(case)
        elif reglage["type"] == "scale":
            contenu.append(Gtk.Label(label=etiquette, halign=Gtk.Align.START))
            curseur = _curseur(reglage.get("min", 0.0), reglage.get("max", 1.0),
                               float(valeurs.get(cle, 0.5)))
            curseur.connect("value-changed",
                            lambda s, i=ident, k=cle: on_changement(i, k, s.get_value()))
            contenu.append(curseur)

    # Réglages communs : transparence (30 % d'opacité mini) et épinglage.
    contenu.append(Gtk.Label(label=tr("Transparency"), halign=Gtk.Align.START))
    transparence = _curseur(0.0, 1.0, float(valeurs.get("transparence", 0.0)), pas=0.05)
    transparence.connect(
        "value-changed",
        lambda s, i=ident: on_changement(i, "transparence", s.get_value()))
    contenu.append(transparence)
    epingle = Gtk.CheckButton(label=tr("Pin"), active=bool(valeurs.get("epingle")))
    epingle.connect("toggled",
                    lambda c, i=ident: on_changement(i, "epingle", c.get_active()))
    contenu.append(epingle)

    return contenu
