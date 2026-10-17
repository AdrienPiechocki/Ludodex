#!/usr/bin/env python3
"""main.py — point d'entrée de l'overlay : fenêtre layer-shell et instance unique.

Un seul `Gtk.Application` par machine (application_id fixe) : le second
lancement met à jour `--game` sans ouvrir de deuxième fenêtre.

Modes de la fenêtre (validés au spike sur KWin) :
- « overlay » : voile plein écran + panneau — JAMAIS de focus clavier (KWin
                active toute layer-shell carte, même ON_DEMAND : les jeux
                plein écran se minimiseraient) ; interaction souris/Shift+Tab ;
- « pinned »  : seuls les widgets épinglés restent visibles, translucides et
                non interactifs — la région d'entrée est vidée, les clics
                traversent vers le jeu ;
- « hidden »  : fenêtre invisible, en attente de Shift+Tab.
"""
import os, sys

import cairo

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Gtk4LayerShell

from . import config, hook, placement
from .i18n import tr
from .panel import Panneau, popover_reglages
from .shortcuts import RaccourciPortal
from .widgets import REGISTRE, options_defauts

ID = "io.github.AdrienPiechocki.LudodexOverlay"

# Styles d'application (chaque conteneur porte sa classe CSS).
CSS = b"""
window { background-color: transparent; }
.clock { font-family: monospace; font-size: 48px; font-weight: bold; }
.voile { background-color: rgba(10, 10, 14, 0.55); }
.panneau { background-color: rgba(24, 24, 32, 0.96); border-radius: 14px; padding: 18px; color: #f2f2f5; }
.panneau-titre { font-size: 20px; font-weight: bold; margin-bottom: 6px; }
.widget { background-color: rgba(0, 0, 0, 0.45); border-radius: 10px; padding: 8px; }
"""


def _installer_css() -> None:
    """CSS d'application (une seule fois au démarrage de la fenêtre)."""
    fournisseur = Gtk.CssProvider()
    fournisseur.load_from_data(CSS)
    Gtk.StyleContext.add_provider_for_display(
        Gdk.Display.get_default(), fournisseur,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)


