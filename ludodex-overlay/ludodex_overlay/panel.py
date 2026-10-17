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
                 fabrique_reglages: "callable | None" = None,
                 on_fermer: "callable | None" = None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                         css_classes=["panneau"])
        # ⚙ du panneau : les réglages s'ouvrent ICI, sous la ligne —
        # `fabrique_reglages(ident)` rend le contenu (sans surface propre).
        self._fabrique_reglages = fabrique_reglages
        self._lignes: "dict[str, Gtk.Box]" = {}
        self._reglages_ouverts: "dict[str, Gtk.Box]" = {}
        self.append(Gtk.Label(label=tr("Ludodex Overlay"), css_classes=["panneau-titre"]))
        for ident, nom, actif in entrees:
            self.append(self._ligne(ident, nom, actif, on_toggle))
        self.append(Gtk.Button(label=tr("Add widget"), sensitive=False))
        bas = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12,
                      margin_top=8)
        bas.append(Gtk.Label(label=tr("Close: Shift+Tab")))
        fermer = Gtk.Button(label="✕ " + tr("Close"))
        if on_fermer is not None:
            fermer.connect("clicked", lambda *_: on_fermer())
        bas.append(fermer)
        self.append(bas)

    def _ligne(self, ident: str, nom: str, actif: bool,
               on_toggle: callable) -> Gtk.Box:
        """Ligne d'un widget : nom + interrupteur + réglages (⚙).

        Le ⚙ ouvre les réglages INLINE DANS LE PANNEAU, juste sous la
        ligne (demandé le 2026-10-18) — pas de popover (KWin activerait sa
        surface et le jeu plein écran se miniaturiserait, bug du même jour)."""
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
        if self._fabrique_reglages is not None:
            reglages.connect("clicked", lambda b, i=ident: self.basculer_reglages(i))
        ligne.append(reglages)
        self._lignes[ident] = ligne
        return ligne

    def basculer_reglages(self, ident: str) -> None:
        """⚙ : réglages de `ident` à côté du bouton. Un seul à la fois,
        re-cliquer (ou ✕) referme."""
        if ident in self._reglages_ouverts:
            self.fermer_reglages(ident)
            return
        self.fermer_tous_reglages()
        contenu = self._fabrique_reglages(ident)
        if contenu is None:  # ident inconnu (config venue d'une autre version)
            return
        boite = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4,
                        css_classes=["reglages-widget"])
        ferme = Gtk.Button(label="✕ " + tr("Close"), halign=Gtk.Align.END)
        ferme.connect("clicked", lambda *_: self.fermer_reglages(ident))
        boite.append(ferme)
        boite.append(contenu)
        self._reglages_ouverts[ident] = boite
        # Sous la ligne du widget concerné, dans le panneau même.
        self.insert_child_after(boite, self._lignes[ident])

    def fermer_reglages(self, ident: str) -> None:
        """Referme le réglages inline ouvert pour `ident` (s'il y en a)."""
        boite = self._reglages_ouverts.pop(ident, None)
        if boite is not None:
            self.remove(boite)

    def fermer_tous_reglages(self) -> None:
        """Referme tous les réglages inline ouverts (un seul à la fois)."""
        for ident in list(self._reglages_ouverts):
            self.fermer_reglages(ident)


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
