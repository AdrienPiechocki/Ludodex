"""pickview.py — page « Choisir » (tirage du jeu)."""
import random, threading, time
from concurrent.futures import ThreadPoolExecutor

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gsk", "4.0")
from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, GObject, Graphene, Gsk, Gtk, Pango

import picker as P
from i18n import available, get_lang, tr
from .theme import DUREES

# Carrousel de tirage : 45 jaquettes de 170×260 px espacées de 12 px. Dimensions
# FIXES : le défilement se positionne case par case, et une jaquette de largeur
# variable (selon le thème ou le contenu) décalerait le point d'atterrissage.
ROULETTE_JAQUETTES = 45
ROULETTE_L, ROULETTE_H = 170, 260
ROULETTE_ESPACEMENT = 12
ROULETTE_DUREE_MS = 2800
# Décélération : à 90 % du temps écoulé, il ne reste que 10 % du chemin, puis
# presque plus rien — la jaquette retenue semble se poser, pas s'arrêter.
ROULETTE_EASE = lambda t: 1 - (1 - t) ** 5


class RouletteBande(Gtk.Widget):
    """Bande de jaquettes qui glisse horizontalement, cadrée par sa largeur.

    Pourquoi pas un `Gtk.Box` dans un `Gtk.ScrolledWindow` ? Un `Gtk.Box` additionne
    les largeurs MINIMALES de ses enfants : 45 jaquettes de 170 px réclament
    7 650 px. GTK4 propage cette largeur minimale à travers `GtkScrolledWindow`,
    `GtkViewport`, `GtkStack`, `Adw.ToolbarView`… jusqu'à la fenêtre, qui
    s'élargit d'autant (mesuré sur les cinq conteneurs testés : aucun n'y échappe,
    même avec `propagate-natural-width` à `False` ou `overflow` à `Hidden`).

    Ce widget déclare donc lui-même une largeur minimale et naturelle nulle : le
    cadre ne dépend que de l'espace disponible. Les jaquettes sont posées par une
    translation dans `do_allocate`, au-delà du cadre elles sont simplement masquées
    (`overflow = HIDDEN`). Pas de barre de défilement, pas d'ajustement à piloter :
    une seule valeur, l'offset, que l'animation fait varier.
    """

    def __init__(self):
        super().__init__()
        self.set_overflow(Gtk.Overflow.HIDDEN)
        self._pics, self._offset, self._pas = [], 0.0, ROULETTE_L + ROULETTE_ESPACEMENT
        self._largeur = 0                  # largeur du cadre à la dernière allocation
        self._sur_largeur = None           # callable(décalage) : la vue suit le centre

    # -- contenu -------------------------------------------------------------
    def _creer_pic(self, g):
        pic = Gtk.Picture(width_request=ROULETTE_L, height_request=ROULETTE_H,
                          can_shrink=True, content_fit=Gtk.ContentFit.COVER)
        pic.add_css_class("roulette-flou")   # tout est flou tant que la bande tourne
        pic.add_css_class("roulette-case")   # dos de carte tant que l'art n'arrive pas
        pic._jeu_id = g["id"]
        return pic

    def remplacer(self, jeux):
        """Reconstruit la bande. Chaque jaquette garde son `id` de jeu en attribut
        Python (`_jeu_id`) : c'est par là qu'on reconnaît le gagnant au moment de
        le déflouter, sans tenir une liste à part."""
        self.vider()
        self.ajouter(jeux)

    def ajouter(self, jeux):
        """Allonge la bande à droite SANS toucher à ce qui s'y trouve déjà : les
        jaquettes en place gardent leurs textures, et le raccord se produit en
        dehors de ce que le cadre montre (tout est flou et en mouvement)."""
        for g in jeux:
            pic = self._creer_pic(g)
            pic.set_parent(self)
            self._pics.append(pic)
        self.queue_allocate()

    def rogner(self, premier, dernier):
        """Ne garde que `pics[premier:dernier]`, et décale l'offset d'autant pour
        que la bande paraisse strictement immobile. Les widgets conservés ne sont
        PAS recréés — c'est ce qui rend la coupe invisible à l'écran."""
        perdus = self._pics[:premier] + self._pics[dernier:]
        self._pics = self._pics[premier:dernier]
        for pic in perdus:
            pic.unparent()
        self._offset -= premier * self._pas
        self.queue_allocate()

    def index_de(self, jeu_id, depuis=0):
        """Index réel d'un jeu dans la bande, à partir de la case `depuis` — lu,
        jamais déduit de la règle de placement de `roulette_strip`.

        `depuis` est indispensable : un tirage n'ALLONGE la bande que, et un jeu
        conservé peut y être déjà présent. Chercher depuis 0 le trouverait LÀ,
        cible derrière le point de départ, et l'animation partirait à rebours."""
        return next((i for i in range(depuis, len(self._pics))
                     if getattr(self._pics[i], "_jeu_id", None) == jeu_id), depuis)

    def jaquette_de(self, jeu_id):
        return next((p for p in self._pics if getattr(p, "_jeu_id", None) == jeu_id), None)

    def vider(self):
        for pic in self._pics:
            pic.unparent()
        self._pics = []

    # -- défilement ----------------------------------------------------------
    def set_offset(self, v):
        """Décale la bande. `queue_allocate`, pas `queue_draw` : la position des
        jaquettes est fixée au moment de l'allocation, et GTK ne repeint que ce
        qui a bougé.

        AUCUN plafond à zéro : un offset négatif décale la bande vers la DROITE,
        laissant du vide à gauche — c'est même la seule façon de centrer la case 0
        d'une bande plus courte que le cadre (un seul jeu éligible). Ce vide ne se
        voit pas, l'overflow caché du cadre le rognant."""
        self._offset = float(v)
        self.queue_allocate()

    def cible_centre(self, rang):
        """Offset qui place la case `rang` au centre du cadre, si large soit-il.

        Peut être NÉGATIF : la case demandée tombe alors en deçà du début de la
        bande. Voir `set_offset` — un plafond à zéro laissait la jaquette unique
        collée au bord gauche, à 460 px du centre.

        La formule est dans `core/pick.py` : elle dépend de la LARGEUR, et
        `do_size_allocate` doit la relire à chaque changement de celle-ci."""
        return P.roulette_centre(rang, self.get_width(), self._pas, ROULETTE_L)

    # -- protocolles GTK -----------------------------------------------------
    def do_measure(self, orientation, for_size):
        if orientation == Gtk.Orientation.HORIZONTAL:
            return (0, 0, -1, -1)      # ne dicte JAMAIS la largeur de la fenêtre
        return (0, ROULETTE_H + 40, -1, -1)

    def do_size_allocate(self, width, height, baseline):
        """Pose les jaquettes par translation. Protocole nommé `size_allocate`, et
        NON `allocate` : `allocate` est le nom de la fonction publique, celui du
        vfunc de classe est `size_allocate` — un `do_allocate` est ignoré EN
        SILENCE, les enfants restent à 0×0 alors que le parent se croit dimensionné
        (vérifié : les jaquettes alors invisibles, sans la moindre trace d'erreur).

        C'est aussi ICI que le cadre suit un redimensionnement de la fenêtre.
        L'offset exprime une position CENTRÉE, `K − largeur/2` : la largeur
        changeant, le centre bouge de Δ/2 et l'offset doit suivre. Sans cela, le
        gagnant reste où il était — dérivant d'autant (mesuré : 210 px après un
        agrandissement de 420). La première allocation est écartée, `self._largeur`
        valant 0 : il n'y a pas de centre à suivre tant que le cadre n'a jamais
        été dimensionné."""
        if self._largeur and width != self._largeur:
            decal = P.roulette_delta_largeur(self._largeur, width)
            self._offset += decal
            if self._sur_largeur:      # les bornes de l'animation dépendent aussi
                self._sur_largeur(decal)   # de la largeur : elles doivent suivre
        self._largeur = width
        y = max(0, (height - ROULETTE_H) // 2)
        base = Gsk.Transform().translate(Graphene.Point().init(-self._offset, y))
        for i, pic in enumerate(self._pics):
            pic.allocate(ROULETTE_L, ROULETTE_H, -1,
                         base.translate(Graphene.Point().init(i * self._pas, 0)))

    def do_unroot(self):
        # Les jaquettes sont nos enfants : elles doivent être détachées quand la
        # bande quitte l'arbre, sinon GTK se plaint à la destruction.
        self.vider()
        Gtk.Widget.do_unroot(self)


class PickViewMixin:

    # ---------- page « Choisir » ----------
    def _build_pick(self):
        # --- écran 1 : filtres ---
        filters = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18,
                          margin_top=18, margin_bottom=24, margin_start=18, margin_end=18)

        group = Adw.PreferencesGroup()
        self.time_row = Adw.ComboRow(title=tr("Time available"),
                                 model=Gtk.StringList.new([tr("Any"), "15 min", "30 min", "1 h", "2 h"]))
        group.add(self.time_row)
        filters.append(group)

        # Les tags sont TOUJOURS dépliés : le dépliant imposait un clic juste pour
        # voir quels tags sont retenus. Le sous-titre dit, lui, la portée du filtre
        # (« + Puzzle − Roguelike ») — les pastilles bleues et rouges se voient, mais
        # pas toutes ensemble d'un seul regard. En groupe à part, à sa place
        # d'origine, entre « Temps disponible » et « Humeur ».
        self.tags_group = Adw.PreferencesGroup(title=tr("Tags"))
        # « Vider » à droite du titre : le filtre vit dans DEUX ensembles (inclus,
        # exclus) — un seul geste remet tout au neutre. Insensible sans rien de
        # retenu (tenu à jour dans _after_tag_change).
        self.clear_tags_btn = Gtk.Button(label=tr("Clear tags"), valign=Gtk.Align.CENTER, css_classes=["flat"])
        self.clear_tags_btn.connect("clicked", self._on_clear_tags)
        self.tags_group.set_header_suffix(self.clear_tags_btn)
        self.mood_flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=False,
                                     min_children_per_line=2, max_children_per_line=5,
                                     row_spacing=6, column_spacing=6,
                                     margin_top=8, margin_bottom=8, margin_start=12, margin_end=12)
        # défilement interne borné : sinon, beaucoup de tags, la zone dédiée au jeu est écrasée
        mood_scroll = Gtk.ScrolledWindow(propagate_natural_height=True, max_content_height=400,
                                         hscrollbar_policy=Gtk.PolicyType.NEVER)
        mood_scroll.set_child(self.mood_flow)
        self.tags_group.add(mood_scroll)
        filters.append(self.tags_group)
        self._update_tags_summary()  # l'astuce des clics, avant même le chargement des tags

        group2 = Adw.PreferencesGroup()
        self.mood_mode_row = Adw.ComboRow(title=tr("Mood match"),
                                          model=Gtk.StringList.new([tr("Any (OR)"), tr("All (AND)")]))
        self.mood_mode_row.set_selected(0 if self.mood_mode == "any" else 1)
        self.mood_mode_row.connect("notify::selected", self._on_mood_mode_changed)
        group2.add(self.mood_mode_row)
        self.pick_col_selected = set()  # collections du tirage (vide = toutes)
        self.collections_row = Adw.ActionRow(title=tr("Collections"), activatable=False)
        self.collections_btn = Gtk.MenuButton(valign=Gtk.Align.CENTER)
        self.collections_pop = Gtk.Popover()
        self.collections_btn.set_popover(self.collections_pop)
        self.collections_row.add_suffix(self.collections_btn)
        self.collections_row.set_activatable_widget(self.collections_btn)
        group2.add(self.collections_row)
        filters.append(group2)

        self.btn_go = Gtk.Button(label=tr("Pick for me"), halign=Gtk.Align.CENTER)
        self.btn_go.add_css_class("suggested-action")
        self.btn_go.add_css_class("pill")
        self.btn_go.connect("clicked", self._on_choose)
        filters.append(self.btn_go)
        filters_scroll = Gtk.ScrolledWindow(child=filters, hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)

        # --- écran 2 : le jeu proposé, en carrousel de jaquettes ---
        proposal = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                           margin_top=6, margin_bottom=24, margin_start=18, margin_end=18)
        self.back_btn = Gtk.Button(icon_name="go-previous-symbolic", halign=Gtk.Align.START,
                                   css_classes=["flat"], tooltip_text=tr("Back to filters"))
        self.back_btn.connect("clicked", lambda *_: self._show_filters())
        proposal.append(self.back_btn)

        # Le carrousel remplace l'ancienne couverture unique : défilent les
        # jaquettes des jeux ÉLIGIBLES au tirage — ceux qui auraient pu sortir —
        # et le jeu tiré s'immobilise au centre, seul déflouté. Avant tout tirage,
        # la même zone montre simplement l'invite (pile d'écran « vide »).
        self.roulette_bande = RouletteBande()
        # La bande suit déjà son propre offset quand la fenêtre change de largeur ;
        # elle prévient aussi ici, pour que les BORNES de l'animation suivent.
        # Sans quoi le tick suivant remettrait l'offset à l'ancienne valeur et le
        # gagnant atterrit hors centre.
        self.roulette_bande._sur_largeur = self._bordure_a_change
        self.status_page = Adw.StatusPage(icon_name="media-playlist-shuffle-symbolic", title=tr("What to play?"),
                                          description=tr("Press the button and the app decides."),
                                          height_request=ROULETTE_H + 40, vexpand=True)
        # Nom `pick_zone`, PAS `pick_stack` : ce nom désigne déjà, plus bas, le
        # sélecteur « filtres → fiche ». Deux piles du même attribut se seraient
        # écrasées silencieusement (la seconde gagne, la première reste muette).
        self.pick_zone = Gtk.Stack(vexpand=True, transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.pick_zone.add_named(self.status_page, "vide")
        self.pick_zone.add_named(self.roulette_bande, "carrousel")
        proposal.append(self.pick_zone)

        # Centrés pour tenir sous le carrousel centré. `xalign=0.5` et NON le
        # seul `justify` : la justification porte sur les lignes qui REMPLISSENT
        # la ligne, et la dernière d'un paragraphe ne l'est jamais — sans
        # `xalign`, le dernier trait part donc à gauche.
        self.pick_name = Gtk.Label(xalign=0.5, wrap=True, css_classes=["title-2", "revel"])
        self.pick_desc = Gtk.Label(xalign=0.5, wrap=True, justify=Gtk.Justification.CENTER,
                                   css_classes=["dim-label", "revel"])
        proposal.append(self.pick_name)
        proposal.append(self.pick_desc)

        self.btn_yes = Gtk.Button(label=tr("Play"))
        self.btn_yes.add_css_class("suggested-action")
        self.btn_yes.add_css_class("pill")
        self.btn_yes.connect("clicked", self._accept)
        self.btn_no = Gtk.Button(label=tr("Another"))
        self.btn_no.add_css_class("pill")
        self.btn_no.connect("clicked", self._on_other)
        self.choice_box = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER)
        self.choice_box.add_css_class("revel")
        self.choice_box.append(self.btn_no)
        self.choice_box.append(self.btn_yes)
        proposal.append(self.choice_box)
        proposal_scroll = Gtk.ScrolledWindow(child=proposal, hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)

        self.pick_stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT, vexpand=True)
        self.pick_stack.add_named(filters_scroll, "filters")
        self.pick_stack.add_named(proposal_scroll, "proposal")
        self._refresh_moods()
        self._refresh_pick_collections()
        self.time_row.connect("notify::selected", self._on_time_changed)  # changer un filtre annule le choix
        self._spin_id, self._roulette_rang, self._roulette_gen = None, 0, 0
        self._show_filters()  # on démarre sur l'écran des filtres
        return self.pick_stack

    def _show_filters(self):
        self.pick_stack.set_visible_child_name("filters")

    def _reset_pick(self):
        """Changer un filtre du tirage annule la proposition en cours."""
        self.pool, self.current, self._tires = [], None, set()
        self._arreter_roulette()
        self._vide_roulette()
        self.pick_zone.set_visible_child_name("vide")
        self.pick_name.set_text("")
        self.pick_desc.set_text("")
        # Un tirage interrompu (filtre changé à mi-course) aurait laissé nom et
        # boutons à opacité 0 : invisibles et pourtant toujours cliquables.
        self._reveler_infos()
        self._show_filters()  # retour à l'écran des filtres

    def _refresh_moods(self):
        opts = P.mood_options(self.con, self.pick_owned)
        if opts == self._mood_opts:
            return
        self._mood_opts = opts
        self.mood_selected &= set(opts)
        self.exclude_selected &= set(opts)
        while (child := self.mood_flow.get_first_child()):
            self.mood_flow.remove(child)
        self._tag_buttons = {}
        for o in opts:
            b = Gtk.Button(label=o, tooltip_text=tr("Left-click: include · Right-click: exclude"))
            b.add_css_class("pill")
            b.connect("clicked", self._on_tag_left, o)
            right = Gtk.GestureClick(button=3)  # clic droit : exclure
            right.connect("pressed", self._on_tag_right, b, o)
            b.add_controller(right)
            self.mood_flow.append(b)
            self._tag_buttons[o] = b
        self._after_tag_change()

    def _render_tag(self, btn, opt):
        """Colore un tag : inclus (bleu), exclu (rouge), sinon neutre."""
        btn.remove_css_class("suggested-action")
        btn.remove_css_class("destructive-action")
        if opt in self.exclude_selected:
            btn.add_css_class("destructive-action")
        elif opt in self.mood_selected:
            btn.add_css_class("suggested-action")

    def _on_tag_left(self, _btn, opt):
        """Clic gauche : inclure (bascule). Inclure un tag exclu le désexclut."""
        if opt in self.mood_selected:
            self.mood_selected.discard(opt)
        else:
            self.mood_selected.add(opt)
            if opt in self.exclude_selected:
                self.exclude_selected.discard(opt)
                P.set_excluded_tags(self.con, self.exclude_selected)
        self._after_tag_change()

    def _on_tag_right(self, _gesture, _n_press, _x, _y, btn, opt):
        """Clic droit : exclure (bascule). Exclure un tag inclus le dé-inclut."""
        if opt in self.exclude_selected:
            self.exclude_selected.discard(opt)
        else:
            self.exclude_selected.add(opt)
            self.mood_selected.discard(opt)
        P.set_excluded_tags(self.con, self.exclude_selected)  # préférence mémorisée
        self._after_tag_change()

    def _on_clear_tags(self, _btn):
        """Vider : aucun tag ni inclus ni exclu — le tirage ne retient rien."""
        self.mood_selected.clear()
        self.exclude_selected.clear()
        P.set_excluded_tags(self.con, self.exclude_selected)  # la purge suit la mémoire
        self._after_tag_change()

    def _after_tag_change(self):
        for o, b in self._tag_buttons.items():  # états exclusifs : on recolore tout
            self._render_tag(b, o)
        self._update_tags_summary()
        self._update_mood_mode()
        self.clear_tags_btn.set_sensitive(bool(self.mood_selected or self.exclude_selected))
        self._reset_pick()

    def _on_pick_owned(self, chk):
        """Le tirage prend (ou non) en compte les jeux possédés non installés ; les tags suivent."""
        self.pick_owned = chk.get_active()
        P.set_setting(self.con, "pick_owned", "1" if self.pick_owned else "0")
        self._refresh_moods()  # les tags des non installés apparaissent/disparaissent
        self._reset_pick()     # la proposition en cours est caduque

    def _on_mood_mode_changed(self, row, _pspec):
        self.mood_mode = "all" if row.get_selected() == 1 else "any"
        P.set_setting(self.con, "mood_match", self.mood_mode)
        self._reset_pick()

    def _on_time_changed(self, *_):
        self._reset_pick()

    def _update_mood_mode(self):
        """Le mode OU/ET n'a de sens qu'avec au moins un tag inclus — sinon il n'y
        a rien à combiner."""
        self.mood_mode_row.set_sensitive(bool(self.mood_selected))

    def _update_tags_summary(self):
        """Le sous-titre dit la portée du filtre d'un seul coup d'œil (« + Puzzle
        − Roguelike ») : les pastilles bleues et rouges se voient, mais pas toutes
        ensemble. Sans rien de retenu, il rappelle au passage l'astuce des clics.

        L'échappement n'est pas optionnel : le label du description interprète le
        BALISAGE GTK, et un tag « Action & Adventure » contient un « & » nu, qui
        invalide le balisage. GTK ne signale rien alors — il se contente de
        n'afficher PLUS RIEN du tout sous le titre, ce qui passerait inaperçu.
        """
        txt = P.tags_summary(self.mood_selected, self.exclude_selected)
        self.tags_group.set_description(GLib.markup_escape_text(
            txt or tr("Left-click: include · Right-click: exclude")))

    def _fetch_tags_bg(self):
        if getattr(self, "_tagging", False):  # une récupération est déjà en cours
            return
        self._tagging = True

        def work():
            try:
                con = P.db()
                n = P.fetch_missing_tags(con)
                con.close()
            finally:
                self._tagging = False
            if n:
                GLib.idle_add(self._after_tags)
        threading.Thread(target=work, daemon=True).start()

    def _after_tags(self):
        self._refresh_moods()
        self._build_lib()
        return False

    def _refresh_current_cover(self):
        """Une couverture vient de changer (réglages, réimport) : on ne recharge QUE
        la jaquette du jeu en cours. Reconstruire toute la bande échangerait des
        textures déjà décodées pour un seul art nouveau, et ferait repartir le
        tirage alors que l'utilisateur ne demande qu'une image."""
        if not self.current:
            return
        row = self.con.execute("SELECT * FROM games WHERE id=?", (self.current["id"],)).fetchone()
        pic = self.roulette_bande.jaquette_de(self.current["id"])
        if row and pic is not None:
            self._charger_couvertures([row], self.roulette_bande._pics.index(pic))

    def _eligible_pool(self, include_not_installed=False):
        minutes = DUREES[self.time_row.get_selected()]
        mood = sorted(self.mood_selected) or None
        exclude = sorted(self.exclude_selected) or None
        allowed = None
        if self.pick_col_selected:
            allowed = set()
            for cid in self.pick_col_selected:
                allowed |= P.collection_game_ids(self.con, cid)
        now = time.time()
        sql = "SELECT * FROM games"
        sc = P.source_cond(self.con)
        if sc:
            sql += " WHERE " + sc
        pool = [(g, P.weight(g, minutes, mood, now, self.mood_mode, include_not_installed, exclude))
                for g in self.con.execute(sql)]
        pool = [(g, w) for g, w in pool if allowed is None or g["id"] in allowed]
        return [(g, w) for g, w in pool if w > 0]

    def _on_choose(self, *_):
        # UN SEUL bassin : l'option « prendre en compte les jeux non installés »
        # mélange les possédés aux installés dès le premier tirage. L'ancienne
        # progression — épuiser les installés, puis passer aux possédés — tirait
        # un seul jeu quand un seul était installé : mesuré, « Action Roguelike »
        # (1 installé, 18 possédés) ne sortait que SULFUR, et ne découvrait les
        # 18 autres qu'au second « Encore un ».
        self.pool = self._eligible_pool(self.pick_owned)
        if not self.pool:
            self.toasts.add_toast(Adw.Toast.new(tr("Nothing eligible")))
            return
        self.current = None
        self._tires = set()
        self._propose()

    def _propose(self):
        if not self.pool:  # bassin perdu (filtre changé) : on le retélécharge
            self.pool = self._eligible_pool(self.pick_owned)
            self._tires = set()
        if not self.pool:
            self.toasts.add_toast(Adw.Toast.new(tr("Nothing eligible")))
            return
        # Le bassin n'est PAS épuisé au fil des « Autre » : il sert aussi à
        # composer la bande. C'est `tirer_candidats` qui écarte les jeux déjà
        # montrés et le gagnant sortant.
        tirables, self._tires = P.tirer_candidats(
            self.pool, self._tires, self.current["id"] if self.current else None)
        if not tirables:
            self.toasts.add_toast(Adw.Toast.new(tr("Nothing eligible")))
            return
        g = random.choices([p[0] for p in tirables], [p[1] for p in tirables])[0]
        self.current = g
        self._tires.add(g["id"])
        self.pick_stack.set_visible_child_name("proposal")   # on quitte l'écran des filtres
        self._montrer_jeu(g)               # la bande défile, le nom n'apparaît qu'à l'arrêt

    # ---------- carrousel de jaquettes ----------
    def _montrer_jeu(self, g):
        """Tire le jeu `g` : la bande défile, puis le nom, le détail et les boutons
        apparaissent. Le résultat ne se dévoile QU'À L'ARRÊT — pendant la rotation
        il est déjà écrit, mais invisible (`_masquer_infos`).

        Sauf quand il n'y a rien à tirer : un seul jeu éligible est posé au centre
        et dévoilé d'emblée, sans faire patienter 2 800 ms devant un résultat déjà
        décidé (voir `_poser_sans_rotation`)."""
        self.pick_name.set_text(g["name"])
        desc = P.status_label(g["status"])
        if not g["installed"]:
            desc += " · " + tr("not installed")
        if g["playtime_min"]:
            desc += " · " + tr("{t} played", t=P.fmt_minutes(g["playtime_min"]))
        if g["store_tags"]:
            desc += "\n" + " · ".join(g["store_tags"].split(",")[:4])
        self.pick_desc.set_text(desc)
        self.btn_yes.set_label(tr("Play") if g["installed"] else tr("Store page"))
        self.pick_zone.set_visible_child_name("carrousel")
        self._rouler(g)

    def _rouler(self, g):
        """Fait défiler la bande jusqu'à ce que `g` occupe le centre.

        La bande n'est PAS reconstruite : elle est rognée à gauche de ce qui a déjà
        défilé, puis allongée à droite de nouvelles jaquettes. Reconstruire laisserait
        LA MÊME CIBLE à chaque tirage — le gagnant occupe toujours la même case, et
        l'offset est déjà à cette case, donc il n'y aurait RIEN À ANIMER : « Autre »
        réaffichait le résultat d'un coup. Les jaquettes déjà en place restent les
        mêmes widgets : leurs textures ne sont pas perdues, et le raccord se produit
        hors du cadre, où tout est flou et en mouvement.

        Sauf pour un seul jeu éligible, seul cas où il n'y a rien à dérouler : la
        bande est alors REMPLACÉE, et posée au centre sans rotation.
        """
        bande = self.roulette_bande
        if len(self.pool) <= 1:
            # Un seul jeu éligible : la bande n'a ni voisins à montrer ni distance
            # à parcourir. On la REMPLACE plutôt que de la rognner puis l'allonger —
            # garder les 7 dernières jaquettes y laisserait des COPIES floutées du
            # même jeu encadrant le centre, autrefois invisibles puisque tout
            # défilait, et qui se verraient désormais au repos.
            self._roulette_gen += 1        # invalide les chargements encore en vol
            bande.remplacer([g])
            self._roulette_rang = 0
            self._charger_couvertures([g], 0)
            self._poser_sans_rotation()
            return
        premier, dernier, rang = P.roulette_rogne(len(bande._pics), self._roulette_rang)
        bande.rogner(premier, dernier)
        self._roulette_rang = rang
        self._roulette_gen += 1            # invalide les chargements encore en vol
        depart = len(bande._pics)          # première case NEUVE de la bande
        # Les jaquettes conservées sont déjà à l'écran : `roulette_strip` les
        # écarte de ses remplies, sans quoi le gagnant pourrait s'y trouver déjà —
        # la cible serait alors derrière le point de départ et l'animation
        # partirait à rebours.
        gardes = [p._jeu_id for p in bande._pics[:depart]]
        nouveaux = P.roulette_strip(self.pool, g["id"], ROULETTE_JAQUETTES,
                                    deja_visibles=gardes)
        bande.ajouter(nouveaux)
        # Rang LU dans la bande, jamais déduit de `slots - 3` : la règle de
        # placement appartient à `roulette_strip`, et la dupliquer la ferait
        # dériver dès que l'une des deux évolue.
        self._roulette_rang = bande.index_de(g["id"], depart)
        self._charger_couvertures(nouveaux, depart)
        # Le résultat n'est masqué qu'ICI, et nulle part ailleurs : la rotation
        # n'existe que pour retarder la révélation, donc à défaut de rotation il
        # n'y a rien à masquer. Toujours dans le même passage synchrone, donc
        # aucun cadre n'est peint entre les deux — pas de scintillement.
        self._masquer_infos()
        self._lancer_defilement()

    def _masquer_infos(self):
        """Nom, détail et boutons pendant la rotation.

        L'OPACITÉ, et non `set_visible(False)` : un widget invisible libère sa
        place, la bande s'élargirait d'un coup au moment de la révélation."""
        for w in (self.pick_name, self.pick_desc, self.choice_box):
            w.set_opacity(0.0)
            w.set_sensitive(False)

    def _reveler_infos(self):
        for w in (self.pick_name, self.pick_desc, self.choice_box):
            w.set_sensitive(True)
            w.set_opacity(1.0)


    def _vide_roulette(self):
        self.roulette_bande.vider()

    def _charger_couvertures(self, jeux, index_debut=0):
        """Charge les couvertures EN TÂCHE DE FOND, à partir de la case `index_debut`.

        45 couvertures décodées d'un coup geleraient l'interface pour ~250 ms, ce
        qui se verrait sur un défilement. Mesuré : 239 ms en série, 59 ms à quatre
        fils — assez pour que l'art soit déjà en place aux premiers tours. Les
        jaquettes sans art restent des « dos de carte » (`.roulette-case`) : un
        TROU dans une bande qui tourne se verrait immédiatement.

        `index_debut` parce qu'un tirage n'ALLONGE la bande que : seules les
        jaquettes neuves sont décodées, les autres gardent leur texture.

        `gen` nomme la bande à laquelle appartiennent les jaquettes. « Encore un »
        travaille sur la bande pendant qu'un vieux fil tourne encore ; sans ce
        jeton, ces textures atterriraient sur des cases qui ne sont plus les leurs
        — de fausses couvertures, sans la moindre erreur pour le signaler.
        """
        gen = self._roulette_gen

        def charger(g):
            f = P.cover_file(g)
            if not f:
                return None
            try:
                return Gdk.Texture.new_for_pixbuf(GdkPixbuf.Pixbuf.new_from_file_at_scale(
                    str(f), ROULETTE_L, ROULETTE_H, True))
            except Exception:
                return None

        def work():
            with ThreadPoolExecutor(max_workers=4) as ex:
                for i, tex in enumerate(ex.map(charger, jeux)):
                    GLib.idle_add(self._poser_couverture, gen, index_debut + i, tex)

        threading.Thread(target=work, daemon=True).start()

    def _poser_couverture(self, gen, i, texture):
        if gen != self._roulette_gen:
            return False                       # bande remplacée depuis : on jette
        pics = self.roulette_bande._pics
        if i < len(pics):
            pics[i].set_paintable(texture)
            if texture is not None:
                pics[i].remove_css_class("roulette-case")   # l'art remplace le dos
        return False

    def _poser_sans_rotation(self):
        """Un seul jeu éligible : rien à tirer, donc rien à faire défiler.

        Une rotation de 2 800 ms ne ferait que faire patienter devant un résultat
        déjà décidé. On pose la jaquette au centre, nette, et nom, détail et
        boutons sont là d'emblée — jamais masqués, donc jamais révélés.

        L'ATTENTE est la même que pour une rotation, et tout aussi nécessaire : au
        premier lancement, la bande n'a pas encore été allouée — `pick_zone` vient
        de basculer sur le carrousel, et GTK n'a pas encore dessiné de frame. Elle
        mesure alors 0, `cible_centre` renvoie 85 au lieu de −460, et la jaquette
        se retrouve à x = −85, à MOITIÉ hors du cadre. C'était exactement le cas à
        son signalement — corrigé d'un coup par « Autre », la bande étant alors
        dimensionnée.
        """
        self._arreter_roulette()
        self._spin_id = GLib.timeout_add(16, self._pose_lors_alloue)

    def _pose_lors_alloue(self):
        bande = self.roulette_bande
        if not bande._pics:
            self._spin_id = None
            return False
        if bande.get_width() <= 0:
            return True          # GTK n'a pas encore alloué : on réessaie à la frame suivante
        bande.set_offset(bande.cible_centre(self._roulette_rang))
        self._deflouter_gagnant()
        self._reveler_infos()
        self._spin_id = None
        return False

    def _lancer_defilement(self):
        """Attend l'allocation de la bande, puis la fait défiler jusqu'à centrer le jeu."""
        self._arreter_roulette()
        self._deflouter_tout()
        self._spin_id = GLib.timeout_add(16, self._attente_allocation)

    def _attente_allocation(self):
        bande = self.roulette_bande
        if not bande._pics:
            self._spin_id = None
            return False
        if bande.get_width() <= 0:
            return True          # GTK n'a pas encore alloué : on réessaie à la frame suivante
        self._roulette_anim = (bande._offset, bande.cible_centre(self._roulette_rang),
                               GLib.get_monotonic_time())
        self._spin_id = GLib.timeout_add(16, self._pas_de_temps)
        return False

    def _pas_de_temps(self):
        depart, cible, debut = self._roulette_anim
        t = min((GLib.get_monotonic_time() - debut) / 1e6 / (ROULETTE_DUREE_MS / 1000), 1.0)
        self.roulette_bande.set_offset(depart + (cible - depart) * ROULETTE_EASE(t))
        if t >= 1:
            self._spin_id = None
            self._deflouter_gagnant()
            self._reveler_infos()          # c'est ici, et nulle part ailleurs, qu'on découvre
            return False
        return True

    def _bordure_a_change(self, decal):
        """Le cadre a changé de largeur pendant un défilement.

        La bande a déjà décalé son offset de `decal` (−Δ/2) ; les bornes de
        l'animation doivent suivre, sinon le tick suivant — 16 ms plus tard —
        recalcule l'offset à partir des anciennes bornes et annule le décalage :
        le gagnant atterrit alors hors centre, exactement le défaut signalé.
        `depart` et `cible` ne servent qu'au calcul de l'interpolation, leur
        décaler tous les deux ne bouge pas la trajectoire, seulement son repère.
        """
        if getattr(self, "_roulette_anim", None) is None:
            return
        depart, cible, debut = self._roulette_anim
        self._roulette_anim = (depart + decal, cible + decal, debut)

    def _arreter_roulette(self):
        if getattr(self, "_spin_id", None) is not None:
            GLib.source_remove(self._spin_id)
            self._spin_id = None

    def _deflouter_tout(self):
        for pic in self.roulette_bande._pics:
            pic.add_css_class("roulette-flou")

    def _deflouter_gagnant(self):
        """Une seule règle : flou sauf la case centrale. À l'arrêt, le gagnant
        l'occupe — il redevient net, ses voisins restent floutés autour de lui.

        On défloute la case CENTRALE, et non « toutes les jaquettes du gagnant » :
        sur un très petit bassin le même jeu peut revenir ailleurs dans la bande,
        et deux jaquettes nettes apparaîtraient d'un coup."""
        for i, pic in enumerate(self.roulette_bande._pics):
            if i == self._roulette_rang:
                pic.remove_css_class("roulette-flou")
            else:
                pic.add_css_class("roulette-flou")

    def _accept(self, *_):
        """Valide la proposition : lance un jeu installé, ouvre la fiche magasin d'un jeu possédé."""
        if self.current and not self.current["installed"]:
            self._open_store(self.current)
        else:
            self._launch(self.current)

    def _open_store(self, g):
        url = P.store_page(g)
        if url and P.open_url(url):
            self.toasts.add_toast(Adw.Toast.new(tr("Opened store page")))
        else:
            self.toasts.add_toast(Adw.Toast.new(tr("No store page for this game.")))

    def _on_other(self, *_):
        # On ne vide PAS le bassin : la bande visible en est composée, et le
        # vider faisait dégénérer le carrousel (longueurs 7, 12, 10, 1, 6, 9 et
        # 3 jeux distincts sur 4 éligibles, mesurés sur le tag « voxel »).
        # C'est `tirer_candidats`, via `_tires`, qui évite les répétitions.
        self._propose()

    def _launch(self, g):
        """Lance le jeu (ou ouvre sa fiche magasin). Le carrousel reste affiché et le
        jeu lancé reste net au centre : le message de réussite remplace le détail,
        pas la jaquette — c'est elle qui dit quel jeu est parti."""
        ok = P.launch(self.con, g, on_exit=self._on_game_exit)
        self.pick_name.set_text(g["name"])
        self.pick_desc.set_text(
            tr("▶ Launched. Have fun!") if ok else tr("Couldn't launch: is the game still installed?"))

    def _refresh_pick_collections(self):
        if not hasattr(self, "collections_pop"):
            return
        cols = P.list_collections(self.con, include_hidden=self.show_hidden)
        self.pick_col_selected &= {c["id"] for c in cols}
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                      margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
        self._pick_all_cb = Gtk.CheckButton(label=tr("All games"), active=not self.pick_col_selected)
        self._pick_all_cb.connect("toggled", self._on_pick_all_toggled)
        box.append(self._pick_all_cb)
        self._pick_checks = {}
        for c in cols:
            cb = Gtk.CheckButton(label=self._col_label(c), active=c["id"] in self.pick_col_selected)
            cb.connect("toggled", self._on_pick_col_toggled, c["id"])
            self._pick_checks[c["id"]] = cb
            box.append(cb)
        self.collections_pop.set_child(box)
        self._update_pick_col_label()

    def _update_pick_col_label(self):
        n = len(self.pick_col_selected)
        txt = tr("All games") if not n else tr("{n} collection(s)", n=n)
        self.collections_btn.set_child(Gtk.Label(label=txt))

    def _on_pick_col_toggled(self, btn, cid):
        if btn.get_active():
            self.pick_col_selected.add(cid)
        else:
            self.pick_col_selected.discard(cid)
        self._pick_all_cb.set_active(not self.pick_col_selected)
        self._update_pick_col_label()
        self._reset_pick()

    def _on_pick_all_toggled(self, btn):
        if btn.get_active():
            self.pick_col_selected.clear()
            for cb in self._pick_checks.values():
                cb.set_active(False)
            self._update_pick_col_label()
            self._reset_pick()
        elif not self.pick_col_selected:  # on ne laisse jamais « tout decoche »
            btn.set_active(True)
