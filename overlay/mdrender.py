"""mdrender.py — rendu Markdown « en direct » d'un Gtk.TextView, pour les notes de l'overlay.

Même analyse que la page Notes de Ludodex (`ui.markdown.line_elements`, GTK-free) et
mêmes styles : titres, gras, italique, barré, code, citations, liens, listes, tâches
cochables. Le buffer garde toujours le Markdown d'origine ; le rendu n'est qu'une pose
de balises. Ouvert (éditable), la syntaxe réapparaît sous le curseur, comme dans
Ludodex ; fermé (lecture seule), tout est rendu.

Différence avec la page Notes : le fond de l'overlay est translucide, on ne peut donc pas
« peindre en couleur du fond » le `[ ]` pour le cacher. On masque le préfixe de la
tâche (`- [ ] `) et on réserve une marge à gauche où la case est dessinée."""
import math, os, sys

import cairo
import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk, Pango

from ui.markdown import (HEADING_SCALE, heading_level, is_fence, line_elements, link_at,
                         markers_revealed, task_at)

CHECK_ACCENT = (0.208, 0.518, 0.894)   # #3584e4, comme Ludodex
CHECK_BORDER = (0.80, 0.80, 0.80)      # clair : le fond de l'overlay est sombre


class MarkdownRenderer:
    def __init__(self, view, area):
        """`view` : Gtk.TextView ; `area` : Gtk.DrawingArea superposée (cases à cocher)."""
        self.view, self.area = view, area
        self.buf = view.get_buffer()
        self.interactive = False
        self.margin = 24            # place réservée à la case, à gauche du texte d'une tâche
        self._applying, self._idle = False, None
        self._retries = 0
        self._boxes = []            # (x, y, côté, début de ligne, a, b) en coords fenêtre
        self._tasks = []
        self._tag_names = []
        self._font_css = Gtk.CssProvider()
        view.get_style_context().add_provider(self._font_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self._create_tags()
        # La mise en page du TextView est asynchrone : on redessine quand elle change
        # (contenu, taille, défilement) — pas seulement quand les balises sont posées.
        view.get_vadjustment().connect("changed", lambda *_: area.queue_draw())
        area.connect("map", lambda *_: area.queue_draw())
        area.set_can_target(False)
        area.set_draw_func(self._draw)
        self.buf.connect("changed", lambda *_: self.schedule())
        self.buf.connect("notify::cursor-position", lambda *_: self._applying or self.schedule())

        click = Gtk.GestureClick(button=1, propagation_phase=Gtk.PropagationPhase.CAPTURE)
        click.connect("pressed", self._on_click)
        view.add_controller(click)

    # ---- balises
    def _create_tags(self):
        b = self.buf
        names = ["md-hidden", "md-bold", "md-italic", "md-bi", "md-strike", "md-code", "md-quote",
                 "md-link", "md-list", "md-checked"]
        b.create_tag("md-hidden", invisible=True)
        b.create_tag("md-bold", weight=Pango.Weight.BOLD)
        b.create_tag("md-italic", style=Pango.Style.ITALIC)
        b.create_tag("md-bi", weight=Pango.Weight.BOLD, style=Pango.Style.ITALIC)
        b.create_tag("md-strike", strikethrough=True)
        b.create_tag("md-code", family="monospace")
        b.create_tag("md-quote", style=Pango.Style.ITALIC)
        b.create_tag("md-link", foreground="#78aeed", underline=Pango.Underline.SINGLE)
        b.create_tag("md-list")
        b.create_tag("md-checked", strikethrough=True)
        for lvl, scale in HEADING_SCALE.items():
            b.create_tag(f"md-h{lvl}", scale=scale, weight=Pango.Weight.BOLD)
            names.append(f"md-h{lvl}")
        for lvl in range(1, 5):
            b.create_tag(f"md-indent{lvl}", left_margin=10 + lvl * 18, indent=-14)
            b.create_tag(f"md-taskline{lvl}")
            names += [f"md-indent{lvl}", f"md-taskline{lvl}"]
        self._tag_names = names
        self.set_font_size(13)

    def set_font_size(self, size):
        self._font_css.load_from_string(f"textview {{ font-size: {size}pt; }}")
        self.margin = 12 + int(size * 1.4)
        table = self.buf.get_tag_table()
        for lvl in range(1, 5):
            table.lookup(f"md-taskline{lvl}").set_property(
                "left_margin", 10 + self.margin + (lvl - 1) * 18)
        self.schedule()

    def set_interactive(self, on):
        self.interactive = on
        self.schedule()

    # ---- rendu
    def schedule(self):
        if self._idle is None:
            self._idle = GLib.idle_add(self._run)

    def _run(self):
        self._idle = None
        self.apply()
        return False

    def _tag(self, ls, name, a, b):
        if b <= a:
            return
        s, e = ls.copy(), ls.copy()
        s.forward_chars(a)
        e.forward_chars(b)
        self.buf.apply_tag_by_name(name, s, e)

    def apply(self):
        if self._applying:
            return
        self._applying = True
        try:
            buf = self.buf
            start, end = buf.get_bounds()
            for name in self._tag_names:
                buf.remove_tag_by_name(name, start, end)
            self._tasks = []
            ins = buf.get_iter_at_mark(buf.get_insert())
            # fermé (lecture seule) : aucune ligne n'est « sous le curseur »
            cursor_line = ins.get_line() if self.interactive else -1
            count, in_code = buf.get_line_count(), False
            for n in range(count):
                ls = buf.get_iter_at_line(n)[1]
                le = buf.get_iter_at_line(n + 1)[1] if n + 1 < count else buf.get_end_iter()
                raw = buf.get_text(ls, le, True)
                raw = raw[:-1] if raw.endswith("\n") else raw
                if is_fence(raw):
                    if n != cursor_line:
                        self._tag(ls, "md-hidden", 0, len(raw))
                    in_code = not in_code
                    continue
                col = ins.get_offset() - ls.get_offset() if n == cursor_line else -1
                line = line_elements(raw, in_code)
                for el in line.elements:
                    if not markers_revealed(el.markers, col):
                        for a, b in el.markers:
                            self._tag(ls, "md-hidden", a, b)
                    if el.kind is not None and el.cend > el.cstart:
                        self._tag(ls, f"md-{el.kind}", el.cstart, el.cend)
                task_open = False
                if line.task:
                    ts, te, checked = line.task
                    task_open = markers_revealed([(ts, te)], col)
                    if not task_open:
                        lead = te
                        while lead < len(raw) and raw[lead] in " \t":
                            lead += 1
                        # Tâche SANS texte : tout masquer laisserait une ligne sans rien de
                        # visible, donc de hauteur nulle (la case n'aurait plus de repère
                        # vertical). On garde visible la dernière espace — la syntaxe en exige
                        # toujours une après `[ ]` — qui donne à la ligne sa hauteur normale.
                        if lead >= len(raw):
                            lead = max(te, len(raw) - 1)
                        self._tag(ls, "md-hidden", 0, lead)
                        self._tag(ls, f"md-taskline{line.indent or 1}", 0, len(raw))
                        self._tasks.append((ls.get_offset() + lead, ls.get_offset(), ts, te, checked,
                                            line.indent or 1))
                if line.indent and not (line.task and not task_open):
                    self._tag(ls, f"md-indent{line.indent}", 0, le.get_offset() - ls.get_offset())
                if line.marker and (not line.task or task_open):
                    self._tag(ls, "md-list", *line.marker)
                if line.checked and not task_open:
                    self._tag(ls, "md-checked", *line.checked)
        finally:
            self._applying = False
            self.area.queue_draw()
            GLib.timeout_add(40, lambda: self.area.queue_draw() or False)   # y : après la remise en page

    # ---- cases à cocher
    def _draw(self, _area, cr, _w, _h):
        view, buf = self.view, self.buf
        self._boxes = []
        pending = False
        for first_vis, line_off, ts, te, checked, lvl in self._tasks:
            it = buf.get_iter_at_offset(first_vis)
            loc = view.get_iter_location(it)
            top, height = loc.y, loc.height
            if height < 4:
                # Tâche sans texte : le seul caractère visible est une espace finale, dont GTK
                # ne mesure pas le rectangle (0x0). La ligne, elle, a sa hauteur normale.
                top, height = view.get_line_yrange(it)
            if height < 4:      # vraiment pas encore mise en page : on réessaiera
                pending = True
                continue
            _, y = view.buffer_to_window_coords(Gtk.TextWindowType.WIDGET, loc.x, top)
            side = max(10.0, min(height - 3.0, self.margin - 8.0))
            # x vient des marges (déterministes), jamais de la position du texte : juste après
            # un changement de balises la mise en page est périmée et le texte pas encore
            # recalé — la case atterrirait dessus.
            bx = view.get_left_margin() + (lvl - 1) * 18 + 2.0
            by = y + (height - side) / 2.0
            self._boxes.append((bx, by, side, line_off, ts, te))
            self._path(cr, bx, by, side)
            if checked:
                cr.set_source_rgb(*CHECK_ACCENT)
                cr.fill()
                cr.set_source_rgb(1, 1, 1)
                cr.set_line_width(max(1.6, side * 0.16))
                cr.set_line_cap(cairo.LINE_CAP_ROUND)
                cr.set_line_join(cairo.LINE_JOIN_ROUND)
                cr.move_to(bx + side * 0.24, by + side * 0.52)
                cr.line_to(bx + side * 0.43, by + side * 0.72)
                cr.line_to(bx + side * 0.78, by + side * 0.28)
                cr.stroke()
            else:
                cr.set_source_rgb(*CHECK_BORDER)
                cr.set_line_width(max(1.3, side * 0.11))
                cr.stroke()

        if os.environ.get("LUDODEX_OVERLAY_DEBUG"):
            print(f"overlay: tâches={len(self._tasks)} cases={len(self._boxes)} "
                  f"en attente={pending} zone={self.area.get_width()}x{self.area.get_height()}",
                  file=sys.stderr)
        if pending and self._retries < 40:
            self._retries += 1
            GLib.timeout_add(50, lambda: self.area.queue_draw() or False)
        elif not pending:
            self._retries = 0

    @staticmethod
    def _path(cr, x, y, side):
        r = side * 0.28
        cr.new_path()
        cr.arc(x + side - r, y + r, r, -math.pi / 2, 0)
        cr.arc(x + side - r, y + side - r, r, 0, math.pi / 2)
        cr.arc(x + r, y + side - r, r, math.pi / 2, math.pi)
        cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
        cr.close_path()

    # ---- clics (ouvert seulement) : cases et liens
    def _on_click(self, gesture, n_press, x, y):
        if not self.interactive or n_press != 1:
            return
        for bx, by, side, line_off, a, b in self._boxes:
            if bx - 3 <= x <= bx + side + 3 and by - 3 <= y <= by + side + 3:
                self._toggle(line_off, a, b)
                gesture.set_state(Gtk.EventSequenceState.CLAIMED)
                return
        url = self._link_at(x, y)
        if url:
            if not url.startswith("#"):
                Gio.AppInfo.launch_default_for_uri_async(url, None, None, None)
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)

    def _link_at(self, x, y):
        view, buf = self.view, self.buf
        found, it = view.get_iter_at_location(
            *view.window_to_buffer_coords(Gtk.TextWindowType.WIDGET, int(x), int(y)))
        if not found:
            return None
        n = it.get_line()
        ls = buf.get_iter_at_line(n)[1]
        le = buf.get_iter_at_line(n + 1)[1] if n + 1 < buf.get_line_count() else buf.get_end_iter()
        raw = buf.get_text(ls, le, True).rstrip("\n")
        in_code = False
        for k in range(n):
            a = buf.get_iter_at_line(k)[1]
            z = buf.get_iter_at_line(k + 1)[1]
            if is_fence(buf.get_text(a, z, True).rstrip("\n")):
                in_code = not in_code
        return link_at(raw, it.get_offset() - ls.get_offset(), in_code)

    def _toggle(self, line_off, a, b):
        buf = self.buf
        s, e = buf.get_iter_at_offset(line_off + a), buf.get_iter_at_offset(line_off + b)
        new = "[ ]" if buf.get_text(s, e, True).lower() == "[x]" else "[x]"
        buf.begin_user_action()
        buf.delete(s, e)
        buf.insert(buf.get_iter_at_offset(line_off + a), new)
        buf.end_user_action()
