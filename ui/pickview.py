"""pickview.py — page « Choisir » (tirage du jeu)."""
import random, threading, time

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, GObject, Gtk, Pango

import picker as P
from i18n import available, get_lang, tr
from .theme import DUREES


class PickViewMixin:

    # ---------- page « Choisir » ----------
    def _build_pick(self):
        # --- écran 1 : filtres ---
        filters = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18,
                          margin_top=18, margin_bottom=24, margin_start=18, margin_end=18)

        group = Adw.PreferencesGroup()
        self.time_row = Adw.ComboRow(title=tr("Time available"),
                                 model=Gtk.StringList.new([tr("Any"), "15 min", "30 min", "1 h", "2 h"]))
        self.mood_row = Adw.ExpanderRow(title=tr("Tags"), subtitle=tr("Any"))
        self.mood_flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=False,
                                     min_children_per_line=2, max_children_per_line=5,
                                     row_spacing=6, column_spacing=6,
                                     margin_top=8, margin_bottom=8, margin_start=12, margin_end=12)
        # défilement interne borné : sinon, liste de tags dépliée, la zone dédiée au jeu est écrasée
        mood_scroll = Gtk.ScrolledWindow(propagate_natural_height=True, max_content_height=400,
                                         hscrollbar_policy=Gtk.PolicyType.NEVER)
        mood_scroll.set_child(self.mood_flow)
        self.mood_row.add_row(Gtk.ListBoxRow(child=mood_scroll, activatable=False, selectable=False))
        group.add(self.time_row)
        group.add(self.mood_row)
        self.mood_mode_row = Adw.ComboRow(title=tr("Mood match"),
                                          model=Gtk.StringList.new([tr("Any (OR)"), tr("All (AND)")]))
        self.mood_mode_row.set_selected(0 if self.mood_mode == "any" else 1)
        self.mood_mode_row.connect("notify::selected", self._on_mood_mode_changed)
        group.add(self.mood_mode_row)
        self.pick_col_selected = set()  # collections du tirage (vide = toutes)
        self.collections_row = Adw.ActionRow(title=tr("Collections"), activatable=False)
        self.collections_btn = Gtk.MenuButton(valign=Gtk.Align.CENTER)
        self.collections_pop = Gtk.Popover()
        self.collections_btn.set_popover(self.collections_pop)
        self.collections_row.add_suffix(self.collections_btn)
        self.collections_row.set_activatable_widget(self.collections_btn)
        group.add(self.collections_row)
        filters.append(group)

        self.btn_go = Gtk.Button(label=tr("Pick for me"), halign=Gtk.Align.CENTER)
        self.btn_go.add_css_class("suggested-action")
        self.btn_go.add_css_class("pill")
        self.btn_go.connect("clicked", self._on_choose)
        filters.append(self.btn_go)
        filters_scroll = Gtk.ScrolledWindow(child=filters, hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)

        # --- écran 2 : le jeu proposé ---
        proposal = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                           margin_top=6, margin_bottom=24, margin_start=18, margin_end=18)
        self.back_btn = Gtk.Button(icon_name="go-previous-symbolic", halign=Gtk.Align.START,
                                   css_classes=["flat"], tooltip_text=tr("Back to filters"))
        self.back_btn.connect("clicked", lambda *_: self._show_filters())
        proposal.append(self.back_btn)

        # la couverture est affichee dans la status page elle-meme (set_paintable)
        self.status_page = Adw.StatusPage(icon_name="input-gaming-symbolic", title=tr("What to play?"),
                                          description=tr("Press the button and the app decides."),
                                          height_request=220, vexpand=True)  # hauteur mini : sinon ecrasee quand les tags sont deplies
        proposal.append(self.status_page)

        self.btn_yes = Gtk.Button(label=tr("Play"))
        self.btn_yes.add_css_class("suggested-action")
        self.btn_yes.add_css_class("pill")
        self.btn_yes.connect("clicked", self._accept)
        self.btn_no = Gtk.Button(label=tr("Another"))
        self.btn_no.add_css_class("pill")
        self.btn_no.connect("clicked", self._on_other)
        self.choice_box = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER)
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
        self._show_filters()  # on démarre sur l'écran des filtres
        return self.pick_stack

    def _show_filters(self):
        self.pick_stack.set_visible_child_name("filters")

    def _reset_pick(self):
        """Changer un filtre du tirage annule la proposition en cours."""
        self.pool, self.current = [], None
        self.pool_inst, self.pool_owned, self.pick_stage = [], [], None
        self.status_page.set_icon_name("input-gaming-symbolic")
        self.status_page.set_paintable(None)
        img = self._cover_image()
        if img:
            img.set_pixel_size(-1)
        self.status_page.set_title(tr("What to play?"))
        self.status_page.set_description(tr("Press the button and the app decides."))
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

    def _after_tag_change(self):
        for o, b in self._tag_buttons.items():  # états exclusifs : on recolore tout
            self._render_tag(b, o)
        self._update_mood_subtitle()
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

    def _update_mood_subtitle(self):
        inc = sorted(self.mood_selected, key=str.lower)
        exc = sorted(self.exclude_selected, key=str.lower)
        if inc or exc:  # résumé : « + » inclus / « − » exclus
            txt = "  ".join(["+ " + t for t in inc] + ["− " + t for t in exc])
        else:
            txt = tr("Left-click: include · Right-click: exclude")
        self.mood_row.set_subtitle(GLib.markup_escape_text(txt))
        self.mood_mode_row.set_sensitive(bool(inc))  # le mode OU/ET n'a de sens qu'avec un tag inclus

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
        if self.current:
            row = self.con.execute("SELECT * FROM games WHERE id=?", (self.current["id"],)).fetchone()
            if row:
                self._set_cover(row)

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
        self.pool_inst = self._eligible_pool(False)
        # les jeux non installés ne sont proposés que si l'option du Choix les prend en compte
        self.pool_owned = ([p for p in self._eligible_pool(True) if not p[0]["installed"]]
                           if self.pick_owned else [])
        if not self.pool_inst and not self.pool_owned:
            self.toasts.add_toast(Adw.Toast.new(tr("Nothing eligible")))
            return
        self.pick_stage = "installed" if self.pool_inst else "owned"
        self.pool = list(self.pool_inst) if self.pick_stage == "installed" else list(self.pool_owned)
        self.current = None
        self._propose()

    def _propose(self):
        if not self.pool:  # reservoir epuise : installes passes -> possedes, sinon on recycle
            if self.pick_stage == "installed" and self.pool_owned:
                self.pick_stage = "owned"
            self.pool = list(self.pool_owned) if self.pick_stage == "owned" else list(self.pool_inst)
            if self.current and len(self.pool) > 1:  # eviter la repetition immediate
                self.pool = [p for p in self.pool if p[0]["id"] != self.current["id"]]
        if not self.pool:
            self.toasts.add_toast(Adw.Toast.new(tr("Nothing eligible")))
            return
        g = random.choices([p[0] for p in self.pool], [p[1] for p in self.pool])[0]
        self.current = g
        self._set_cover(g)
        self.status_page.set_title(g["name"])
        desc = P.status_label(g["status"])
        if not g["installed"]:
            desc += " · " + tr("not installed")
        if g["playtime_min"]:
            desc += " · " + tr("{t} played", t=P.fmt_minutes(g["playtime_min"]))
        if g["store_tags"]:
            desc += "\n" + " · ".join(g["store_tags"].split(",")[:4])
        self.status_page.set_description(GLib.markup_escape_text(desc))
        self.btn_yes.set_label(tr("Play") if g["installed"] else tr("Store page"))
        self.pick_stack.set_visible_child_name("proposal")  # on bascule sur la fiche du jeu

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
        self.pool = [p for p in self.pool if p[0]["id"] != self.current["id"]]
        self._propose()

    def _launch(self, g):
        ok = P.launch(self.con, g)
        self._set_cover(g)
        self.status_page.set_title(g["name"])
        self.status_page.set_description(
            tr("▶ Launched. Have fun!") if ok else tr("Couldn't launch: is the game still installed?"))

    def _set_cover(self, g):
        """Affiche la couverture (réduite à 240 px de haut) ; sans couverture lisible, on garde l'icône."""
        texture, err = None, ""
        f = P.cover_file(g)
        if f:
            try:
                texture = Gdk.Texture.new_for_pixbuf(GdkPixbuf.Pixbuf.new_from_file_at_scale(str(f), -1, 240, True))
            except Exception as e:  # secours : chargeur de GTK lui-même (taille d'origine)
                err = f"pixbuf : {getattr(e, 'message', e)}"
                try:
                    texture = Gdk.Texture.new_from_filename(str(f))
                except Exception as e2:
                    err += f" | texture : {getattr(e2, 'message', e2)}"
        name = g["name"] if g else None
        self.status_page.set_paintable(texture)          # la couverture fait partie de la status page
        self.status_page.set_icon_name(None if texture is not None else "input-gaming-symbolic")
        img = self._cover_image()                        # agrandit la couverture (sinon taille d'icone ~128px)
        if img is not None:
            img.set_pixel_size(240 if texture is not None else -1)

    def _cover_image(self):
        """Le Gtk.Image interne de la status page qui affiche l'icone/couverture."""
        def walk(w):
            if isinstance(w, Gtk.Image) and "icon" in w.get_css_classes():
                return w
            c = w.get_first_child()
            while c:
                r = walk(c)
                if r is not None:
                    return r
                c = c.get_next_sibling()
            return None
        return walk(self.status_page)

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
