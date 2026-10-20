"""library.py — page « Bibliothèque » : grille, tri, filtres et collections."""
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, GObject, Gtk, Pango

import picker as P
from i18n import available, get_lang, tr
from .theme import STATUTS, TILE_W, TILE_H, COVER_PX


def _est_croix(b):
    """Ce bouton porte-t-il l'icône « fermer » d'Adw ?

    La croix automatique d'`Adw.Dialog` est un bouton SANS propriété
    `icon-name` : l'icône est portée par son enfant `Image` — d'où la
    recherche sur le bouton puis sur ses enfants directs."""
    if b.get_icon_name() == "window-close-symbolic":
        return True
    c = b.get_first_child()
    while c is not None:
        if isinstance(c, Gtk.Image) and c.get_icon_name() == "window-close-symbolic":
            return True
        c = c.get_next_sibling()
    return False


class LibraryMixin:

    # ---------- page « Bibliothèque » : grille de pochettes ----------
    def _build_library_page(self):
        self.search = Gtk.SearchEntry(hexpand=True)
        if hasattr(self.search, "set_placeholder_text"):  # GTK >= 4.10
            self.search.set_placeholder_text(tr("Search games…"))
        self.search.connect("search-changed", self._apply_filter)
        self.search_bar = Gtk.SearchBar(child=Adw.Clamp(maximum_size=420, child=self.search))
        self.search_bar.connect_entry(self.search)

        # La barre d'outils de la liste : les commandes de la bibliothèque vivent
        # AU-DESSUS des tuiles, dans le volet qui les porte, et non plus dans
        # l'en-tête de la fenêtre (qui ne garde qu'Importer, les onglets et les
        # Réglages). Trier et Filtrer y sont deux menus distincts.
        # Tous les boutons sont ICÔNES SEULES : les libellés sont portés par les
        # info-bulles, sinon la rangée déborde la barre centrée de peu de largeur.
        self.search_btn = Gtk.ToggleButton(icon_name="system-search-symbolic", tooltip_text=tr("Search"))
        self.search_btn.bind_property("active", self.search_bar, "search-mode-enabled",
                                      GObject.BindingFlags.BIDIRECTIONAL)
        self.sort_btn = Gtk.MenuButton(icon_name="view-sort-descending-symbolic", tooltip_text=tr("Sort"),
                                       menu_model=self._build_sort_menu())
        self.filter_btn = Gtk.MenuButton(icon_name="view-filter-symbolic", tooltip_text=tr("Filter"),
                                         menu_model=self._build_filter_menu())
        # Sans libellé, une CheckButton ne montrerait que la coche, muette sur son
        # objet : un ToggleButton à icône porte l'état dans son symbole actif/inactif.
        self.not_installed_chk = Gtk.ToggleButton(icon_name="view-reveal-symbolic",
                                                  tooltip_text=tr("Show not installed games"),
                                                  active=self.show_not_installed)
        self.not_installed_chk.connect("toggled", self._on_show_not_installed)
        # Mode sélection : cocher plusieurs tuiles pour les pousser dans une
        # collection d'un geste — la bascule est dans la barre, la barre du bas
        # porte le compte et l'ajout.
        self.select_btn = Gtk.ToggleButton(icon_name="edit-select-all-symbolic",
                                           tooltip_text=tr("Select games"))
        self.select_btn.connect("toggled", self._on_select_mode)
        self.add_btn = Gtk.Button(icon_name="list-add-symbolic", tooltip_text=tr("Add a game"))
        self.add_btn.connect("clicked", self._open_add_game)
        lib_bar = Gtk.Box(spacing=6, margin_top=8, margin_bottom=4, margin_start=12, margin_end=12,
                          halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, css_classes=["toolbar"])
        for w in (self.search_btn, self.sort_btn, self.filter_btn, self.not_installed_chk,
                  self.select_btn, self.add_btn):
            lib_bar.append(w)

        self.lib_flow = Gtk.FlowBox(valign=Gtk.Align.START, homogeneous=True, selection_mode=Gtk.SelectionMode.NONE,
                                    activate_on_single_click=True, min_children_per_line=2, max_children_per_line=12,
                                    column_spacing=14, row_spacing=14,
                                    margin_top=14, margin_bottom=18, margin_start=18, margin_end=18)
        self.lib_flow.set_filter_func(self._match)
        self.lib_flow.set_sort_func(self._sort_func)
        self.lib_flow.connect("child-activated", self._on_tile_activated)
        self.lib_flow.connect("selected-children-changed", lambda *_: self._update_sel_bar())
        # Mémoire de clic : le FlowBox, en MULTIPLE, coche au clic mais ne décoche
        # JAMAIS. L'état « la tuile était déjà cochée » n'existe qu'entre l'appui et
        # le relâchement — une capture le voit avant le traitement interne du
        # FlowBox ; l'activation clavier (Entrée), elle, la voit toujours à None et
        # ne désélectionne rien.
        self._pressed_sel = None
        cap = Gtk.GestureClick(button=1)
        cap.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        cap.connect("pressed", self._on_lib_capture)
        self.lib_flow.add_controller(cap)
        self.lib_scroll = Gtk.ScrolledWindow(child=self.lib_flow, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.lib_empty = Adw.StatusPage(icon_name="view-grid-symbolic", vexpand=True)
        self.lib_stack = Gtk.Stack()
        self.lib_stack.add_named(self.lib_scroll, "grid")
        self.lib_stack.add_named(self.lib_empty, "empty")

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.append(lib_bar)
        content.append(self.search_bar)
        content.append(self.lib_stack)

        # « Jeu en cours » : la frontière d'une session n'est pas observable
        # partout — un jeu web chez itch tourne dans la fenêtre du client, sans
        # aucun processus à suivre. La bibliothèque porte alors la frontière :
        # le nom du jeu et « Terminer la session », en bas de la liste. La barre
        # ne se montre que si une session est ouverte.
        self.session_bar = Gtk.Box(spacing=10, halign=Gtk.Align.CENTER,
                                   margin_top=2, margin_bottom=10, visible=False)
        self.session_lbl = Gtk.Label(css_classes=["dim-label"])
        fin_box = Gtk.Box(spacing=6)
        fin_box.append(Gtk.Image(icon_name="media-playback-stop-symbolic"))
        fin_box.append(Gtk.Label(label=tr("End session")))
        self.session_fin = Gtk.Button(child=fin_box, css_classes=["pill"])
        self.session_fin.connect("clicked", self._on_end_session)
        self.session_bar.append(self.session_lbl)
        self.session_bar.append(self.session_fin)
        self.session_sid = None
        self._maj_session_bar()  # session héritée d'une exécution précédente : visible

        # « Sélection » : mode multi-choix des tuiles → barre en bas de la liste,
        # au-dessus de « Jeu en cours », avec le compte en toutes lettres
        # (« {n} jeu(s) sélectionné(s) ») et trois actions en bloc : « Collections »
        # (popover à COCHES : cocher y ajoute la sélection entière, décocher
        # l'en retire — la sélection reste en place pour enchaîner — + « Nouvelle
        # collection… »), masquage / démasquage (pilule à libellé DYNAMIQUE :
        # « Démasquer » quand toute la sélection est déjà masquée) et statut
        # (popover des cinq statuts de la fiche). Comme la barre de session, elle
        # ne se montre que dans son état.
        self.sel_bar = Gtk.Box(spacing=10, halign=Gtk.Align.CENTER, margin_top=2,
                               margin_bottom=10, visible=False)
        self.sel_lbl = Gtk.Label(css_classes=["dim-label"])
        self.sel_menu = Gtk.MenuButton(label=tr("Collections"), css_classes=["pill"])
        self.sel_pop = Gtk.Popover()
        self.sel_menu.set_popover(self.sel_pop)
        self.sel_pop.connect("map", lambda *_: self._rebuild_sel_pop())  # ouvert : liste fraîche
        self.sel_hide_btn = Gtk.Button(label=tr("Hide"), css_classes=["pill"])
        self.sel_hide_btn.connect("clicked", self._on_hide_selection)
        self.sel_status_menu = Gtk.MenuButton(label=tr("Status"), css_classes=["pill"])
        self.sel_status_pop = Gtk.Popover()
        self.sel_status_menu.set_popover(self.sel_status_pop)
        self.sel_status_pop.connect("map", lambda *_: self._rebuild_status_pop())
        self.sel_cancel = Gtk.Button(icon_name="window-close-symbolic", css_classes=["flat"],
                                     tooltip_text=tr("Cancel selection"))
        self.sel_cancel.connect("clicked", lambda *_: self.select_btn.set_active(False))
        self.sel_bar.append(self.sel_lbl)
        self.sel_bar.append(self.sel_menu)
        self.sel_bar.append(self.sel_hide_btn)
        self.sel_bar.append(self.sel_status_menu)
        self.sel_bar.append(self.sel_cancel)
        content.append(self.sel_bar)
        content.append(self.session_bar)

        # barre latérale : « Tous les jeux » + une ligne par collection
        self.col_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["navigation-sidebar"])
        self.col_list.set_activate_on_single_click(False)  # row-activated = double-clic (ou Entrée) = isoler
        self.col_list.connect("row-activated", self._on_col_activated)
        col_scroll = Gtk.ScrolledWindow(child=self.col_list, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        head = Gtk.Box(spacing=6, margin_top=8, margin_bottom=6, margin_start=12, margin_end=6)
        title_lbl = Gtk.Label(label=tr("Collections"), xalign=0, hexpand=True, css_classes=["heading"])
        add_col = Gtk.Button(icon_name="list-add-symbolic", css_classes=["flat"], tooltip_text=tr("New collection"))
        add_col.connect("clicked", self._on_new_collection)
        head.append(title_lbl)
        head.append(add_col)
        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        sidebar.append(head)
        sidebar.append(col_scroll)

        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.set_start_child(sidebar)
        paned.set_end_child(content)
        paned.set_resize_start_child(False)
        paned.set_shrink_start_child(False)
        paned.set_position(200)
        return paned

    def _build_sort_menu(self):
        """Menus « Trier » et « Filtrer » de la barre de la liste — leurs deux
        actions d'état sont créées ici (les deux menus les partagent), le
        premier appel venant du bouton « Trier »."""
        for name, initial, callback in (("lib-sort", self.lib_sort, self._on_sort),
                                        ("lib-filter", self.lib_filter, self._on_filter)):
            act = Gio.SimpleAction.new_stateful(name, GLib.VariantType.new("s"), GLib.Variant.new_string(initial))
            act.connect("activate", callback)
            self.add_action(act)
        menu, sort_sec = Gio.Menu(), Gio.Menu()
        for key, label in (("name", tr("Name")), ("recent", tr("Last played")), ("played", tr("Most played"))):
            sort_sec.append(label, f"win.lib-sort::{key}")
        menu.append_section(tr("Sort by"), sort_sec)
        return menu

    def _build_filter_menu(self):
        menu, filter_sec = Gio.Menu(), Gio.Menu()
        filter_sec.append(tr("All"), "win.lib-filter::all")
        for s in STATUTS:
            filter_sec.append(P.status_label(s), f"win.lib-filter::{s}")
        menu.append_section(tr("Status"), filter_sec)
        return menu

    def _on_sort(self, action, param):
        action.set_state(param)
        self.lib_sort = param.get_string()
        P.set_setting(self.con, "lib_sort", self.lib_sort)
        self.lib_flow.invalidate_sort()

    def _on_filter(self, action, param):
        action.set_state(param)
        self.lib_filter = param.get_string()
        self._apply_filter()

    def _on_page_changed(self, *_):
        page = self.stack.get_visible_child_name()
        if page == "retro":
            self._refresh_retro()
        if page == "notes":
            self._refresh_notes()  # la liste suit les jeux ajoutés / renommés ailleurs
        else:
            self._flush_note()     # quitter l'onglet enregistre la note en cours
        if page == "achievements":
            self._refresh_achievements()  # les logs du module succès sont relus à l'ouverture
        self._refresh_moods()
        self._refresh_pick_collections()

    def _toggle_search(self, *_):
        if self.stack.get_visible_child_name() == "lib":
            self.search_btn.set_active(not self.search_btn.get_active())
        return True

    def _match(self, child):
        g = child._game
        if self.lib_filter != "all" and g["status"] != self.lib_filter:
            return False
        if self.col_selected and g["id"] not in self.col_game_ids:
            return False
        return self.search.get_text().strip().casefold() in child._hay

    def _sort_key(self, g):
        by = {"name": g["name"].casefold(), "recent": -g["last_played"], "played": -g["playtime_min"]}[self.lib_sort]
        if self.lib_group:
            # « Regrouper les jeux » : installés, puis non installés, puis masqués
            # — le masqué l'emporte, même installé
            return (2 if g["hidden"] else (0 if g["installed"] else 1), by, g["name"].casefold())
        return (by, g["name"].casefold())  # décoché : le tri choisi seul, AUCUN palier

    def _sort_func(self, a, b):
        ka, kb = self._sort_key(a._game), self._sort_key(b._game)
        return (ka > kb) - (ka < kb)

    def _apply_filter(self, *_):
        self.lib_flow.invalidate_filter()
        if any(self._match(c) for c in self._tiles):
            self.lib_stack.set_visible_child_name("grid")
            return
        empty = not self._tiles
        self.lib_empty.set_title(tr("Your library is empty") if empty else tr("No games found"))
        self.lib_empty.set_description(tr("Import your games, or add one by hand with +.") if empty
                                       else tr("Try another search or filter."))
        self.lib_stack.set_visible_child_name("empty")

    def _make_cover(self, g, w, h, big=False):
        """Pochette : fond coloré avec le nom du jeu ; l'image se pose dessus quand elle est chargée."""
        hue = sum(map(ord, g["name"])) % 6
        label = Gtk.Label(label=g["name"], wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, lines=5,
                          ellipsize=Pango.EllipsizeMode.END, max_width_chars=12, justify=Gtk.Justification.CENTER,
                          margin_start=10, margin_end=10, vexpand=True, valign=Gtk.Align.CENTER,
                          css_classes=["title-3" if big else "title-4"])
        placeholder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["game-placeholder", f"ph-{hue}"])
        placeholder.append(label)
        pic = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, can_shrink=True, visible=False)
        cover = Gtk.Overlay(child=placeholder, width_request=w, height_request=h, css_classes=["card"])
        cover.set_overflow(Gtk.Overflow.HIDDEN)
        cover.add_overlay(pic)
        return cover, pic

    def _cover_texture(self, g):
        """Texture réduite de la couverture (gardée en cache tant que le fichier ne change pas)."""
        f = P.cover_file(g)
        if not f:
            return None
        try:
            key = (str(f), f.stat().st_mtime_ns)
        except OSError:
            return None
        if key not in self._tex_cache:
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(f), -1, COVER_PX, True)
                self._tex_cache[key] = Gdk.Texture.new_for_pixbuf(pixbuf)
            except Exception:
                self._tex_cache[key] = None
        return self._tex_cache[key]

    def _cached_cover(self, g):
        """Texture de couverture DÉJÀ décodée, ou None — sans jamais décoder ici :
        le premier décodage asynchrone reste l'affaire de la file, mais une grille
        reconstruite repose les anciennes images au lieu de les faire réapparaître."""
        f = P.cover_file(g)
        if not f:
            return None
        try:
            return self._tex_cache.get((str(f), f.stat().st_mtime_ns))
        except OSError:
            return None

    @staticmethod
    def _image_ok(path):
        """Le fichier est-il une image que GdkPixbuf sait réellement décoder ?"""
        try:
            GdkPixbuf.Pixbuf.new_from_file_at_scale(path, 32, 32, True)
            return True
        except Exception:
            return False

    def _cover_row(self, g, pic):
        """Ligne « Couverture » du dialogue d'édition : choisir une image / retirer la couverture perso.
        La couverture au fond de la fiche (``pic``) suit en direct."""
        row = Adw.ActionRow(title=tr("Cover image"), subtitle=tr("Custom") if g["custom_cover"] else "")
        remove = Gtk.Button(icon_name="edit-delete-symbolic", valign=Gtk.Align.CENTER, css_classes=["flat"],
                            tooltip_text=tr("Remove custom cover"), sensitive=bool(g["custom_cover"]))

        def refresh():
            fresh = self.con.execute("SELECT * FROM games WHERE id=?", (g["id"],)).fetchone()
            custom = bool(fresh and fresh["custom_cover"])
            row.set_subtitle(tr("Custom") if custom else "")
            remove.set_sensitive(custom)
            tex = self._cover_texture(fresh) if fresh else None
            if tex:
                pic.set_paintable(tex)
                pic.set_visible(True)
            else:
                pic.set_visible(False)
            self._build_lib()              # la tuile de la bibliothèque reflète le changement
            self._refresh_current_cover()  # et l'onglet « Choisir » s'il montre ce jeu

        choose = Gtk.Button(label=tr("Choose…"), valign=Gtk.Align.CENTER, css_classes=["flat"])
        choose.connect("clicked", lambda _b: self._pick_cover(g["id"], refresh))
        remove.connect("clicked", lambda _b: self._clear_cover(g["id"], refresh))
        row.add_suffix(choose)
        row.add_suffix(remove)
        return row

    def _pick_cover(self, gid, on_done=None):
        """Sélecteur de fichier filtré sur les images."""
        dlg = Gtk.FileDialog(title=tr("Choose a cover image"))
        filt = Gtk.FileFilter()
        filt.set_name(tr("Images"))
        for pat in ("*.png", "*.jpg", "*.jpeg", "*.webp", "*.bmp", "*.gif"):
            filt.add_pattern(pat)
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(filt)
        dlg.set_filters(filters)
        dlg.set_default_filter(filt)

        def done(d, res):
            try:
                f = d.open_finish(res)
            except GLib.Error:
                return
            if self._apply_cover(gid, f.get_path()) and on_done:
                on_done()
        dlg.open(self, None, done)

    def _apply_cover(self, gid, path):
        """Enregistre l'image choisie comme couverture perso (refuse un fichier illisible)."""
        if not self._image_ok(path):
            self.toasts.add_toast(Adw.Toast.new(tr("Unsupported image file")))
            return False
        try:
            P.set_custom_cover(self.con, gid, path)
        except OSError as e:
            self.toasts.add_toast(Adw.Toast.new(GLib.markup_escape_text(tr("Failed: {e}", e=e))))
            return False
        self.toasts.add_toast(Adw.Toast.new(tr("Custom cover saved")))
        return True

    def _clear_cover(self, gid, on_done=None):
        P.clear_custom_cover(self.con, gid)
        self.toasts.add_toast(Adw.Toast.new(tr("Custom cover removed")))
        if on_done:
            on_done()

    def _load_covers_step(self, gen):
        """Charge les couvertures par petits lots, pour que la fenêtre s'affiche tout de suite."""
        if gen != self._lib_gen:  # la grille a été reconstruite entre-temps
            return False
        for _ in range(3):
            if not self._cover_queue:
                return False
            pic, g = self._cover_queue.pop(0)
            tex = self._cover_texture(g)
            if tex:
                pic.set_paintable(tex)
                pic.set_visible(True)
        return True

    def _make_tile(self, g):
        gid = g["id"]
        cover, pic = self._make_cover(g, TILE_W, TILE_H)
        tex = self._cached_cover(g)
        if tex:
            pic.set_paintable(tex)
            pic.set_visible(True)   # déjà décodée : posée tout de suite, sans vague de chargement
        elif P.cover_file(g):
            self._cover_queue.append((pic, g))
        bar = Gtk.Label(label=g["name"], ellipsize=Pango.EllipsizeMode.END, valign=Gtk.Align.END,
                        css_classes=["game-title-bar"], visible=False)
        play = Gtk.Button(icon_name="media-playback-start-symbolic", halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
                          css_classes=["osd", "circular"], width_request=48, height_request=48,
                          tooltip_text=tr("Play"), visible=False)
        play.connect("clicked", lambda _b, gid=gid: self._quick_launch(gid))
        cover.add_overlay(bar)
        cover.add_overlay(play)
        if g["hidden"]:
            cover.add_css_class("hid-tile")  # masqué : un cran plus grisé que le non installé
        elif not g["installed"]:
            cover.add_css_class("dim-tile")

        def on_hover(ctrl, _p):
            hovered = ctrl.get_property("contains-pointer")
            bar.set_visible(hovered and pic.get_visible())  # le nom n'est utile que sur une vraie couverture
            play.set_visible(hovered and bool(g["installed"]))
        motion = Gtk.EventControllerMotion()
        motion.connect("notify::contains-pointer", on_hover)
        cover.add_controller(motion)

        child = Gtk.FlowBoxChild(child=cover, tooltip_text=g["name"])
        child._game = g
        child._hay = " ".join((g["name"], g["tags"], g["store_tags"])).casefold()
        return child

    def _lib_where(self, trailing=False, sans_installe=False):
        """Filtre SQL de la grille : masqués et non-installés ne comptent/apparaissent que s'ils sont affichés.

        `sans_installe` : la page Notes garde les jeux non installés quel que soit le
        réglage de la bibliothèque — une note ne se perd pas à cause d'un désinstall.
        Les MASQUÉS restent filtrés partout : cachés volontairement, cas différent."""
        conds = []
        if not self.show_hidden:
            conds.append("hidden=0")
        if not sans_installe and not self.show_not_installed:
            conds.append("installed=1")
        sc = P.source_cond(self.con)
        if sc:
            conds.append(sc)
        if not conds:
            return ""
        return "WHERE " + " AND ".join(conds) + (" " if trailing else "")

    def _build_lib(self):
        """(Re)construit la grille ; recherche, tri et filtre agissent ensuite sans rien reconstruire."""
        self._lib_gen += 1
        self._cover_queue = []
        adj = self.lib_scroll.get_vadjustment()
        pos = adj.get_value()
        while (child := self.lib_flow.get_first_child()):
            self.lib_flow.remove(child)
        self._tiles = []
        where = self._lib_where(trailing=True)
        # Temps de jeu affichable : provider s'il en donne un, sinon la somme des sessions
        # (une seule requête pour toute la grille → sert aussi au tri « Le plus joué »).
        pts = P.playtimes(self.con)
        for row in self.con.execute(f"SELECT * FROM games {where}"):
            g = dict(row)
            g["playtime_min"] = pts.get(g["id"], g["playtime_min"])
            tile = self._make_tile(g)
            self.lib_flow.append(tile)
            self._tiles.append(tile)
        self._apply_filter()
        self._refresh_collections()
        GLib.idle_add(self._load_covers_step, self._lib_gen, priority=GLib.PRIORITY_LOW)

        def restore():
            adj.set_value(pos)
            return False
        if pos:
            GLib.timeout_add(80, restore)

    # ---------- collections ----------
    def _recompute_col_ids(self):
        ids = set()
        for cid in self.col_selected:
            ids |= P.collection_game_ids(self.con, cid)
        self.col_game_ids = ids

    def _refresh_collections(self):
        cols = P.list_collections(self.con, include_hidden=self.show_hidden,
                                  include_not_installed=self.show_not_installed)
        self.col_selected &= {c["id"] for c in cols}
        self._build_collections(cols)
        self._recompute_col_ids()
        self._refresh_pick_collections()
        self._apply_filter()

    def _build_collections(self, cols):
        self._building_cols = True
        while (child := self.col_list.get_first_child()):
            self.col_list.remove(child)
        all_row = Adw.ActionRow(title=tr("All games"), activatable=True)
        all_row._cid = None
        self._add_row_click(all_row)
        where = self._lib_where()
        n_all = self.con.execute(f"SELECT COUNT(*) FROM games {where}").fetchone()[0]
        all_row.add_suffix(Gtk.Label(label=str(n_all), css_classes=["dim-label"], valign=Gtk.Align.CENTER))
        self.col_list.append(all_row)
        manual = [c for c in cols if c["kind"] == "manual"]
        auto = [c for c in cols if c["kind"] == "auto"]
        if manual or auto:
            self.col_list.append(self._make_separator_row())
        for c in manual:
            self.col_list.append(self._make_collection_row(c))
        if manual and auto:
            self.col_list.append(self._make_separator_row())
        for c in auto:
            self.col_list.append(self._make_collection_row(c))
        self._update_col_highlight()
        self._building_cols = False

    def _col_label(self, c):
        return tr(c["name"]) if c["kind"] == "auto" else c["name"]

    def _make_collection_row(self, c):
        row = Adw.ActionRow(title=GLib.markup_escape_text(self._col_label(c)), activatable=True)
        row._cid = c["id"]
        self._add_row_click(row)
        row.add_suffix(Gtk.Label(label=str(c["n"]), css_classes=["dim-label"], valign=Gtk.Align.CENTER))
        if c["kind"] == "auto":  # collection automatique : ni renommable ni supprimable
            if P.launcher_command(c["auto_source"]):  # à droite du compte : ouvrir le client
                play = Gtk.Button(icon_name="media-playback-start-symbolic", css_classes=["flat"],
                                  valign=Gtk.Align.CENTER,
                                  tooltip_text=tr("Open {name}", name=self._col_label(c)))
                play.connect("clicked", lambda _b, src=c["auto_source"]: self._on_open_launcher(src))
                row.add_suffix(play)
            return row
        pop = Gtk.Popover()
        menu = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                       margin_top=4, margin_bottom=4, margin_start=4, margin_end=4)
        rename = Gtk.Button(label=tr("Rename"), css_classes=["flat"])
        delete = Gtk.Button(label=tr("Delete"), css_classes=["flat"])
        menu.append(rename)
        menu.append(delete)
        pop.set_child(menu)
        rename.connect("clicked", lambda *_: (pop.popdown(), self._on_rename_collection(c)))
        delete.connect("clicked", lambda *_: (pop.popdown(), self._on_delete_collection(c)))
        row.add_suffix(Gtk.MenuButton(icon_name="view-more-symbolic", css_classes=["flat"],
                                      valign=Gtk.Align.CENTER, popover=pop))
        return row

    def _make_separator_row(self):
        row = Gtk.ListBoxRow(selectable=False, activatable=False, css_classes=["col-sep"])
        row.set_child(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        return row

    def _add_row_click(self, row):
        for button, handler in ((1, self._on_row_primary), (3, self._on_row_secondary)):
            g = Gtk.GestureClick(button=button)
            g.connect("pressed", handler, row)
            row.add_controller(g)
            for c in row.observe_controllers():      # le geste interne de la ligne « claim » le clic :
                if isinstance(c, Gtk.GestureClick) and c is not g:
                    g.group(c)                       # on les groupe pour recevoir aussi l'evenement
                    break

    def _on_row_primary(self, _gesture, _n_press, _x, _y, row):
        """Clic gauche : n'isoler que cette collection."""
        if self._building_cols:
            return
        self._isolate_col(row)

    def _on_row_secondary(self, _gesture, _n_press, _x, _y, row):
        """Clic droit : ajouter / retirer cette collection de la sélection."""
        if self._building_cols:
            return
        self._toggle_col(row)

    def _on_col_activated(self, _lb, row):
        """Entrée clavier (ou double-clic géré par la liste) : n'isole que cette collection."""
        if self._building_cols or row is None:
            return
        self._isolate_col(row)

    def _isolate_col(self, row):
        cid = getattr(row, "_cid", None)
        self.col_selected = set() if cid is None else {cid}
        self._recompute_col_ids()
        self._apply_filter()
        self._update_col_highlight()

    def _toggle_col(self, row):
        cid = getattr(row, "_cid", None)
        if cid is None:                 # « Tous les jeux » : reinitialise la selection
            self.col_selected = set()
        elif cid in self.col_selected:  # recliquer une collection la deselectionne
            self.col_selected.discard(cid)
        else:
            self.col_selected.add(cid)
        self._recompute_col_ids()
        self._apply_filter()
        self._update_col_highlight()

    def _update_col_highlight(self):
        child = self.col_list.get_first_child()
        while child:
            cid = getattr(child, "_cid", None)
            if hasattr(child, "_cid"):  # ligne « Tous les jeux » (None) ou collection
                on = (not self.col_selected) if cid is None else (cid in self.col_selected)
                child.remove_css_class("col-on") if not on else child.add_css_class("col-on")
            child = child.get_next_sibling()

    # ---------- sélection : plusieurs jeux poussés dans une collection ----------
    def _on_select_mode(self, btn):
        """La bascule pilote le mode de la grille : en sélection, le clic coche
        (l'activation des tuiles est neutralisée) ; à la sortie, tout se
        désélectionne et la barre du bas disparaît."""
        on = btn.get_active()
        self.lib_flow.set_selection_mode(Gtk.SelectionMode.MULTIPLE if on else Gtk.SelectionMode.NONE)
        if on:
            self._rebuild_sel_pop()
        else:
            self.lib_flow.unselect_all()
        self._update_sel_bar()

    def _update_sel_bar(self):
        sel = self.lib_flow.get_selected_children() if self.select_btn.get_active() else []
        n = len(sel)
        self.sel_lbl.set_text(tr("{n} game(s) selected", n=n))
        self.sel_bar.set_visible(n > 0)
        if n:
            # pilule dynamique : toute la sélection déjà masquée → « Démasquer »
            self.sel_hide_btn.set_label(tr("Unhide") if all(c._game["hidden"] for c in sel)
                                         else tr("Hide"))

    def _on_lib_capture(self, _gesture, _n_press, x, y):
        """Clic souris : retenir la tuile visée si elle était déjà cochée au moment
        de l'appui — seul moment où cette vérité est observable."""
        self._pressed_sel = None
        if not self.select_btn.get_active():
            return
        child = self.lib_flow.get_first_child()
        while child is not None:
            a = child.get_allocation()
            if a.x <= x < a.x + a.width and a.y <= y < a.y + a.height:
                if child in self.lib_flow.get_selected_children():
                    self._pressed_sel = child
                return
            child = child.get_next_sibling()

    def _on_tile_activated(self, _f, child):
        """Mode sélection : le clic coche (le FlowBox) et décoche (nous) — jamais
        il n'ouvre la fiche."""
        if self.select_btn.get_active():
            if child is self._pressed_sel:      # déjà cochée à l'appui : on bascule
                self.lib_flow.unselect_child(child)
            self._pressed_sel = None
            return
        self._open_game(child._game["id"])

    def _sel_gids(self):
        return [c._game["id"] for c in self.lib_flow.get_selected_children()]

    def _rebuild_sel_pop(self):
        """Le popover « Collections » : une COCHE par collection MANUELLE (les
        automatiques sont en lecture seule) — cocher y ajoute la sélection
        entière, décocher la retire, et la sélection reste en place pour cocher
        plusieurs collections d'affilée, « Nouvelle collection… » compris.
        Reconstruit à l'ouverture (`set_child` remplace l'ancien contenu d'un bloc)."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                      margin_top=4, margin_bottom=4, margin_start=4, margin_end=4)
        cols = [c for c in P.list_collections(self.con) if c["kind"] == "manual"]
        if not cols:
            box.append(Gtk.Label(label=tr("no collections"), css_classes=["dim-label"],
                                 margin_top=4, margin_bottom=4))
        gids = self._sel_gids()
        membres = {c["id"]: P.collection_game_ids(self.con, c["id"]) for c in cols}
        for c in cols:
            # coche = TOUTE la sélection déjà dans la collection (partielle → non
            # cochée : cliquer alors l'y achève, décocher en sort toute la sélection)
            b = Gtk.CheckButton(label=c["name"],
                                active=bool(gids) and all(g in membres[c["id"]] for g in gids),
                                css_classes=["flat"])
            b._cid = c["id"]
            b.connect("toggled", lambda b, cid=c["id"], name=c["name"]:
                      self._on_toggle_collection(cid, name, b))
            box.append(b)
        box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        nouveau = Gtk.Button(label=tr("New collection…"), css_classes=["flat"])
        nouveau._new_col = True
        nouveau.connect("clicked", self._on_new_collection_selection)
        box.append(nouveau)
        self.sel_pop.set_child(box)

    def _toast_ajout(self, n, name):
        self.toasts.add_toast(Adw.Toast.new(tr("{n} game(s) added to “{name}”", n=n, name=name) if n
                                            else tr("Already in this collection")))

    def _on_toggle_collection(self, cid, name, btn):
        """Coche = la sélection ENTIERE rejoint la collection (le manquant est
        ajouté) ; décoche = elle ENTIERE en sort. Ni popdown ni fin de
        sélection : on coche plusieurs collections d'affilée."""
        gids = self._sel_gids()
        if btn.get_active():
            n = P.add_games_to_collection(self.con, cid, gids)
            msg = tr("{n} game(s) added to “{name}”", n=n, name=name) if n \
                else tr("Already in this collection")
        else:
            n = P.remove_games_from_collection(self.con, cid, gids)
            msg = tr("{n} game(s) removed from “{name}”", n=n, name=name) if n \
                else tr("Already out of this collection")
        self.toasts.add_toast(Adw.Toast.new(msg))
        self._refresh_collections()

    def _on_new_collection_selection(self, *_):
        self.sel_pop.popdown()

        def on_ok(name):
            try:
                cid = P.add_collection(self.con, name)
            except ValueError:
                if not name.strip():
                    raise                   # nom vide : le message revient au dialogue
                # nom déjà pris : comme dans la fiche, on ajoute à l'existant
                key = name.strip().casefold()
                cid = next(r["id"] for r in self.con.execute("SELECT id, name FROM collections")
                           if r["name"].strip().casefold() == key)
            n = P.add_games_to_collection(self.con, cid, self._sel_gids())
            self._toast_ajout(n, name)
            self._refresh_collections()   # la sélection reste en place : on peut enchaîner

        self._prompt_collection(tr("New collection"), "", on_ok)

    def _rebuild_status_pop(self):
        """Le popover des statuts : une ligne par statut de la fiche, dans l'ordre
        de `STATUTS` (le même modèle que le sélecteur de la fiche). Reconstruit à
        l'ouverture (`set_child` remplace l'ancien contenu d'un bloc)."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                      margin_top=4, margin_bottom=4, margin_start=4, margin_end=4)
        for s in STATUTS:
            b = Gtk.Button(label=P.status_label(s), css_classes=["flat"])
            b.connect("clicked", lambda _b, st=s: self._on_status_selection(st))
            box.append(b)
        self.sel_status_pop.set_child(box)

    def _on_status_selection(self, status):
        self.sel_status_pop.popdown()
        label = P.status_label(status)
        n = P.set_status_many(self.con, self._sel_gids(), status)
        self.toasts.add_toast(Adw.Toast.new(tr("{n} game(s) set to “{status}”", n=n, status=label) if n
                                            else tr("Already set to this status")))
        self.select_btn.set_active(False)   # action faite : la sélection se termine
        self._build_lib()                   # les sous-titres portent le nouveau statut

    def _on_hide_selection(self, *_):
        """Masquer / démasquer la sélection d'un geste — la pilule devient
        « Démasquer » quand toute la sélection est déjà masquée (libellé tenu par
        `_update_sel_bar`). Masquer retire les tuiles de la grille : les masqués ne
        reviennent que si le réglage « afficher les masqués » est ouvert."""
        sel = self.lib_flow.get_selected_children()
        masquage = not all(c._game["hidden"] for c in sel)
        n = P.set_hidden_many(self.con, [c._game["id"] for c in sel], 1 if masquage else 0)
        if n:
            msg = tr("{n} game(s) hidden", n=n) if masquage else tr("{n} game(s) unhidden", n=n)
        else:
            msg = tr("Already hidden") if masquage else tr("Already unhidden")
        self.toasts.add_toast(Adw.Toast.new(msg))
        self.select_btn.set_active(False)   # action faite : la sélection se termine
        self._build_lib()
        self._refresh_collections()         # les comptes de la barre latérale suivent

    def _prompt_collection(self, title, initial, on_ok):
        win = Adw.Dialog(content_width=380, title=title)
        group = Adw.PreferencesGroup(margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        entry = Adw.EntryRow(title=tr("Name"), text=initial or "")
        group.add(entry)
        err = Gtk.Label(css_classes=["error"], wrap=True, visible=False, margin_start=12, margin_end=12)
        save = Gtk.Button(label=tr("Save"), halign=Gtk.Align.CENTER,
                          css_classes=["suggested-action", "pill"], margin_bottom=18)

        def do(*_):
            try:
                on_ok(entry.get_text())
            except ValueError as e:
                err.set_text(str(e))
                err.set_visible(True)
                return
            win.close()
        save.connect("clicked", do)
        entry.connect("entry-activated", do)

        def on_key(ctrl, keyval, keycode, state):
            if keyval == Gdk.KEY_Escape:
                win.close()
                return True
            return False
        key = Gtk.EventControllerKey()
        key.connect("key-pressed", on_key)
        win.add_controller(key)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for w in (group, err, save):
            box.append(w)
        view = Adw.ToolbarView(content=box)
        view.add_top_bar(Adw.HeaderBar())
        win.set_child(view)
        win.present(self)

    def _on_new_collection(self, *_):
        self._prompt_collection(tr("New collection"), "", lambda n: (P.add_collection(self.con, n),
                                                                     self._refresh_collections()))

    def _on_rename_collection(self, c):
        self._prompt_collection(tr("Rename collection"), c["name"],
                                lambda n: (P.rename_collection(self.con, c["id"], n), self._refresh_collections()))

    def _on_delete_collection(self, c):
        dlg = Adw.AlertDialog(heading=tr("Delete “{name}”?", name=c["name"]),
                              body=tr("The collection is removed, but its games are kept."))
        dlg.add_response("cancel", tr("Cancel"))
        dlg.add_response("delete", tr("Delete"))
        dlg.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.set_default_response("cancel")
        dlg.set_close_response("cancel")

        def on_resp(_d, resp):
            if resp == "delete":
                P.delete_collection(self.con, c["id"])
                self.col_selected.discard(c["id"])
                self._refresh_collections()
        dlg.connect("response", on_resp)
        dlg.present(self)

    def _on_open_launcher(self, source):
        """Le play des collections automatiques : ouvrir le client de la plateforme
        (Steam, Lutris…), lui-même et pas un jeu. Rien à dire quand ça réussit —
        la fenêtre du client EST le retour. Toast seulement si le spawn a cassé."""
        if not P.open_launcher(source):
            self.toasts.add_toast(Adw.Toast.new(tr("Couldn't open the launcher")))

    def _on_show_hidden(self, chk):
        self.show_hidden = chk.get_active()
        P.set_setting(self.con, "show_hidden", "1" if self.show_hidden else "0")
        self._build_lib()
        self._refresh_covers_summary()  # le compte des couvertures suit les jeux affichés

    def _on_show_not_installed(self, chk):
        act = chk.get_active()
        self.show_not_installed = act
        P.set_setting(self.con, "show_not_installed", "1" if act else "0")
        # L'œil de la barre est SEUL pilote : la case a quitté les Réglages pour
        # ne plus y être dupliquée — plus rien à tenir d'accord.
        self._build_lib()
        self._reset_pick()  # filtre du tirage : la proposition en cours est caduque
        self._refresh_covers_summary()  # le compte des couvertures suit les jeux affichés
        if self.show_not_installed:  # couvertures récupérées à la demande
            self._fetch_covers_bg(include_not_installed=True)

    def _on_group_games(self, chk):
        """« Regrouper les jeux » : la grille passe à trois paliers — installés,
        puis non installés, puis masqués — ou reste mélangée selon le tri choisi.
        Le contenu ne change pas : seul l'ordre bouge, d'où un simple recalage."""
        self.lib_group = chk.get_active()
        P.set_setting(self.con, "lib_group", "1" if self.lib_group else "0")
        self.lib_flow.invalidate_sort()

    def _lib_subtitle(self, gid, status):
        inst, hid = self.con.execute("SELECT installed, hidden FROM games WHERE id=?", (gid,)).fetchone()
        pt = P.playtime_min(self.con, gid)   # provider si > 0, sinon temps des sessions
        extra = [P.fmt_minutes(pt)] if pt else []
        return " · ".join([P.status_label(status)] + extra + ([] if inst else [tr("not installed")])
                          + ([tr("hidden")] if hid else []))

    # ---------- page « Bibliothèque » ----------
    def _upd(self, gid, col, val):
        self.con.execute(f"UPDATE games SET {col}=? WHERE id=?", (val, gid))
        self.con.commit()

    def _on_rename(self, gid, row, on_title):
        new = row.get_text().strip()
        if new:
            self.con.execute("UPDATE games SET name=?, name_locked=1 WHERE id=?", (new, gid))
            on_title(new)
            msg = tr("Name saved")
        else:
            self.con.execute("UPDATE games SET name_locked=0 WHERE id=?", (gid,))
            msg = tr("Original name restored on next import")
        self.con.commit()
        self.toasts.add_toast(Adw.Toast.new(msg))

    def _launch_text(self, g, use_override=True):
        try:
            return P.launch_text(g, use_override)
        except Exception:
            return ""

    def _on_exe(self, gid, row):
        g = self.con.execute("SELECT * FROM games WHERE id=?", (gid,)).fetchone()
        text, default = row.get_text().strip(), self._launch_text(g, use_override=False)
        if not text and g["ext_id"].startswith("custom:"):
            return self.toasts.add_toast(Adw.Toast.new(tr("A manually added game needs a command")))
        if not text or text == default:
            P.set_launch_override(self.con, gid, "")
            row.set_text(default)
            msg = tr("Default launch restored")
        else:
            try:
                missing = P.check_command(text)
            except ValueError:
                return self.toasts.add_toast(Adw.Toast.new(tr("Invalid command (unclosed quotes?)")))
            P.set_launch_override(self.con, gid, text)
            msg = tr("Launch command saved") + (
                " · " + tr("warning, not found: {cmd}", cmd=missing) if missing else "")
        self.toasts.add_toast(Adw.Toast.new(GLib.markup_escape_text(msg)))

    def _pick_exe(self, row):
        dlg = Gtk.FileDialog(title=tr("Choose the executable"))

        def done(d, res):
            try:
                f = d.open_finish(res)
            except GLib.Error:  # annulé
                return
            row.set_text(P.join_command([f.get_path()]))
            row.emit("apply")
        dlg.open(self, None, done)

    def _open_add_game(self, *_):
        win = Adw.Dialog(content_width=440, title=tr("Add a game"))
        group = Adw.PreferencesGroup(margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        name = Adw.EntryRow(title=tr("Name"))
        cmd = Adw.EntryRow(title=tr("Executable or launch command"))
        browse = Gtk.Button(icon_name="document-open-symbolic", valign=Gtk.Align.CENTER,
                            css_classes=["flat"], tooltip_text=tr("Choose a file…"))
        cmd.add_suffix(browse)
        group.add(name)
        group.add(cmd)
        err = Gtk.Label(css_classes=["error"], wrap=True, visible=False, margin_start=12, margin_end=12)
        add = Gtk.Button(label=tr("Add"), halign=Gtk.Align.CENTER, sensitive=False,
                         css_classes=["suggested-action", "pill"], margin_bottom=18)

        def on_change(*_):
            add.set_sensitive(bool(name.get_text().strip() and cmd.get_text().strip()))
        name.connect("changed", on_change)
        cmd.connect("changed", on_change)

        def on_browse(*_):
            dlg = Gtk.FileDialog(title=tr("Choose the executable or AppImage"))

            def done(d, res):
                try:
                    f = d.open_finish(res)
                except GLib.Error:  # annulé
                    return
                path = f.get_path()
                cmd.set_text(P.join_command([path]))
                if not name.get_text().strip():  # nom proposé d'après le fichier
                    base = path.replace("\\", "/").rsplit("/", 1)[-1]
                    name.set_text(base[:-9] if base.lower().endswith(".appimage") else base)
            dlg.open(self, None, done)  # parent = la fenêtre : FileDialog.open() refuse un Adw.Dialog
        browse.connect("clicked", on_browse)

        def on_add(*_):
            if not add.get_sensitive():
                return
            title = name.get_text().strip()
            try:
                _, missing = P.add_custom(self.con, title, cmd.get_text())
            except ValueError as e:
                err.set_text(tr("Invalid command: {e}", e=e))
                err.set_visible(True)
                return
            win.close()
            self._build_lib()
            self._refresh_moods()
            msg = tr("“{title}” added", title=title) + (
                " · " + tr("warning, not found: {cmd}", cmd=missing) if missing else "")
            self.toasts.add_toast(Adw.Toast.new(GLib.markup_escape_text(msg)))
        add.connect("clicked", on_add)
        cmd.connect("entry-activated", on_add)

        def on_key(ctrl, keyval, keycode, state):
            if keyval == Gdk.KEY_Escape:
                win.close()
                return True
            return False
        key = Gtk.EventControllerKey()
        key.connect("key-pressed", on_key)
        win.add_controller(key)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for w in (group, err, add):
            box.append(w)
        view = Adw.ToolbarView(content=box)
        view.add_top_bar(Adw.HeaderBar())
        win.set_child(view)
        win.present(self)

    def _on_game_closed(self, *_):
        self._build_lib()  # reflète les modifications faites dans la fenêtre de détail
        self._refresh_moods()
        return False

    def _open_game(self, gid):
        g = self.con.execute("SELECT * FROM games WHERE id=?", (gid,)).fetchone()
        if not g:
            return
        win = Adw.Dialog(content_width=440, content_height=700, title=g["name"])

        cover, pic = self._make_cover(dict(g), 200, 300, big=True)
        cover.set_halign(Gtk.Align.CENTER)
        tex = self._cover_texture(g)
        if tex:
            pic.set_paintable(tex)
            pic.set_visible(True)
        title = Gtk.Label(label=g["name"], wrap=True, justify=Gtk.Justification.CENTER, css_classes=["title-1"])
        subtitle = Gtk.Label(label=self._lib_subtitle(gid, g["status"]), wrap=True, css_classes=["dim-label"])
        # « Installer » remplace le « Lancer » inactif, mais SEULEMENT là où la source
        # sait installer : ailleurs, un bouton muet ne servirait à rien. C'est
        # `install_command` qui tranche, pour Steam, Lutris et Flatpak.
        installer = not g["installed"] and P.install_command(g) is not None
        play = Gtk.Button(label=tr("Install") if installer else tr("Play"), halign=Gtk.Align.CENTER,
                          sensitive=installer or bool(g["installed"]),
                          css_classes=["suggested-action", "pill"])

        def on_primary(_b):
            (self._install if installer else self._quick_launch)(gid)
            win.close()
        play.connect("clicked", on_primary)

        # Rangée d'actions : petites icônes, pas de texte. Chaque bouton ne
        # s'affiche QUE si sa commande est avérée — un lien mort ne servirait à
        # rien : le magasin exige une fiche magasin, les trois pages exigent
        # leur module activé, et la page Succès exige EN PLUS des succès pour
        # CE jeu (sinon elle s'ouvrirait sur rien).
        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4, halign=Gtk.Align.CENTER)

        def icon(icon_name, tooltip, cb, destructive=False):
            b = Gtk.Button(icon_name=icon_name, tooltip_text=tooltip, valign=Gtk.Align.CENTER,
                           css_classes=["flat"] + (["destructive-action"] if destructive else []))
            b.connect("clicked", cb)
            actions.append(b)

        if P.store_page(g):
            icon("insert-link-symbolic", tr("View in store"), lambda *_: self._open_store(g))
        if P.module_enabled(self.con, "retro"):
            icon("document-open-recent-symbolic", tr("Retrospective"), lambda *_: self._goto_retro_game(g, win))
        if P.module_enabled(self.con, "notes"):
            icon("document-edit-symbolic", tr("Notes"), lambda *_: self._goto_note_game(gid, win))
        if P.module_enabled(self.con, "achievements") and P.has_achievements(self.con, g):
            icon("starred-symbolic", tr("Achievements"), lambda *_: self._goto_ach_game(gid, win))
        # Un seul bouton destructeur à l'emplacement, jamais les deux. La source
        # qui sait RETIRER LES FICHIERS l'emporte : « Désinstaller », c'est
        # `uninstall_command` qui tranche, pour Steam, Flatpak et Lutris. Partout
        # ailleurs — jeu déjà retiré du disque, mais aussi ajouté à la main (custom)
        # ou installé sans désinstallation possible (RetroArch, itch) — la même
        # poubelle porte « Supprimer », qui ne retire que la FICHE.
        if g["installed"] and P.uninstall_command(g) is not None:
            icon("user-trash-symbolic", tr("Uninstall"), lambda *_: self._on_uninstall(gid, win), destructive=True)
        else:
            icon("user-trash-symbolic", tr("Delete"), lambda *_: self._on_delete_game(gid, win), destructive=True)

        st = Adw.ComboRow(title=tr("Status"), model=Gtk.StringList.new([P.status_label(s) for s in STATUTS]),
                          selected=STATUTS.index(g["status"]) if g["status"] in STATUTS else 1)

        def on_status(r, _p):
            s = STATUTS[r.get_selected()]
            self._upd(gid, "status", s)
            subtitle.set_text(self._lib_subtitle(gid, s))
        st.connect("notify::selected", on_status)

        tags = Adw.EntryRow(title=tr("Tags (e.g. calm,short)"), text=g["tags"])
        tags.connect("changed", lambda r: self._upd(gid, "tags", r.get_text()))

        mn = Adw.SpinRow.new_with_range(5, 480, 5)
        mn.set_title(tr("Session length (min)"))
        mn.set_value(g["session_length"])
        mn.connect("notify::value", lambda r, _p: self._upd(gid, "session_length", int(r.get_value())))

        sg = Adw.EntryRow(title=tr("SteamGridDB ID (cover)"), text=str(g["sgdb_id"] or ""), show_apply_button=True)
        sg.connect("apply", lambda r: self._on_sgdb_id(gid, r))

        cover_row = self._cover_row(g, pic)  # couverture choisie à la main, prioritaire sur SteamGridDB

        hid = Adw.SwitchRow(title=tr("Hidden"), active=bool(g["hidden"]))

        def on_hide(r, _p):
            self._upd(gid, "hidden", int(r.get_active()))
            subtitle.set_text(self._lib_subtitle(gid, STATUTS[st.get_selected()]))
        hid.connect("notify::active", on_hide)

        def on_title(text):
            title.set_text(text)
            win.set_title(text)
        nm = Adw.EntryRow(title=tr("Name"), text=g["name"], show_apply_button=True)
        nm.connect("apply", lambda r: self._on_rename(gid, r, on_title))

        ex = Adw.EntryRow(title=tr("Executable or launch command"), text=self._launch_text(g), show_apply_button=True)
        ex.connect("apply", lambda r: self._on_exe(gid, r))
        browse = Gtk.Button(icon_name="document-open-symbolic", valign=Gtk.Align.CENTER,
                            css_classes=["flat"], tooltip_text=tr("Choose a file…"))
        browse.connect("clicked", lambda _b: self._pick_exe(ex))
        ex.add_suffix(browse)

        # Les lignes de modification ne vivent PLUS dans la fiche : elles
        # composent le dialogue « Édition », rouvrable autant de fois qu'on veut
        # (le groupe est déplacé tel quel dans le dialogue du moment).
        rows = [nm, ex, st, tags, mn, sg, cover_row, hid]
        if g["store_tags"]:
            rows.append(Adw.ActionRow(title=tr("Store tags"),
                                      subtitle=GLib.markup_escape_text(", ".join(g["store_tags"].split(",")))))
        edit_group = Adw.PreferencesGroup(margin_top=6)
        for row in rows:
            edit_group.add(row)

        def refresh_infos(*_):
            """Fermeture d'« Édition » : la fiche reflète ce qui a changé dedans."""
            row = self.con.execute("SELECT * FROM games WHERE id=?", (gid,)).fetchone()
            if not row:
                return
            title.set_text(row["name"])
            win.set_title(row["name"])
            subtitle.set_text(self._lib_subtitle(gid, row["status"]))

        def cacher_croix(w):
            """Adw.Dialog ajoute toujours sa croix « fermer » en haut à droite (aucune
            API publique pour la désactiver) : on ne garde QUE le bouton Retour."""
            c = w.get_first_child()
            while c is not None:
                suivant = c.get_next_sibling()
                if isinstance(c, Gtk.Button) and _est_croix(c):
                    c.set_visible(False)
                else:
                    cacher_croix(c)
                c = suivant

        def on_edit(_b):
            dlg = Adw.Dialog(content_width=440, title=tr("Edit"))
            body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                           margin_top=6, margin_bottom=24, margin_start=12, margin_end=12)
            if (parent := edit_group.get_parent()) is not None:
                parent.remove(edit_group)  # dialogue précédent fermé : le groupe se repositionne
            body.append(edit_group)
            view = Adw.ToolbarView(content=Adw.Clamp(maximum_size=480, child=body))
            back = Gtk.Button(icon_name="go-previous-symbolic", tooltip_text=tr("Back"),
                              css_classes=["flat"])
            back.connect("clicked", lambda _b2: dlg.close())  # retour à la fiche
            bar = Adw.HeaderBar()
            bar.pack_start(back)
            view.add_top_bar(bar)
            dlg.set_child(view)
            dlg.connect("closed", refresh_infos)
            dlg.connect("map", lambda *_: cacher_croix(dlg))  # la croix revient à chaque présentation
            dlg.present(win)
            cacher_croix(dlg)

        edit = Gtk.Button(halign=Gtk.Align.CENTER, css_classes=["pill"])
        edit_box = Gtk.Box(spacing=6)
        edit_box.append(Gtk.Image(icon_name="document-edit-symbolic"))
        edit_box.append(Gtk.Label(label=tr("Edit")))
        edit.set_child(edit_box)
        edit.connect("clicked", on_edit)

        col_group = Adw.PreferencesGroup(title=tr("Collections"), margin_top=6)
        col_rows = []

        def fill_collections():
            for w in col_rows:
                col_group.remove(w)
            col_rows.clear()
            member = P.collections_of_game(self.con, gid)
            gsrc = self.con.execute("SELECT source FROM games WHERE id=?", (gid,)).fetchone()["source"]
            for c in P.list_collections(self.con):
                if c["kind"] == "auto":  # lecture seule : simple indicateur, aucune option cliquable
                    if c["auto_source"] != gsrc:
                        continue
                    row = Adw.ActionRow(title=GLib.markup_escape_text(self._col_label(c)), subtitle=tr("Automatic"))
                    row.add_prefix(Gtk.Image(icon_name="object-select-symbolic", valign=Gtk.Align.CENTER,
                                             css_classes=["success"]))
                else:
                    row = Adw.ActionRow(title=GLib.markup_escape_text(c["name"]))
                    check = Gtk.CheckButton(active=c["id"] in member, valign=Gtk.Align.CENTER)
                    check.connect("toggled", lambda b, cid=c["id"]: (
                        P.add_game_to_collection(self.con, cid, gid) if b.get_active()
                        else P.remove_game_from_collection(self.con, cid, gid)))
                    row.add_prefix(check)
                    row.set_activatable_widget(check)
                col_group.add(row)
                col_rows.append(row)
            entry = Adw.EntryRow(title=tr("New collection…"), show_apply_button=True)

            def on_apply(r):
                t = r.get_text().strip()
                if not t:
                    return
                try:
                    cid = P.add_collection(self.con, t)
                except ValueError:
                    key = t.casefold()
                    cid = next(r["id"] for r in self.con.execute("SELECT id, name FROM collections")
                               if r["name"].strip().casefold() == key)
                P.add_game_to_collection(self.con, cid, gid)
                r.set_text("")
                fill_collections()
            entry.connect("apply", on_apply)
            col_group.add(entry)
            col_rows.append(entry)

        fill_collections()

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                      margin_top=6, margin_bottom=24, margin_start=18, margin_end=18)
        for w in (cover, title, subtitle, play, edit, actions, col_group):
            if w is not None:
                box.append(w)
        body = Gtk.ScrolledWindow(child=Adw.Clamp(maximum_size=480, child=box), vexpand=True,
                                  hscrollbar_policy=Gtk.PolicyType.NEVER)
        view = Adw.ToolbarView(content=body)
        view.add_top_bar(Adw.HeaderBar(title_widget=Gtk.Label()))
        win.set_child(view)

        def on_key(ctrl, keyval, keycode, state):
            if keyval == Gdk.KEY_Escape:
                win.close()
                return True
            return False
        key = Gtk.EventControllerKey()
        key.connect("key-pressed", on_key)
        win.add_controller(key)
        win.connect("closed", self._on_game_closed)
        win.present(self)

    # ---------- aller aux pages du jeu depuis sa fiche ----------
    def _goto_retro_game(self, g, win):
        """Rétrospective ciblée sur le jeu : c'est le champ de recherche qui porte le filtre."""
        win.close()
        self._retro_search.set_text(g["name"])
        self._on_retro_search(self._retro_search)  # on ne dépend pas du délai d'émission du SearchEntry
        self.stack.set_visible_child_name("retro")

    def _goto_note_game(self, gid, win):
        """Notes du jeu : la liste est défiltrée pour que la ligne existe, puis sélectionnée."""
        win.close()
        self.notes_search.set_text("")   # un filtre oublié ne doit pas cacher le jeu demandé
        self.notes_only.set_active(False)
        self._fill_notes_list()
        row = self._note_rows.get(gid)
        if row is not None:
            self.notes_list.select_row(row)     # → `_on_note_selected` → `_show_note`
        elif self.notes_game_id != gid:
            self._flush_note()
            self._show_note(gid)  # jeu masqué par la bibliothèque : l'éditeur s'ouvre quand même
        self.stack.set_visible_child_name("notes")

    def _goto_ach_game(self, gid, win):
        """Succès du même acquis : recherche vidée, liste remplie, ligne sélectionnée."""
        win.close()
        self.ach_game_search.set_text("")
        self._fill_ach_list()
        row = self._ach_row_by_gid.get(gid)
        if row is not None:
            self.ach_list.select_row(row)       # → `_on_ach_game_selected` → `_show_ach_game`
        else:
            self._show_ach_game(gid)
        self.stack.set_visible_child_name("achievements")

    def _maj_session_bar(self):
        """La barre « Jeu en cours » reflète la base : visible ssi une session est
        ouverte (la plus récente s'il en reste plusieurs héritées)."""
        r = self.con.execute(
            "SELECT s.id, g.name FROM sessions s JOIN games g ON g.id=s.game_id "
            "WHERE s.ended=0 ORDER BY s.id DESC LIMIT 1").fetchone()
        self.session_sid = r["id"] if r else None
        self.session_bar.set_visible(r is not None)
        if r is not None:
            self.session_lbl.set_text(tr("Playing now: {game}", game=r["name"]))

    def _on_end_session(self, _b):
        """Frontière manuelle : la session se clôt à la main, comme l'aurait fait
        la sortie du jeu si elle était observable (jeu web chez itch, par ex.)."""
        if self.session_sid is not None:
            P.close_session(self.con, self.session_sid)
        self._maj_session_bar()
        self.toasts.add_toast(Adw.Toast.new(tr("Session ended")))

    def _quick_launch(self, gid):
        g = self.con.execute("SELECT * FROM games WHERE id=?", (gid,)).fetchone()
        ok = P.launch(self.con, g, on_exit=self._on_game_exit)
        self._maj_session_bar()  # session ouverte par ce lancement, précédente closee par lui
        self.toasts.add_toast(Adw.Toast.new(GLib.markup_escape_text(
            tr("▶ Launched. Have fun!") if ok else tr("Couldn't launch: is the game still installed?"))))

    def _install(self, gid):
        """Ouvre l'installation chez le lanceur. Ludodex ne peut pas savoir si elle
        aboutit : Steam écrit l'`appmanifest` quand le téléchargement est fini, et c'est
        l'importation suivante qui bascule le jeu en installé — d'où le rappel."""
        g = self.con.execute("SELECT * FROM games WHERE id=?", (gid,)).fetchone()
        if not g:
            return
        msg = (tr("Installation opened · the library updates on the next import") if P.install(g)
               else tr("Couldn't open the installation"))
        self.toasts.add_toast(Adw.Toast.new(GLib.markup_escape_text(msg)))

    def _on_uninstall(self, gid, win):
        """Confirmation destructrice avant de retirer les fichiers.

        On ne touche à rien en base : Steam réclame sa propre validation et on ne saura
        pas si elle a été donnée — la fiche ne bascule qu'à la prochaine importation,
        quand l'`appmanifest` aura disparu."""
        g = self.con.execute("SELECT * FROM games WHERE id=?", (gid,)).fetchone()
        if not g:
            return
        dlg = Adw.AlertDialog(heading=tr("Uninstall “{name}”?", name=g["name"]),
                              body=tr("Its files leave your computer. The game stays in Ludodex until the next import."))
        dlg.add_response("cancel", tr("Cancel"))
        dlg.add_response("uninstall", tr("Uninstall"))
        dlg.set_response_appearance("uninstall", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.set_default_response("cancel")
        dlg.set_close_response("cancel")

        def on_resp(_d, resp):
            if resp != "uninstall":
                return
            msg = (tr("Uninstallation opened · the library updates on the next import") if P.uninstall(g)
                   else tr("Couldn't open the uninstallation"))
            self.toasts.add_toast(Adw.Toast.new(GLib.markup_escape_text(msg)))
            win.close()
        dlg.connect("response", on_resp)
        dlg.present(win)

    def _on_delete_game(self, gid, win):
        """Confirmation destructrice, puis la fiche et tout ce qu'elle porte disparaissent."""
        g = self.con.execute("SELECT * FROM games WHERE id=?", (gid,)).fetchone()
        if not g:
            return
        dlg = Adw.AlertDialog(heading=tr("Delete “{name}”?", name=g["name"]),
                              body=tr("Its sessions, notes and achievements go with it."))
        dlg.add_response("cancel", tr("Cancel"))
        dlg.add_response("delete", tr("Delete"))
        dlg.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.set_default_response("cancel")
        dlg.set_close_response("cancel")

        def on_resp(_d, resp):
            if resp != "delete":
                return
            P.delete_game(self.con, gid)
            win.close()  # → `_on_game_closed` rebâtit la grille et les collections
            self._refresh_covers_summary()  # le dénominateur du « X/Y » vient de bouger
        dlg.connect("response", on_resp)
        dlg.present(win)

    def _on_auto_collection(self, row, _p, source):
        P.set_auto_collection_enabled(self.con, source, row.get_active())
        self._refresh_collections()

    def _on_import_source(self, row, _p, source):
        """Une plateforme désactivée disparaît partout (rien n'est supprimé) ; réactiver la restaure."""
        P.set_import_off(self.con, source, not row.get_active())
        sw = self._auto_rows.get(source) if self._auto_rows else None
        if sw is not None:
            sw.set_visible(P.source_enabled(self.con, source))
        self._build_lib()
        self._refresh_moods()
        self._refresh_retro()
        self._refresh_collections()
        self._refresh_covers_summary()  # le compte des couvertures suit les plateformes affichées

    def _on_import(self, *_):
        moved = P.import_all(self.con)
        self._build_lib()
        self._refresh_moods()
        self._fetch_tags_bg()  # mots clés des nouveaux jeux itch.io / Steam
        self._fetch_covers_bg()
        msg = tr("Library imported")
        if moved:
            msg += " · " + tr("{n} paused", n=len(moved))
        self.toasts.add_toast(Adw.Toast.new(msg))
