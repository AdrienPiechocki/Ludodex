"""notes.py — page « Notes » : une note Markdown par jeu, avec aperçu en direct.

Comme dans Obsidian, la syntaxe reste cachée : un marqueur (`#`, `**`, `>`…) ne
réapparaît que le moment où le curseur est posé exactement dessus, le reste de
la ligne restant rendu. Le buffer contient toujours le Markdown d'origine :
l'aperçu n'est qu'une pose de balises `GtkTextTag`, recalculée à chaque frappe
et chaque déplacement du curseur. Un sommaire (colonne dépliable à droite de
l'éditeur, indentation par niveau) permet de naviguer dans la note.
"""
import math
import re

import cairo
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango

import picker as P
from i18n import tr
from .markdown import (HEADING_SCALE, anchor_matches, heading_level, heading_text,
                       is_fence, line_elements, link_at, markers_revealed, task_at)

NOTE_SAVE_DELAY = 600  # ms d'inactivité avant d'enregistrer une note

# Auto-complétion : ouvrant -> fermant ; un fermant tapé alors qu'il est déjà
# présent est simplement sauté.
_PAIRS = {"(": ")", "[": "]", "{": "}", '"': '"'}
_OPEN_KEYS = {Gdk.KEY_parenleft: "(", Gdk.KEY_bracketleft: "[",
              Gdk.KEY_braceleft: "{", Gdk.KEY_quotedbl: '"'}
_CLOSE_KEYS = {Gdk.KEY_parenright: ")", Gdk.KEY_bracketright: "]",
               Gdk.KEY_braceright: "}", Gdk.KEY_quotedbl: '"'}
_LIST_RE = re.compile(r"^([ \t]*)([-*+]|\d+[.)])([ \t]+)(\[[ xX]\][ \t]+)?")
_QUOTE_RE = re.compile(r"^([ \t]*>[ \t]?)")

# Couleurs des cases dessinées (indépendantes du thème, qui n'a pas toujours de
# vraies icônes de case : sur Breeze, `checkbox-symbolic` n'est qu'un crochet).
_CHECK_ACCENT = (0.208, 0.518, 0.894)   # #3584e4
_CHECK_BORDER = (0.604, 0.596, 0.588)   # #9a9996
_CHECK_STROKE = 0.11                    # épaisseur du trait : max(1.3, side * 0.11)
_CHECK_GAP = 2.0                        # marge visuelle voulue entre le trait et le texte