def _texte(morceau) -> str:
    """Un élément des arguments d'une GApplicationCommandLine, quel que soit
    son type d'emballage (str, bytes ou liste d'entiers)."""
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
    """Fenêtre layer-shell unique : voile, panneau et widgets flottants."""

    def __init__(self, app: Gtk.Application):
        super().__init__(application=app, title=tr("Ludodex Overlay"))
        Gtk4LayerShell.init_for_window(self)
        Gtk4LayerShell.set_layer(self, Gtk4LayerShell.Layer.OVERLAY)
        Gtk4LayerShell.set_namespace(self, "ludodex-overlay")
        for bord in (Gtk4LayerShell.Edge.TOP, Gtk4LayerShell.Edge.RIGHT,
                     Gtk4LayerShell.Edge.BOTTOM, Gtk4LayerShell.Edge.LEFT):
            Gtk4LayerShell.set_anchor(self, bord, True)
        # -1 = ignorer les zones réservées : le voile couvre TOUT l'écran,
        # barre des tâches comprise (avec 0, le protocole fait éviter les
        # panneaux et le voile se fait rogner sur leur zone — bug du 2026-10-17).
        Gtk4LayerShell.set_exclusive_zone(self, -1)
        _installer_css()
        self._mode = "hidden"
        self.conf = config.charger(config.chemin_defaut())
        self.instances: "list[tuple]" = []  # [(spécimen Widget, conteneur)]
        self.panneau: "Panneau | None" = None
        self._tick_id: "int | None" = None
        # Racine : enfant plein écran (le voile) + enfants flottants par-dessus.
        self.racine = Gtk.Overlay()
        self._voile = Gtk.Box(css_classes=["voile"])
        self.racine.set_child(self._voile)
        self.set_child(self.racine)
        controle = Gtk.EventControllerKey()
        controle.connect("key-pressed", self._touche)
        self.add_controller(controle)
        self.connect("map", lambda *_: self.placer_tout())
        self.construire_widgets()
        self._placer_panneau()
        self.appliquer_ecran()
        self.set_visible(False)

    # ------------------------------------------------------------------ modes

    def definer_mode(self, mode: str) -> None:
        """Applique un mode : visibilité, clavier, région d'entrée, voile,
        panneau et vie du tick (rafraîchi seulement quand visible)."""
        self._mode = mode
        self.set_visible(mode != "hidden")
        # Aucun focus clavier (expériences du 2026-10-17 : KWin ACTIVE toute
        # surface layer-shell qui se carte, même en ON_DEMAND — les jeux
        # plein écran se minimisent alors d'eux-mêmes) : le jeu garde son
        # clavier, l'overlay s'utilise à la souris + Shift+Tab.
        Gtk4LayerShell.set_keyboard_mode(self, Gtk4LayerShell.KeyboardMode.NONE)
        # Drapeau pour le hook LD_PRELOAD injecté dans le jeu : ouvert → le
        # hook relâche la capture de la souris de l'intérieur du processus
        # (spike 2026-10-17) — zéro changement de focus, zéro miniaturisation.
        hook.ecrire_etat(bool(self.conf.get("souris_libre", True)) and mode == "overlay")
        self._appliquer_region_entree()
        self._voile.set_visible(mode == "overlay")
        if self.panneau is not None:
            self.panneau.set_visible(mode == "overlay")
        for specimen, conteneur in self.instances:
            # Table des modes : en « pinned », seuls les widgets épinglés
            # restent rendus — les autres disparaissent du jeu.
            epingle = bool(self.conf["widgets"][type(specimen).id].get("epingle"))
            conteneur.set_visible(epingle if mode == "pinned" else True)
        if mode != "overlay":
            self.reveler_barre(None, False)
        self._gerer_tick(mode != "hidden")
        if mode == "overlay":
            self.placer_tout()
            self._retablir_region(0)

    def _retablir_region(self, essai: int) -> bool:
        """Au mappage la taille est encore 0×0 : on re-pose la région
        d'entrée jusqu'à ce que l'arrive de l'allocation (10 × 50 ms max)."""
        self._appliquer_region_entree()
        if self.get_width() > 0 or essai >= 10 or self._mode != "overlay":
            return False
        GLib.timeout_add(50, self._retablir_region, essai + 1)
        return False

    def _appliquer_region_entree(self) -> None:
        """Région d'entrée du mode courant — AUSSI rappelée à chaque
        allocation (signal size-allocate) : au mappage la taille est encore
        0, et poser une région 1×1 alors rendait tout l'overlay cliquable à
        travers (bug du 2026-10-18, à partir de la deuxième ouverture) —
        c'est l'allocation qui fait foi, pas l'instant du changement de mode."""
        surface = self.get_surface()
        if surface is None:
            return  # premier mappage : défaut plein, l'allocation suit
        if self._mode == "pinned":
            # Clics traversants : région vide (validé KWin au spike).
            surface.set_input_region(cairo.Region())
            return
        largeur, hauteur = self.get_width(), self.get_height()
        if largeur <= 0 or hauteur <= 0:
            return  # non alloué : on laisse tel quel, size-allocate suit
        surface.set_input_region(cairo.Region(
            cairo.RectangleInt(x=0, y=0, width=largeur, height=hauteur)))

    def basculer(self) -> None:
        """Ouvre/ferme l'overlay : fermé → épinglés seuls (ou rien), sinon voile."""
        if self._mode == "overlay":
            epingle = any(entree.get("epingle")
                          for entree in self.conf["widgets"].values())
            self.definer_mode("pinned" if epingle else "hidden")
        else:
            self.definer_mode("overlay")

    def fermer(self) -> None:
        """⏻ Quitter : l'overlay est orphelin, il doit toujours pouvoir sortir."""
        self.get_application().quit()

    def _touche(self, _controle, keyval, _code, _etats) -> bool:
        # Sans focus clavier (mode NONE) c'est mort-vivant : la fermeture
        # réelle est Shift+Tab (portail) et ✕ Fermer du panneau.
        if keyval == Gdk.KEY_Escape and self._mode == "overlay":
            self.basculer()
            return True
        return False

    def _gerer_tick(self, actif: bool) -> None:
        if actif and self._tick_id is None:
            self._tick_id = GLib.timeout_add_seconds(1, self._tick)
        elif not actif and self._tick_id is not None:
            GLib.source_remove(self._tick_id)
            self._tick_id = None

    def _tick(self) -> bool:
        for specimen, _conteneur in self.instances:
            specimen.tick()
        return True

    # ---------------------------------------------------------------- widgets

    def construire_widgets(self) -> None:
        """Construit les widgets actifs de la config dans des conteneurs
        flottants (drag + barre d'outils au survol)."""
        for cle_cfg, entree in self.conf["widgets"].items():
            if not entree.get("actif") or cle_cfg not in REGISTRE:
                continue
            spec = REGISTRE[cle_cfg]
            options = {**options_defauts(spec), **entree.get("options", {})}
            specimen = spec(**options)
            conteneur = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                css_classes=["widget"])
            conteneur.set_opacity(1 - 0.7 * float(entree.get("transparence", 0.0)))
            conteneur.append(specimen.fabrique())
            reveleur = Gtk.Revealer(
                transition_type=Gtk.RevealerTransitionType.CROSSFADE,
                transition_duration=120)
            reveleur.set_child(self._barre_outils(cle_cfg))
            conteneur.append(reveleur)
            conteneur.set_halign(Gtk.Align.START)
            conteneur.set_valign(Gtk.Align.START)
            self.racine.add_overlay(conteneur)
            self._attacher_deplacement(cle_cfg, conteneur)
            self._attacher_survol(conteneur, reveleur)
            self.instances.append((specimen, conteneur))

    def reconstruire(self) -> None:
        """Détruit et rebâtit widgets + panneau (après un changement d'état)."""
        for _specimen, conteneur in self.instances:
            self.racine.remove_overlay(conteneur)
            conteneur.unparent()
        self.instances.clear()
        self.construire_widgets()
        self._placer_panneau()
        self.placer_tout()

    def placer_tout(self) -> None:
        """Repositionne les widgets d'après les fractions sauvegardées —
        clampé à l'écran courant (écran/résolution ayant pu changer)."""
        largeur, hauteur = self.get_width(), self.get_height()
        if largeur <= 0 or hauteur <= 0:
            return
        for specimen, conteneur in self.instances:
            pos = self.conf["widgets"][type(specimen).id]["pos"]
            x, y = placement.frac_vers_px(pos["x"], pos["y"], largeur, hauteur)
            x, y = placement.ajuster(
                x, y, conteneur.get_width(), conteneur.get_height(),
                largeur, hauteur)
            conteneur.set_margin_start(int(x))
            conteneur.set_margin_top(int(y))

    def _attacher_deplacement(self, ident: str, conteneur: Gtk.Box) -> None:
        """Drag au doigt/souris : aimantation douce pendant le mouvement,
        position convertie en fractions (persistance) au relâchement.

        Les offsets GTK4 sont livrés dans le repère du widget, qui bouge
        dès qu'on applique une marge : la base est la marge COURANTE
        (`placement.pas_de_drag`), sinon le widget traîne derrière la
        souris (bug du 2026-10-17, cause racine en test)."""
        def mise_a_jour(_geste, dx, dy):
            largeur, hauteur = self.get_width(), self.get_height()
            if largeur <= 0:
                return
            x, y = placement.pas_de_drag(
                conteneur.get_margin_start(), conteneur.get_margin_top(),
                dx, dy, conteneur.get_width(), conteneur.get_height(),
                largeur, hauteur)
            conteneur.set_margin_start(x)
            conteneur.set_margin_top(y)

        def fin(_geste, _dx, _dy):
            largeur, hauteur = self.get_width(), self.get_height()
            if largeur <= 0:
                return
            pos = self.conf["widgets"][ident]["pos"]
            pos["x"], pos["y"] = placement.px_vers_frac(
                conteneur.get_margin_start(), conteneur.get_margin_top(),
                largeur, hauteur)
            config.sauver(config.chemin_defaut(), self.conf)

        geste = Gtk.GestureDrag()
        geste.connect("drag-update", mise_a_jour)
        geste.connect("drag-end", fin)
        conteneur.add_controller(geste)

    def _attacher_survol(self, conteneur: Gtk.Box, reveleur: Gtk.Revealer) -> None:
        """Barre d'outils révélée au survol (mode overlay seulement).

        Les coordonnées de enter/leave varient selon la version GTK
        (GTK 4.24 les a retirées) : les arguments optionnels sont absorbés."""
        mouvement = Gtk.EventControllerMotion()
        mouvement.connect(
            "enter", lambda _c, *_coords, r=reveleur: self.reveler_barre(r, self._mode == "overlay"))
        mouvement.connect("leave", lambda _c, *_coords, r=reveleur: self.reveler_barre(r, False))
        conteneur.add_controller(mouvement)

    def reveler_barre(self, reveleur: Gtk.Revealer | None, visible: bool) -> None:
        if visible and reveleur is not None:
            reveleur.set_reveal_child(True)
        elif reveleur is not None:
            reveleur.set_reveal_child(False)
        else:  # fermeture générale (changement de mode)
            for _specimen, conteneur in self.instances:
                enfant = conteneur.get_last_child()
                if isinstance(enfant, Gtk.Revealer):
                    enfant.set_reveal_child(False)

    def _barre_outils(self, ident: str) -> Gtk.Widget:
        """📌 Épingler · ⚙ Réglages · ✕ Retirer — tooltips pour économiser la place."""
        boite = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6,
                        halign=Gtk.Align.CENTER, margin_top=4)
        epingle = Gtk.Button(label="📌")
        epingle.set_tooltip_text(tr("Pin"))
        epingle.connect("clicked", lambda *_: self.basculer_epingle(ident))
        reglages = Gtk.Button(label="⚙")
        reglages.set_tooltip_text(tr("Settings"))
        reglages.connect("clicked", lambda *_: self.ouvrir_reglages(reglages, ident))
        retirer = Gtk.Button(label="✕")
        retirer.set_tooltip_text(tr("Remove"))
        retirer.connect("clicked", lambda *_: self.basculer_actif(ident, False))
        for bouton in (epingle, reglages, retirer):
            boite.append(bouton)
        return boite

    def ouvrir_reglages(self, bouton: Gtk.Button, ident: str) -> None:
        """Popover réglages du widget : options du schema, transparence, épinglage."""
        spec = REGISTRE[ident]
        entree = self.conf["widgets"][ident]
        valeurs = {**options_defauts(spec), **entree.get("options", {}),
                   "transparence": float(entree.get("transparence", 0.0)),
                   "epingle": bool(entree.get("epingle", False))}
        pop = popover_reglages(bouton, ident, spec.schema, valeurs, self.reglage_change)
        pop.connect("closed", lambda p: p.unparent())
        pop.popup()

    def conteneur_de(self, ident: str) -> "Gtk.Box | None":
        for specimen, conteneur in self.instances:
            if type(specimen).id == ident:
                return conteneur
        return None

    def reglage_change(self, ident: str, cle: str, valeur) -> None:
        """Un réglage bouge : config à jour, effet immédiat, sauvegarde."""
        entree = self.conf["widgets"][ident]
        if cle == "transparence":
            entree["transparence"] = float(valeur)
            conteneur = self.conteneur_de(ident)
            if conteneur is not None:
                # Jamais invisible : 100 % de transparence = 30 % d'opacité.
                conteneur.set_opacity(1 - 0.7 * float(valeur))
        elif cle == "epingle":
            entree["epingle"] = bool(valeur)
        else:
            entree.setdefault("options", {})[cle] = valeur
            # Contrat Widget : la clé du schema est un attribut du spécimen,
            # mis à jour en direct (le rafraîchissement suivant l'applique).
            for specimen, _conteneur in self.instances:
                if type(specimen).id == ident:
                    setattr(specimen, cle, valeur)
                    specimen.tick()
        config.sauver(config.chemin_defaut(), self.conf)

    # ----------------------------------------------------------------- états

    def basculer_actif(self, ident: str, actif: bool) -> None:
        """Activation/désactivation d'un widget (interrupteur ou ✕)."""
        self.conf["widgets"][ident]["actif"] = bool(actif)
        config.sauver(config.chemin_defaut(), self.conf)
        self.reconstruire()

    def basculer_epingle(self, ident: str) -> None:
        """Épinglage : le widget restera visible en mode « pinned »."""
        entree = self.conf["widgets"][ident]
        entree["epingle"] = not entree.get("epingle", False)
        config.sauver(config.chemin_defaut(), self.conf)

    def _ligne_souris(self) -> Gtk.Box:
        """Interrupteur global « Libérer la souris des jeux » (persisté)."""
        ligne = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        ligne.append(Gtk.Label(label=tr("Release game mouse"),
                               hexpand=True, halign=Gtk.Align.START))
        interrupteur = Gtk.Switch(active=bool(self.conf.get("souris_libre", True)))
        interrupteur.connect("notify::active", self._souris_change)
        ligne.append(interrupteur)
        return ligne

    def _souris_change(self, interrupteur, _ps) -> None:
        self.conf["souris_libre"] = bool(interrupteur.get_active())
        config.sauver(config.chemin_defaut(), self.conf)
        hook.ecrire_etat(self.conf["souris_libre"] and self._mode == "overlay")

    def _placer_panneau(self) -> None:
        """(Re)construit le panneau central selon l'état courant de la config."""
        if self.panneau is not None:
            self.racine.remove_overlay(self.panneau)
            self.panneau.unparent()
        entrees = [
            (cle, tr(REGISTRE[cle].nom) if cle in REGISTRE else cle,
             bool(entree.get("actif")))
            for cle, entree in self.conf["widgets"].items()]
        self.panneau = Panneau(entrees, self.basculer_actif, self.fermer,
                               on_reglages=lambda i, b: self.ouvrir_reglages(b, i),
                               ecran=self._ligne_ecran(),
                               on_fermer=self.basculer,
                               souris=self._ligne_souris())
        self.panneau.set_halign(Gtk.Align.CENTER)
        self.panneau.set_valign(Gtk.Align.CENTER)
        self.racine.add_overlay(self.panneau)
        self.panneau.set_visible(self._mode == "overlay")

    # ----------------------------------------------------------------- écran

    def _noms_sorties(self) -> "list[str]":
        """Noms des connecteurs des moniteurs (vides → réglage masqué)."""
        display = Gdk.Display.get_default()
        if display is None:
            return []
        noms = []
        for moniteur in display.get_monitors():
            nom = moniteur.get_connector()
            if nom:
                noms.append(nom)
        return noms

    def _ligne_ecran(self) -> "Gtk.Box | None":
        """Ligne « Écran de l'overlay » : auto ou sortie nommée, persistée."""
        noms = self._noms_sorties()
        if not noms:
            return None
        modele = Gtk.StringList()
        modele.append(tr("Auto"))
        for nom in noms:
            modele.append(nom)
        liste = Gtk.DropDown(model=modele)
        regle = self.conf.get("ecran", "auto")
        liste.set_selected(noms.index(regle) + 1 if regle in noms else 0)
        # Connecté APRÈS la valeur initiale : construction sans faux signal.
        liste.connect("notify::selected", self._ecran_change)
        boite = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        boite.append(Gtk.Label(label=tr("Overlay screen"),
                               hexpand=True, halign=Gtk.Align.START))
        boite.append(liste)
        return boite

    def _ecran_change(self, liste: Gtk.DropDown, _ps) -> None:
        index = liste.get_selected()
        noms = self._noms_sorties()
        self.conf["ecran"] = "auto" if index == 0 else noms[index - 1]
        config.sauver(config.chemin_defaut(), self.conf)
        self.appliquer_ecran()

    def appliquer_ecran(self) -> None:
        """Place la fenêtre sur la sortie demandée (si elle existe encore),
        sinon laisse le composant choisir (auto)."""
        noms = self._noms_sorties()
        nom = config.choix_sortie(self.conf.get("ecran", "auto"), noms)
        cible = None
        if nom is not None:
            for moniteur in Gdk.Display.get_default().get_monitors():
                if moniteur.get_connector() == nom:
                    cible = moniteur
                    break
        Gtk4LayerShell.set_monitor(self, cible)