class NotesMixin:

    # ---------- page « Notes » : liste des jeux + éditeur ----------
    def _build_notes_page(self):
        self.notes_game_id = None
        self._note_loading = False   # on charge une note : le « changed » ne doit pas enregistrer
        self._note_dirty = False
        self._note_timeout = None
        self._note_rows = {}
        self._md_applying = False    # pose des balises en cours : on ignore les signaux
        self._md_idle = None
        self._note_tasks = []        # cases à cocher : (début du tiret, fin du `]`, coché)

        # --- volet gauche : recherche, filtre et liste des jeux ---
        self.notes_search = Gtk.SearchEntry()
        if hasattr(self.notes_search, "set_placeholder_text"):  # GTK >= 4.10
            self.notes_search.set_placeholder_text(tr("Search games…"))
        self.notes_search.connect("search-changed", self._on_notes_filter)
        self.notes_only = Gtk.ToggleButton(icon_name="document-edit-symbolic",
                                           active=self._has_notes(),   # ON s'il y a des notes, sinon OFF
                                           tooltip_text=tr("Only games with a note"), valign=Gtk.Align.CENTER)
        self.notes_only.connect("toggled", self._on_notes_filter)
        head = Gtk.Box(spacing=6, margin_top=8, margin_bottom=6, margin_start=12, margin_end=6)
        head.append(Gtk.Label(label=tr("Notes"), xalign=0, hexpand=True, css_classes=["heading"]))
        head.append(self.notes_only)

        self.notes_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE,
                                      css_classes=["navigation-sidebar"])
        self.notes_list.connect("row-selected", self._on_note_selected)
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        left.append(head)
        left.append(self.notes_search)
        left.append(Gtk.ScrolledWindow(child=self.notes_list, vexpand=True,
                                       hscrollbar_policy=Gtk.PolicyType.NEVER))

        # --- volet droit : éditeur (aperçu en direct du Markdown) ---
        self.note_title = Gtk.Label(xalign=0, wrap=True, hexpand=True, css_classes=["title-2"])
        self.note_help_btn = Gtk.Button(icon_name="help-about-symbolic", valign=Gtk.Align.CENTER,
                                        css_classes=["flat"], tooltip_text=tr("Markdown help"))
        self.note_help_btn.connect("clicked", self._on_note_help)
        title_row = Gtk.Box(spacing=6)
        title_row.append(self.note_title)
        # Le bouton d'aide n'est ajouté qu'APRÈS le bouton du sommaire (plus bas) :
        # ajouté ici ET là, il serait parenté deux fois (gtk_box_append: critical).
        self.note_subtitle = Gtk.Label(xalign=0, css_classes=["dim-label"])
        # Sommaire : colonne dépliable à DROITE de l'éditeur — une ligne par
        # titre, indentée par niveau ; le clic y amène le curseur.
        self._toc_open = True                     # la colonne s'ouvre sur les notes titrées
        self._toc_key = []
        self.toc_toggle = Gtk.ToggleButton(icon_name="view-list-symbolic", valign=Gtk.Align.CENTER,
                                           css_classes=["flat"], sensitive=False,  # pas de titre : inactif, jamais muet
                                           active=True,  # la colonne est ouverte par défaut
                                           tooltip_text=tr("No heading in this note"))
        self.toc_toggle.connect("toggled", self._on_toc_toggled)
        title_row.append(self.toc_toggle)
        title_row.append(self.note_help_btn)
        self.toc_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE,
                                    css_classes=["navigation-sidebar"])
        self.toc_list.connect("row-selected", self._on_toc_row)
        self.toc_panel = Gtk.ScrolledWindow(child=self.toc_list, vexpand=True, width_request=200,
                                            hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.toc_panel.set_visible(False)
        self.note_view = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, vexpand=True,
                                      top_margin=8, bottom_margin=8, left_margin=10, right_margin=10)
        self.note_buffer = self.note_view.get_buffer()
        self.note_buffer.connect("changed", self._on_note_changed)
        self.note_buffer.connect("notify::cursor-position", self._on_cursor_moved)
        keyctl = Gtk.EventControllerKey()
        keyctl.connect("key-pressed", self._on_note_key)
        self.note_view.add_controller(keyctl)
        click = Gtk.GestureClick(button=1)                 # cases à cocher et liens cliquables
        click.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        click.connect("pressed", self._on_note_click)
        self.note_view.add_controller(click)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_note_motion)
        self.note_view.add_controller(motion)
        self._create_md_tags()
        # Les cases à cocher réelles sont dessinées par-dessus le texte, à la place
        # des marqueurs `[ ]`/`[x]` rendus transparents (qui gardent leur largeur).
        self.note_check_area = Gtk.DrawingArea(hexpand=True, vexpand=True)
        self.note_check_area.set_can_target(False)          # les clics vont à la vue texte
        self.note_check_area.set_draw_func(self._draw_note_checks)
        # L'éditeur est ScrolledWindow -> TextView DIRECT : c'est la condition du
        # défilement programmé (`scroll_to_iter` des titres du sommaire et des
        # ancres de liens). Un Overlay entre les deux (zones de cases à cocher)
        # en ferait un enfant de viewport : il perdrait les ajustements et
        # scroll_to_iter ciblerait des valeurs que personne n'écoute — mort
        # silencieux, constaté en réel. Les cases sont donc superposées au
        # SCROLLEDWINDOW (zone fixe), dont l'origine coïncide avec celle du
        # TextView : `buffer_to_window_coords` rend des coordonnées fenêtre déjà
        # sans défilement, la case suit donc le texte SANS compensation d'ajustement.
        editor_scroll = Gtk.ScrolledWindow(child=self.note_view, vexpand=True,
                                           hscrollbar_policy=Gtk.PolicyType.NEVER, css_classes=["card"])
        self._editor_adj = editor_scroll.get_vadjustment()
        for adj in (self._editor_adj, editor_scroll.get_hadjustment()):
            adj.connect("value-changed", lambda *_: self.note_check_area.queue_draw())
        self.note_overlay = Gtk.Overlay()
        self.note_overlay.set_child(editor_scroll)
        self.note_overlay.add_overlay(self.note_check_area)
        editor = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                         margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        for w in (title_row, self.note_subtitle, self.note_overlay):
            editor.append(w)
        notes_paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        notes_paned.set_start_child(editor)
        notes_paned.set_end_child(self.toc_panel)
        notes_paned.set_resize_start_child(True)
        notes_paned.set_shrink_start_child(True)
        notes_paned.set_resize_end_child(False)      # la colonne garde sa largeur
        notes_paned.set_shrink_end_child(False)

        self.notes_empty = Adw.StatusPage(icon_name="document-edit-symbolic", vexpand=True,
                                          title=tr("Your notes"),
                                          description=tr("Pick a game on the left to write a note."))
        self.notes_right = Gtk.Stack()
        self.notes_right.add_named(self.notes_empty, "empty")
        self.notes_right.add_named(notes_paned, "editor")
        self.notes_right.set_visible_child_name("empty")

        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.set_start_child(left)
        paned.set_end_child(self.notes_right)
        paned.set_resize_start_child(False)
        paned.set_shrink_start_child(False)
        paned.set_position(260)

        # Taille du texte de l'éditeur (points) : valeur mémorisée, sinon celle du thème.
        self.notes_font_size = self._note_base_size()
        stored = P.get_setting(self.con, "notes_font_size")
        if stored and stored.strip().lstrip("-").isdigit():
            self.notes_font_size = max(8, min(40, int(stored)))
        self._apply_note_font(self.notes_font_size)

        self._fill_notes_list()
        return paned

    # ---------- aperçu en direct ----------
    def _note_bg_color(self):
        """Couleur de fond réelle de l'éditeur, pour peindre les marqueurs dedans.

        GTK4 ne retient que le RGB de `foreground_rgba` : avec `Gdk.RGBA(0,0,0,0)`
        les crochets `[ ]` n'étaient pas masqués mais repeints NOIRS ET OPEQUES
        et restaient visibles à travers la case creuse. Peindre le marqueur dans
        la couleur du fond le fait disparaître tout en lui gardant sa place — ce
        qu'`invisible=True` ne fait pas : il supprime la largeur et le texte
        suivant remonte, ce qui décalerait la case sur la tâche."""
        sc = self.note_view.get_style_context()
        for name in ("theme_base_color", "theme_bg_color"):
            try:
                found, col = sc.lookup_color(name)
            except Exception:
                found, col = False, None
            if found:
                return col
        return Gdk.RGBA(1, 1, 1, 1)

    def _create_md_tags(self):
        """Une balise par style ; « md-hidden » masque les marqueurs de syntaxe."""
        buf = self.note_buffer
        names = ["md-hidden", "md-bold", "md-italic", "md-bi", "md-strike", "md-code",
                 "md-quote", "md-link", "md-list", "md-checked", "md-task", "md-tasklead"]
        buf.create_tag("md-hidden", invisible=True)
        bg = self._note_bg_color()
        buf.create_tag("md-task", foreground_rgba=bg)                   # peint dans le fond : garde la largeur
        buf.create_tag("md-tasklead", foreground_rgba=bg, scale=0.01)   # tiret réduit à zéro
        buf.create_tag("md-bold", weight=Pango.Weight.BOLD)
        buf.create_tag("md-italic", style=Pango.Style.ITALIC)
        buf.create_tag("md-bi", weight=Pango.Weight.BOLD, style=Pango.Style.ITALIC)
        buf.create_tag("md-strike", strikethrough=True)
        buf.create_tag("md-code", family="monospace")
        buf.create_tag("md-quote", style=Pango.Style.ITALIC)
        buf.create_tag("md-link", foreground="#3584e4", underline=Pango.Underline.SINGLE)
        buf.create_tag("md-list")                              # puce/numéro dans la couleur du texte
        buf.create_tag("md-checked", strikethrough=True)      # tâche cochée : texte barré
        for lvl, scale in HEADING_SCALE.items():
            buf.create_tag(f"md-h{lvl}", scale=scale, weight=Pango.Weight.BOLD)
            names.append(f"md-h{lvl}")
        for lvl in range(1, 5):                                # retrait des listes (puce pendante)
            buf.create_tag(f"md-indent{lvl}", left_margin=10 + lvl * 18, indent=-14)
            names.append(f"md-indent{lvl}")
        self._md_tag_names = names

    def _note_base_size(self):
        """Taille de base des notes (en points) telle que résolue par le thème."""
        try:
            ctx = self.note_view.create_pango_context()
            desc = ctx.get_font_description()
            s = desc.get_size()
            if s <= 0:
                return 11
            if desc.get_size_is_absolute():          # en 1/1024 de pixel → points (dpi du contexte)
                dpi = ctx.get_resolution() or 96.0
                return max(1, round(s / Pango.SCALE * 72.0 / dpi))
            return max(1, round(s / Pango.SCALE))    # déjà en unités de point
        except Exception:
            return 11

    def _apply_note_font(self, size):
        """Fixe la taille du texte de l'éditeur (toute la note, titres compris)."""
        if getattr(self, "_note_font_css", None) is None:
            self._note_font_css = Gtk.CssProvider()
            self.note_view.get_style_context().add_provider(
                self._note_font_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self._note_font_css.load_from_string(f"textview {{ font-size: {size}pt; }}")
        self.note_check_area.queue_draw()

    def _on_cursor_moved(self, *_):
        if not self._md_applying:
            self._schedule_md()

    def _schedule_md(self):
        """Regroupe les recalculs (frappe, déplacement du curseur) en un seul passage."""
        if self._md_idle is None:
            self._md_idle = GLib.idle_add(self._run_md)

    def _run_md(self):
        self._md_idle = None
        self._apply_md()
        return False

    def _apply_md(self):
        """Rend toute la note ; seul l'élément sous le curseur reste en source brute."""
        if self._md_applying:
            return
        self._md_applying = True
        try:
            buf = self.note_buffer
            start, end = buf.get_bounds()
            for name in self._md_tag_names:
                buf.remove_tag_by_name(name, start, end)
            self._note_tasks = []
            tocs = []
            ins = buf.get_iter_at_mark(buf.get_insert())
            cursor_line = ins.get_line()
            count = buf.get_line_count()
            in_code = False
            for lineno in range(count):
                ls = buf.get_iter_at_line(lineno)[1]
                le = buf.get_iter_at_line(lineno + 1)[1] if lineno + 1 < count else buf.get_end_iter()
                raw = buf.get_text(ls, le, True)  # True : on veut le texte, marqueurs masqués compris
                raw = raw[:-1] if raw.endswith("\n") else raw
                if is_fence(raw):
                    if lineno != cursor_line:     # la ligne ``` est masquée hors édition
                        self._apply_tag(buf, ls, "md-hidden", 0, len(raw))
                    in_code = not in_code
                    continue
                cursor_col = ins.get_offset() - ls.get_offset() if lineno == cursor_line else -1
                if not in_code:
                    lvl = heading_level(raw)
                    if lvl:
                        tocs.append((lineno, lvl, heading_text(raw) or ""))
                line = line_elements(raw, in_code)
                for el in line.elements:
                    if self._hide_markers(el, lineno, cursor_line, cursor_col):
                        for a, b in el.markers:
                            self._apply_tag(buf, ls, "md-hidden", a, b)
                    if el.kind is not None and el.cend > el.cstart:
                        self._apply_tag(buf, ls, f"md-{el.kind}", el.cstart, el.cend)
                if line.indent:
                    self._apply_tag(buf, ls, f"md-indent{line.indent}", 0, le.get_offset() - ls.get_offset())
                task_open = False
                if line.task:
                    ts, te, checked = line.task
                    # La syntaxe `[ ]` brute ne réapparaît que sous le curseur
                    # exact ; partout ailleurs, la vraie case prend sa place.
                    task_open = markers_revealed([(ts, te)], cursor_col)
                    if task_open:
                        if line.marker:
                            self._apply_tag(buf, ls, "md-list", *line.marker)
                    else:
                        start = line.marker[0] if line.marker else ts
                        self._apply_tag(buf, ls, "md-tasklead", start, ts)  # tiret réduit à zéro (la case prend sa place)
                        self._apply_tag(buf, ls, "md-task", ts, te)         # `[ ]`/`[x]` : peints dans le fond (garde la largeur)
                        # tout l'emplacement (tiret -> fin de `]`) : c'est lui que la case doit occuper
                        self._note_tasks.append((ls.get_offset() + start, ls.get_offset() + te, checked))
                elif line.marker:
                    self._apply_tag(buf, ls, "md-list", *line.marker)
                if line.checked and not task_open:   # barré masqué tant que `[ ]` n'est pas sous le curseur
                    self._apply_tag(buf, ls, "md-checked", *line.checked)
            self._set_toc(tocs)
        finally:
            self._md_applying = False
            self.note_check_area.queue_draw()

    @staticmethod
    def _hide_markers(el, lineno, cursor_line, cursor_col):
        """Faut-il masquer les marqueurs de cet élément ?

        La syntaxe reste masquée tant que le curseur n'est pas posé EXACTEMENT
        sur elle — marqueur de bloc (`#`, `>`) comme mise en forme en ligne
        (`**`, `*`, `` ` ``…) : on ne voit les `#` d'un titre que le moment d'y
        écrire ou d'y revenir."""
        return not markers_revealed(el.markers, cursor_col)

    # ---------- sommaire ----------
    def _set_toc(self, entries):
        """Remplit la colonne du sommaire — et la RÉFABRIQUE dès que les titres bougent.

        `entries` : liste de (numéro de ligne, niveau, texte), dans l'ordre.
        L'indentation suit le niveau : `Titre 1` / `  Titre 2` / `    Titre 3`.

        La clé de comparaison CONTIENT le numéro de ligne : sans lui, une édition
        qui décale les titres (ligne insérée au-dessus) laissait les anciennes
        lignes en place avec leur `_lineno` périmé — le clic sur le sommaire
        amenait alors à la mauvaise ligne, souvent déjà visible : « rien ne se
        passe ». Un numéro de ligne ne bouge jamais sans que la liste des titres
        elle-même n'ait changé, donc pas de reconstruction inutile pendant la
        frappe ni d'un simple déplacement de curseur."""
        key = [(ln, lvl, txt) for ln, lvl, txt in entries]
        if key == getattr(self, "_toc_key", []):
            return
        self._toc_key = key
        while self.toc_list.get_first_child() is not None:
            self.toc_list.remove(self.toc_list.get_first_child())
        for lineno, lvl, txt in entries:
            row = Gtk.ListBoxRow()
            row.set_child(Gtk.Label(label=txt, xalign=0, ellipsize=Pango.EllipsizeMode.END,
                                    margin_start=6 + 12 * (lvl - 1), margin_top=3, margin_bottom=3,
                                    tooltip_text=tr("Jump to “{t}”", t=txt)))
            row._lineno = lineno
            self.toc_list.append(row)
        self.toc_toggle.set_sensitive(bool(entries))
        self.toc_toggle.set_tooltip_text(
            tr("Table of contents") if entries else tr("No heading in this note"))
        self._update_toc_visibility()

    def _on_toc_toggled(self, btn):
        self._toc_open = btn.get_active()
        self._update_toc_visibility()

    def _update_toc_visibility(self):
        """La colonne ne monte que s'il y a des titres ET qu'elle est dépliée."""
        self.toc_panel.set_visible(self._toc_open and bool(self._toc_key))

    def _on_toc_row(self, _listbox, row):
        """Clic sur une ligne du sommaire : curseur + défilement sur le titre."""
        if row is None:                      # sélection perdue au rebuild : rien à faire
            return
        buf = self.note_buffer
        it = buf.get_iter_at_line(min(row._lineno, buf.get_line_count() - 1))[1]
        buf.place_cursor(it)
        self.note_view.scroll_to_iter(it, 0.0, 0.0, 0.0, False)
        self.note_view.grab_focus()

    @staticmethod
    def _apply_tag(buf, line_start, name, a, b):
        if b <= a:
            return
        s = line_start.copy(); s.forward_chars(a)
        e = line_start.copy(); e.forward_chars(b)
        buf.apply_tag_by_name(name, s, e)

    def _on_note_help(self, _btn):
        """Aide : la syntaxe Markdown reconnue par l'aperçu en direct (toujours actif)."""
        dlg = Adw.Dialog(title=tr("Markdown help"), content_width=560, content_height=660)
        grid = Gtk.Grid(row_spacing=6, column_spacing=28, margin_top=4,
                        margin_bottom=28, margin_start=28, margin_end=28)
        row = [0]

        def section(title):
            grid.attach(Gtk.Label(label=tr(title), xalign=0, css_classes=["heading"],
                                  margin_top=12),
                        0, row[0], 2, 1)
            row[0] += 1

        def item(example, syntax):
            ex = Gtk.Label(xalign=0, halign=Gtk.Align.START)
            ex.set_markup(example)
            sy = Gtk.Label(xalign=0, wrap=True, hexpand=True, css_classes=["dim-label"])
            sy.set_markup("<tt>%s</tt>" % GLib.markup_escape_text(syntax))
            grid.attach(ex, 0, row[0], 1, 1)
            grid.attach(sy, 1, row[0], 1, 1)
            row[0] += 1

        section("Headings")
        item("<b>Titre de niveau 1</b>", "# Titre 1")
        item("<b>Titre de niveau 2</b>", "## Titre 2")
        item("<b>Titre de niveau 3</b>", "### Titre 3")

        section("Formatting")
        item("<b>gras</b>", "**gras**")
        item("<i>italique</i>", "*italique*")
        item("<b><i>gras italique</i></b>", "***gras italique***")
        item("<s>barré</s>", "~~barré~~")

        section("Code")
        item("<tt>en ligne</tt>", "`en ligne`")
        item("<tt>bloc de code</tt>", "```\ncode\n```")

        section("Lists")
        item("• puce", "- puce")
        item("1. numérotée", "1. numérotée")
        item("☐ à faire", "- [ ] à faire")
        item("<s>☑ fait</s>", "- [x] fait")

        section("Quote")
        item("<i>citation</i>", "> citation")

        section("Links")
        item('<span foreground="#3584e4" underline="single">texte</span>',
             "[texte](https://exemple.com)")
        item('<span foreground="#3584e4" underline="single">texte</span>',
             "[texte](#titre)")

        hint = Gtk.Label(xalign=0, wrap=True, css_classes=["dim-label"], margin_top=16)
        hint.set_text(tr("The live preview is always on: click a checkbox to toggle it, "
                         "click a link to open it."))
        hint.set_xalign(0.5)
        grid.attach(hint, 0, row[0], 2, 1)

        scroll = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroll.set_child(grid)
        view = Adw.ToolbarView(content=scroll)
        view.add_top_bar(Adw.HeaderBar())
        dlg.set_child(view)

        def on_key(_c, keyval, *_a):
            if keyval == Gdk.KEY_Escape:
                dlg.close()
                return True
            return False
        key = Gtk.EventControllerKey()
        key.connect("key-pressed", on_key)
        dlg.add_controller(key)
        dlg.present(self)

    def _draw_note_checks(self, _area, cr, _width, _height):
        """Dessine une vraie case à cocher (carré arrondi) sur chaque marqueur masqué."""
        if not self._note_tasks or self.notes_game_id is None:
            return
        view, buf = self.note_view, self.note_buffer
        rows = []
        for start, end, checked in self._note_tasks:
            r0 = view.get_iter_location(buf.get_iter_at_offset(start))
            # Haut de ligne et hauteur LUS SUR LE `]`, jamais sur le tiret : celui-ci
            # est dans `md-tasklead` (scale=0.01) où le glyphe est aplati sur la
            # ligne de base — la case retombait SOUS le texte, et sa hauteur valait 0
            # ce qui faisait tomber max(12, h-3) sur le plancher de 12 px.
            mark = view.get_iter_location(buf.get_iter_at_offset(end - 1))
            height = mark.height
            if height < 4.0:
                continue
            x, _ = view.buffer_to_window_coords(Gtk.TextWindowType.WIDGET, r0.x, r0.y)
            # `buffer_to_window_coords` rend des coords fenêtre DÉJÀ sans défilement
            # (mesuré : sa pente vaut -1 face à l'adjustment). La zone de dessin est
            # fixe mais a exactement la même origine que le TextView, donc rien à
            # retrancher : retrancher l'ajustement décalait la case vers le HAUT
            # d'autant plus qu'on défilait (décorrélation du texte).
            _, y = view.buffer_to_window_coords(Gtk.TextWindowType.WIDGET, mark.x, mark.y)
            # Limite à droite : la 1re lettre du texte de la tâche (elle seule peut
            # être heurtée ; le marqueur est peint dans le fond, la case le remplace).
            t = buf.get_iter_at_offset(end)
            while not t.ends_line() and t.get_char() in (" ", "\t"):
                t.forward_char()
            if t.ends_line():
                # Rien derrière la case : sans repère, elle prendrait toute la
                # hauteur de la ligne, et une note à cases vides seules en
                # ressortirait plus grande qu'une note à texte. On place une
                # lettre VIRTUELLE où elle serait — après la première espace,
                # que la syntaxe exige toujours après `[ ]` — : le côté devient
                # le même partout, à la lettre près.
                pos = buf.get_iter_at_offset(end + 1)
                rows.append((x, y, height, view.get_iter_location(pos).x, checked))
                continue
            rt = view.get_iter_location(t)
            x_text, _ = view.buffer_to_window_coords(Gtk.TextWindowType.WIDGET, rt.x, rt.y)
            if checked:
                # `[x]` est plus large que `[ ]` (le « x » qu'une espace) : mesurée
                # telle quelle, la limite recule de cet écart et une note à cases
                # cochées SEULES ressort en pleine hauteur — plus grande qu'une note
                # mêlée. On remet la limite là où elle serait si le marqueur était
                # `[ ]`, en comparant la largeur du milieu du marqueur à celle d'une
                # espace, toutes deux prises dans le même style.
                g = lambda o: view.get_iter_location(buf.get_iter_at_offset(o)).x
                w_mid = g(end - 1) - g(end - 2)     # milieu du marqueur (« x »)
                if buf.get_iter_at_offset(end).get_char() == " ":
                    w_esp = g(end + 1) - g(end)     # une espace de la même police
                    x_text -= (w_mid - w_esp)
            rows.append((x, y, height, x_text, checked))
        if not rows:
            return

        # UN seul côté pour toute la note : `[x]` est plus large que `[ ]`, donc,
        # né ligne par ligne, la case cochée disposerait de 4 px de plus, atteindrait
        # la pleine hauteur et ressortirait plus grande que la case vide. On retient
        # la contrainte la plus serrée de la note et on l'applique partout.
        limit = min(self._check_side(x, height, x_text)
                    for x, _y, height, x_text, _checked in rows)

        for x, y, height, x_text, checked in rows:
            side = max(12.0, min(limit, height - 3.0))
            y += (height - side) / 2.0            # centrée sur SA ligne
            self._check_path(cr, x, y, side)
            if checked:
                cr.set_source_rgb(*_CHECK_ACCENT)
                cr.fill()
                cr.set_source_rgb(1, 1, 1)
                cr.set_line_width(max(1.6, side * 0.16))
                cr.set_line_cap(cairo.LINE_CAP_ROUND)
                cr.set_line_join(cairo.LINE_JOIN_ROUND)
                cr.move_to(x + side * 0.24, y + side * 0.52)
                cr.line_to(x + side * 0.43, y + side * 0.72)
                cr.line_to(x + side * 0.78, y + side * 0.28)
                cr.stroke()
            else:
                cr.set_source_rgb(*_CHECK_BORDER)
                cr.set_line_width(max(1.3, side * 0.11))
                cr.stroke()

    @staticmethod
    def _check_side(x, height, x_text):
        """Plus grand côté admis sur une ligne : pleine hauteur, sans lécher le texte.

        `x_text` est la première lettre de la tâche — réelle, ou virtuelle (la
        position qu'elle AURAIT) sur une case vide : une seule règle partout.
        Le trait déborde d'un demi-trait sur le chemin : on décompte son épaisseur
        pour que le cadre ne vienne jamais toucher la première lettre de la tâche.
        """
        room = (x_text - x - _CHECK_GAP) / (1.0 + _CHECK_STROKE / 2.0)
        return max(12.0, min(height - 3.0, room))

    @staticmethod
    def _check_path(cr, x, y, side):
        """Chemin d'un carré à coins arrondis."""
        radius = side * 0.28
        cr.new_path()
        cr.arc(x + side - radius, y + radius, radius, -math.pi / 2, 0)
        cr.arc(x + side - radius, y + side - radius, radius, 0, math.pi / 2)
        cr.arc(x + radius, y + side - radius, radius, math.pi / 2, math.pi)
        cr.arc(x + radius, y + radius, radius, math.pi, 3 * math.pi / 2)
        cr.close_path()

    # ---------- auto-complétion Markdown ----------
    def _on_note_key(self, _ctl, keyval, _code, state):
        """Paires de délimiteurs et continuation des listes (façon Obsidian)."""
        if self.notes_game_id is None:
            return False
        if state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK | Gdk.ModifierType.SUPER_MASK):
            return False
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            return self._list_continue()
        if keyval == Gdk.KEY_BackSpace:
            return self._delete_pair()
        open_ch = _OPEN_KEYS.get(keyval)
        if open_ch is not None:                       # ( [ { " : on encadre
            if self._skip_over(_PAIRS[open_ch]):
                return True
            self._insert_pair(open_ch, _PAIRS[open_ch])
            return True
        close_ch = _CLOSE_KEYS.get(keyval)
        if close_ch is not None:                      # ) ] } : on saute le fermant déjà là
            return self._skip_over(close_ch)
        return False

    def _skip_over(self, ch):
        buf = self.note_buffer
        if buf.get_has_selection():
            return False
        it = buf.get_iter_at_mark(buf.get_insert())
        if it.is_end():
            return False
        nxt = it.copy(); nxt.forward_char()
        if buf.get_text(it, nxt, True) == ch:
            buf.place_cursor(nxt)
            return True
        return False

    def _insert_pair(self, open_ch, close_ch):
        buf = self.note_buffer
        buf.begin_user_action()
        if buf.get_has_selection():                   # on encadre la sélection
            s, e = buf.get_selection_bounds()
            text = buf.get_text(s, e, True)
            buf.delete(s, e)
            buf.insert_at_cursor(open_ch + text + close_ch)
        else:
            buf.insert_at_cursor(open_ch + close_ch)  # le curseur reste entre les deux
            it = buf.get_iter_at_mark(buf.get_insert()); it.backward_char()
            buf.place_cursor(it)
        buf.end_user_action()
        return True

    def _delete_pair(self):
        """Retour arrière entre une paire vide : on efface les deux."""
        buf = self.note_buffer
        if buf.get_has_selection():
            return False
        it = buf.get_iter_at_mark(buf.get_insert())
        if it.is_start() or it.is_end():
            return False
        prv = it.copy(); prv.backward_char()
        nxt = it.copy(); nxt.forward_char()
        if _PAIRS.get(buf.get_text(prv, it, True)) == buf.get_text(it, nxt, True):
            buf.begin_user_action()
            buf.delete(prv, nxt)
            buf.end_user_action()
            return True
        return False

    def _list_continue(self):
        """Entrée dans une liste : prolonge ou termine l'élément courant."""
        buf = self.note_buffer
        it = buf.get_iter_at_mark(buf.get_insert())
        line = it.get_line()
        ls = buf.get_iter_at_line(line)[1]
        le = buf.get_iter_at_line(line + 1)[1] if line + 1 < buf.get_line_count() else buf.get_end_iter()
        before = buf.get_text(ls, it, True)
        after = buf.get_text(it, le, True).rstrip("\n")
        m = _LIST_RE.match(before)
        if m:
            indent, marker, task = m.group(1), m.group(2), m.group(4)
            if before[m.end():] == "" and after == "":   # élément vide : on quitte la liste
                self._remove_prefix(ls, len(indent), it)
                return True
            if marker[0].isdigit():
                marker = f"{int(re.match(r'\d+', marker).group(0)) + 1}."
            buf.begin_user_action()
            buf.insert_at_cursor("\n" + indent + marker + " " + (task or ""))
            buf.end_user_action()
            return True
        m = _QUOTE_RE.match(before)
        if m:
            if before[m.end():] == "" and after == "":   # citation vide : on sort
                keep = len(m.group(1)) - len(m.group(1).lstrip(" \t"))
                self._remove_prefix(ls, keep, it)
                return True
            buf.begin_user_action()
            buf.insert_at_cursor("\n" + m.group(1))
            buf.end_user_action()
            return True
        return False

    def _remove_prefix(self, ls, length, it):
        buf = self.note_buffer
        d = ls.copy(); d.forward_chars(length)
        buf.begin_user_action()
        buf.delete(d, it)
        buf.end_user_action()

    # ---------- interaction : cases à cocher et liens ----------
    def _on_note_click(self, gesture, n_press, x, y):
        """Clic simple sur une case à cocher ou un lien (aperçu actif uniquement)."""
        if n_press != 1 or self.notes_game_id is None:
            return
        hit = self._note_hit(x, y)
        if hit is None:
            return
        self.note_view.grab_focus()                        # le clavier reste dans l'éditeur
        kind, target = hit
        if kind == "task":
            self._toggle_task(*target)
        else:
            self._open_url(target)
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)   # ne pas déplacer le curseur

    def _on_note_motion(self, _ctl, x, y):
        """Curseur « main » au survol d'une case à cocher ou d'un lien."""
        over = self.notes_game_id is not None and self._note_hit(x, y) is not None
        self.note_view.set_cursor_from_name("pointer" if over else "text")

    def _note_hit(self, x, y):
        """Sous le pointeur : (« task », (début_ligne, a, b)) ou (« link », url), sinon None."""
        if self.notes_game_id is None:
            return None
        found, it = self.note_view.get_iter_at_location(
            *self.note_view.window_to_buffer_coords(Gtk.TextWindowType.WIDGET, int(x), int(y)))
        if not found:
            return None
        buf = self.note_buffer
        line = it.get_line()
        ls = buf.get_iter_at_line(line)[1]
        le = buf.get_iter_at_line(line + 1)[1] if line + 1 < buf.get_line_count() else buf.get_end_iter()
        raw = buf.get_text(ls, le, True)
        raw = raw[:-1] if raw.endswith("\n") else raw
        col = it.get_offset() - ls.get_offset()
        task = task_at(raw, col)
        if task:
            return ("task", (ls.get_offset(), task[0], task[1]))
        url = link_at(raw, col, self._line_in_code(line))
        return ("link", url) if url else None

    def _line_in_code(self, target):
        """La ligne `target` est-elle dans un bloc de code ``` ?"""
        buf = self.note_buffer
        in_code = False
        for lineno in range(target):
            ls = buf.get_iter_at_line(lineno)[1]
            le = buf.get_iter_at_line(lineno + 1)[1]
            raw = buf.get_text(ls, le, True).rstrip("\n")
            if is_fence(raw):
                in_code = not in_code
        return in_code

    def _toggle_task(self, line_offset, a, b):
        """Bascule `[ ]` <-> `[x]` sans déplacer le curseur."""
        buf = self.note_buffer
        s = buf.get_iter_at_offset(line_offset + a)
        e = buf.get_iter_at_offset(line_offset + b)
        new = "[ ]" if buf.get_text(s, e, True).lower() == "[x]" else "[x]"
        buf.begin_user_action()
        buf.delete(s, e)
        buf.insert(buf.get_iter_at_offset(line_offset + a), new)
        buf.end_user_action()

    def _open_url(self, url):
        if url.startswith("#"):                              # lien interne vers un titre
            self._scroll_to_anchor(url[1:])
            return
        try:
            Gtk.UriLauncher.new(url).launch(self.note_view, None, None)
        except (GLib.Error, TypeError):
            Gio.AppInfo.launch_default_for_uri(url, None)

    def _scroll_to_anchor(self, fragment):
        """Fait défiler jusqu'au titre visé par un lien `#ancre`."""
        buf = self.note_buffer
        count = buf.get_line_count()
        for lineno in range(count):
            ls = buf.get_iter_at_line(lineno)[1]
            le = buf.get_iter_at_line(lineno + 1)[1] if lineno + 1 < count else buf.get_end_iter()
            heading = heading_text(buf.get_text(ls, le, True).rstrip("\n"))
            if heading and anchor_matches(fragment, heading):
                it = buf.get_iter_at_line(lineno)[1]
                buf.place_cursor(it)
                self.note_view.scroll_to_iter(it, 0.2, False, 0.5, 0.5)
                self.note_view.grab_focus()
                return True
        return False

    # ---------- liste des jeux ----------
    def _refresh_notes(self):
        """Reconstruit la liste (page affichée, import, ajout d'un jeu…)."""
        self._fill_notes_list()

    def _has_notes(self):
        """Un jeu noté est-il visible dans la liste (masqués filtrés, non-installés gardés) ?"""
        rows = self.con.execute(
            f"SELECT notes FROM games {self._lib_where(trailing=True, sans_installe=True)}").fetchall()
        return any((r["notes"] or "").strip() for r in rows)

    def _note_games(self):
        """Jeux proposés : filtres bibliothèque SAUF non-installés (gardés), triés par nom."""
        rows = self.con.execute(
            f"SELECT id, name, notes FROM games {self._lib_where(trailing=True, sans_installe=True)}"
            "ORDER BY name COLLATE NOCASE").fetchall()
        needle = self.notes_search.get_text().strip().casefold()
        if needle:
            rows = [r for r in rows if needle in r["name"].casefold()]
        if self.notes_only.get_active():
            rows = [r for r in rows if r["notes"].strip()]
        return rows

    def _fill_notes_list(self):
        while (child := self.notes_list.get_first_child()) is not None:
            self.notes_list.remove(child)
        self._note_rows = {}
        for g in self._note_games():
            row = self._make_note_row(g)
            self.notes_list.append(row)
            self._note_rows[g["id"]] = row
        row = self._note_rows.get(self.notes_game_id)  # la sélection courante est conservée si possible
        if row is not None:
            self.notes_list.select_row(row)
        elif self.notes_game_id is not None:
            self._flush_note()
            self._clear_note_editor()

    def _make_note_row(self, g):
        row = Adw.ActionRow(title=GLib.markup_escape_text(g["name"]))
        row._gid = g["id"]
        icon = Gtk.Image(icon_name="document-edit-symbolic", valign=Gtk.Align.CENTER,
                         css_classes=["accent"], tooltip_text=tr("Has a note"),
                         visible=bool(g["notes"].strip()))
        row._note_icon = icon
        row.add_suffix(icon)
        return row

    def _on_notes_filter(self, *_):
        self._flush_note()  # une note en cours d'édition est enregistrée avant de filtrer
        self._fill_notes_list()

    def _on_note_selected(self, _lb, row):
        if row is None:
            return
        gid = getattr(row, "_gid", None)
        if gid == self.notes_game_id:
            return
        self._flush_note()  # on enregistre la note du jeu que l'on quitte
        self._show_note(gid)

    def _show_note(self, gid):
        self.notes_game_id = gid
        try:
            P.synchroniser(self.con, gid)  # le carnet overlay se vide (ou se remplit) ici
        except OSError:
            pass  # dossier Steam inaccessible : la note reste éditable
        g = self.con.execute("SELECT name, notes FROM games WHERE id=?", (gid,)).fetchone()
        if not g:
            self._clear_note_editor()
            return
        self.note_title.set_text(g["name"])
        self.note_subtitle.set_text(self._note_subtitle(gid))
        self._note_loading = True
        self.note_buffer.set_text(g["notes"] or "")
        self._note_loading = False
        self._note_dirty = False
        self.note_buffer.place_cursor(self.note_buffer.get_start_iter())
        self._apply_md()
        self.notes_right.set_visible_child_name("editor")

    def _note_subtitle(self, gid):
        inst, hid, pt, status = self.con.execute(
            "SELECT installed, hidden, playtime_min, status FROM games WHERE id=?", (gid,)).fetchone()
        extra = [P.fmt_minutes(pt)] if pt else []
        return " · ".join([P.status_label(status)] + extra + ([] if inst else [tr("not installed")])
                          + ([tr("hidden")] if hid else []))

    def _clear_note_editor(self):
        self.notes_game_id = None
        self._note_dirty = False
        self._set_toc([])
        self.notes_right.set_visible_child_name("empty")

    # ---------- édition et enregistrement ----------
    def _on_note_changed(self, *_):
        if self._note_loading or self.notes_game_id is None:
            return
        self._note_dirty = True
        self._schedule_md()  # le rendu suit la frappe
        if self._note_timeout is not None:  # on repousse l'enregistrement tant qu'on tape
            GLib.source_remove(self._note_timeout)
        self._note_timeout = GLib.timeout_add(NOTE_SAVE_DELAY, self._save_note)

    def _note_text(self):
        buf = self.note_buffer
        text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True)  # True : marqueurs compris
        return text[:-1] if text.endswith("\n") else text  # le buffer ajoute toujours un saut de ligne final

    def _save_note(self):
        self._note_timeout = None
        if not self._note_dirty or self.notes_game_id is None:
            return False
        gid, text = self.notes_game_id, self._note_text()
        P.set_note(self.con, gid, text)
        try:
            P.synchroniser(self.con, gid)  # la poussée reprend l'horodatage que set_note vient de poser
        except OSError:
            pass  # dossier Steam inaccessible : la note locale tient quand même
        self._note_dirty = False
        row = self._note_rows.get(gid)  # la pastille de la liste suit l'état de la note
        if row is not None:
            row._note_icon.set_visible(bool(text.strip()))
        return False

    def _flush_note(self):
        """Enregistre tout de suite (changement de page, de jeu ou de filtre)."""
        if self._note_timeout is not None:
            GLib.source_remove(self._note_timeout)
            self._note_timeout = None
        self._save_note()