class OverlayApp(Gtk.Application):
    """Application unique : lit `--game NOM` et `--demo`, puis active la fenêtre."""

    def __init__(self):
        super().__init__(application_id=ID,
                         flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.nom_jeu = ""
        self.demo = False
        self.fenetre: "Fenetre | None" = None
        self.raccourci: "RaccourciPortal | None" = None

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
            # Raccourci global : démarré une fois pour la vie de l'app —
            # en cas d'échec, notification puis sortie (jamais muette).
            self.raccourci = RaccourciPortal(
                on_toggle=self.fenetre.basculer,
                on_echec=self._raccourci_echec)
            self.raccourci.demarrer()
        if self.demo:
            self.demo = False  # une seule fois : un 2ᵉ lancement (`--game`)
            # ne rouvre pas le voile au-dessus d'une session déjà lancée.
            self.fenetre.definer_mode("overlay")
        # sans --demo : ne rien changer — hidden au 1ᵉʳ lancement, et on
        # n'abîme pas un overlay déjà ouvert si un 2ᵉ jeu relance la session.

    def _raccourci_echec(self, message: str) -> None:
        notifier(tr("Ludodex Overlay"), message)
        self.quit()

    def do_shutdown(self) -> None:
        # Fin de vie : le jeu doit pouvoir reprendre sa souris même si
        # l'overlay sort par ici (Ludodex remet l'état à zéro au lancement
        # suivant, pour couvrir aussi un crash).
        hook.ecrire_etat(False)
        super().do_shutdown()


def main() -> None:
    """Session X11 (hors périmètre v1) : sortie silencieuse, le jeu suit son cours."""
    if not os.environ.get("WAYLAND_DISPLAY"):
        raise SystemExit(0)
    OverlayApp().run(sys.argv)


if __name__ == "__main__":
    main()
