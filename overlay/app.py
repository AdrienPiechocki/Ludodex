"""app.py — l'overlay : une surface layer-shell plein écran avec des widgets librement placés.

Deux états :
  * FERMÉ (passif) : seuls les widgets « toujours visibles » sont dessinés, et la
    surface laisse passer toute la souris et tout le clavier — le jeu ne remarque rien ;
  * OUVERT (Maj+Tab) : tous les widgets activés, déplaçables à la souris, réglables
    (⚙), le clavier est pris (notes). Maj+Tab ou Échap referme.
La fenêtre n'a jamais de contenu quand rien n'est à montrer : transparente et traversable."""
import argparse, datetime, os, sys, time
from collections import deque

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, Gio, GLib, Graphene, Gsk, Gtk

try:
    gi.require_version("Gtk4LayerShell", "1.0")
    from gi.repository import Gtk4LayerShell as LS
except (ValueError, ImportError):
    LS = None

import cairo

from core.db import db, get_note, set_note
from i18n import tr
from . import APP_ID, config, data, windows
from .mdrender import MarkdownRenderer
from .shortcut import GlobalShortcut

POLL_SEC = 2            # session et succès : un SELECT toutes les 2 s
SESSION_WAIT_SEC = 15   # lancé avant que la session n'apparaisse en base : on patiente un peu

CSS = """
window.ludodex-overlay { background: transparent; }
window.ludodex-overlay.interactive { background: alpha(black, %(dim).2f); }
.ld-card { background: alpha(#0f1115, 0.80); color: #f4f4f5; border-radius: 14px; padding: 8px 14px 10px; }
.ld-card.interactive { border: 1px solid alpha(white, 0.28); }
.ld-header { margin-bottom: 4px; }
.ld-settings { background: alpha(white, 0.07); border-radius: 10px; padding: 8px 10px; margin-bottom: 6px; }
.ld-settings label { font-size: 12px; }
.ld-header label { font-size: 11px; font-weight: 700; opacity: 0.7; letter-spacing: 1px; }
.ld-header button { min-height: 22px; min-width: 22px; padding: 0 4px; color: #f4f4f5; }
.ld-clock { font-weight: 800; font-feature-settings: "tnum"; }
.ld-clock.done { color: #ff6b6b; }
.ld-ach-title { font-weight: 700; font-size: 15px; }
.ld-ach-desc { font-size: 12px; opacity: 0.75; }
.ld-ach-icon { font-size: 30px; }
.ld-notes textview, .ld-notes text { background: transparent; color: #f4f4f5; }
.ld-notes scrolledwindow { background: alpha(white, 0.06); border-radius: 8px; }
"""


# ------------------------------------------------------------------ canvas
class Canvas(Gtk.Widget):
    """Conteneur qui place chaque carte à sa fraction (config.place) : le placement suit la
    taille de la surface sans aucun minuteur ni image forcée."""
    __gtype_name__ = "LudodexCanvas"

    def __init__(self):
        super().__init__(hexpand=True, vexpand=True)
        self.cards = []
        self.on_drag_end = None
        self._dragged = None
        # Le geste vit sur le canevas, qui ne bouge pas : sur l'en-tête de la carte (qui se
        # déplace pendant le glisser), les décalages seraient mesurés dans un repère mobile
        # et le widget tremblerait.
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._drag_begin)
        drag.connect("drag-update", self._drag_update)
        drag.connect("drag-end", self._drag_end)
        self.add_controller(drag)

    def _drag_begin(self, gesture, x, y):
        hit = self.pick(x, y, Gtk.PickFlags.DEFAULT)
        for c in self.cards:
            if hit is not None and c.gear.get_visible() and (hit is c.gear or hit.is_ancestor(c.gear)):
                break   # le bouton ⚙ garde son clic : on ne le prend pas pour un début de glisser
            if c.header.get_visible() and hit is not None and (hit is c.header or hit.is_ancestor(c.header)):
                self._dragged, self._origin = c, c.disp[:2]
                self.raise_card(c)
                gesture.set_state(Gtk.EventSequenceState.CLAIMED)
                return
        self._dragged = None
        gesture.set_state(Gtk.EventSequenceState.DENIED)

    def _drag_update(self, _g, dx, dy):
        c = self._dragged
        if c is None:
            return
        _x, _y, dw, dh = c.disp          # taille À L'ÉCRAN (échelle comprise)
        W, H = self.get_width(), self.get_height()
        x = min(max(self._origin[0] + dx, 0), max(0, W - dw))
        y = min(max(self._origin[1] + dy, 0), max(0, H - dh))
        c.cfg["x"], c.cfg["y"] = config.fraction(x, y, W, H, dw, dh)
        self.queue_allocate()

    def _drag_end(self, *_):
        if self._dragged is not None and self.on_drag_end:
            self.on_drag_end()
        self._dragged = None

    def add(self, card):
        card.set_parent(self)
        self.cards.append(card)

    def do_measure(self, orientation, for_size):
        return 0, 0, -1, -1

    def raise_card(self, card):
        """Met `card` au premier plan (ses réglages ouverts, ou pendant son déplacement)."""
        if self.cards[-1] is not card:
            self.cards.remove(card)
            self.cards.append(card)
            card.insert_before(self, None)

    def do_size_allocate(self, width, height, baseline):
        for c in self.cards:
            if not c.get_visible():
                continue
            w = max(c.measure(Gtk.Orientation.HORIZONTAL, -1)[1], 1)
            h = max(c.measure(Gtk.Orientation.VERTICAL, w)[1], 1)
            # Mise en page à la taille naturelle ; réduite d'un bloc seulement si elle dépasse l'écran.
            s = min(1.0, width / w, height / h)
            sw, sh = w * s, h * s
            x, y = config.place(c.cfg["x"], c.cfg["y"], width, height, sw, sh)
            c.disp = (x, y, sw, sh)
            t = Gsk.Transform().translate(Graphene.Point().init(x, y)).scale(s, s)
            c.allocate(w, h, -1, t)

    def do_dispose(self):
        for c in self.cards:
            c.unparent()
        self.cards = []


# ------------------------------------------------------------------ cartes
class Card(Gtk.Box):
    """Un widget de l'overlay : en-tête (poignée de déplacement, ⚙, titre) visible seulement
    quand l'overlay est ouvert, puis le corps propre à chaque widget."""
    KEY = ""
    TITLE = ""

    def __init__(self, ov):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.ov = ov
        self.cfg = ov.cfg["widgets"][self.KEY]
        self.interactive = False
        self.disp = (0, 0, 0, 0)    # position et taille à l'écran, posées par Canvas
        self.add_css_class("ld-card")
        self.set_halign(Gtk.Align.START)
        self.set_valign(Gtk.Align.START)

        self.header = Gtk.Box(spacing=6, css_classes=["ld-header"])
        self.header.append(Gtk.Label(label=tr(self.TITLE).upper(), xalign=0, hexpand=True,
                                     cursor=Gdk.Cursor.new_from_name("grab")))
        # Réglages EN LIGNE dans la carte (⚙ les déplie) et non dans un popover : un popup est une
        # surface xdg_popup que tous les compositeurs de layer-shell ne savent pas afficher
        # (CyberRealm ne gère pas les popups, et encore moins imbriqués).
        self.gear = Gtk.ToggleButton(icon_name="emblem-system-symbolic", has_frame=False,
                                     tooltip_text=tr("Widget settings"))
        self.header.append(self.gear)
        self.header.set_visible(False)
        self.append(self.header)
        self._pop_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                                css_classes=["ld-settings"], visible=False)
        self.gear.connect("toggled", lambda *_: self._sync_settings())
        self.append(self._pop_box)
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.append(self.body)

        self._setting_row(tr("Always visible"), self._switch("pinned"))
        self._setting_row(tr("Opacity"), self._scale("opacity", 0.2, 1.0, 0.05))
        self.build_settings()
        self.apply()

    # -- réglages
    def _setting_row(self, label, widget):
        row = Gtk.Box(spacing=12)
        row.append(Gtk.Label(label=label, xalign=0, hexpand=True))
        row.append(widget)
        self._pop_box.append(row)

    def _switch(self, key, after=None):
        sw = Gtk.Switch(active=self.cfg[key], valign=Gtk.Align.CENTER)

        def changed(s, _p):
            self.cfg[key] = s.get_active()
            self.ov.save()
            self.apply()
            self.ov.refresh()
            if after:
                after()
        sw.connect("notify::active", changed)
        return sw

    def _scale(self, key, lo, hi, step):
        sc = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, lo, hi, step)
        sc.set_value(self.cfg[key])
        sc.set_size_request(140, -1)

        def changed(s):
            self.cfg[key] = s.get_value()
            self.ov.save()
            self.apply()
        sc.connect("value-changed", changed)
        return sc

    def _spin(self, key, lo, hi, step=1):
        sp = Gtk.SpinButton.new_with_range(lo, hi, step)
        sp.set_value(self.cfg[key])

        def changed(s):
            self.cfg[key] = int(s.get_value())
            self.ov.save()
            self.apply()
        sp.connect("value-changed", changed)
        return sp

    def build_settings(self):
        pass

    # -- état
    def apply(self):
        """Reflète la config : opacité, texte… (surchargé)."""
        self.set_opacity(self.cfg["opacity"])

    def _sync_settings(self):
        self._pop_box.set_visible(self.interactive and self.gear.get_active())
        if self._pop_box.get_visible() and self.get_parent() is not None:
            self.get_parent().raise_card(self)
        self.queue_resize()

    def set_interactive(self, on):
        self.interactive = on
        self.header.set_visible(on)
        self._sync_settings()
        self.set_css_classes(["ld-card"] + (["interactive"] if on else []) + self.extra_classes())
        self.on_mode()

    def extra_classes(self):
        return []

    def on_mode(self):
        pass

    def wants_display(self):
        """Visible hors ouverture ? (passif) — par défaut : si « toujours visible »."""
        return self.cfg["pinned"]

    def tick(self):
        pass


class ClockCard(Card):
    KEY, TITLE = "clock", "Clock"

    def __init__(self, ov):
        self.running, self.accum, self.t0 = False, 0.0, 0.0
        super().__init__(ov)
        self._build_body()
        self.on_mode()

    def build_settings(self):
        modes = [("clock", tr("Clock")), ("session", tr("Session time")),
                 ("stopwatch", tr("Stopwatch")), ("countdown", tr("Countdown"))]
        # Des boutons liés plutôt qu'un menu déroulant : un GtkDropDown ouvre un popover DANS ce
        # popover, et les popups imbriqués ne s'affichent pas partout (CyberRealm, layer-shell).
        row = Gtk.Box(css_classes=["linked"], halign=Gtk.Align.FILL)
        first = None
        for key, label in modes:
            b = Gtk.ToggleButton(label=label, hexpand=True, active=(key == self.cfg["mode"]))
            if first is None:
                first = b
            else:
                b.set_group(first)

            def picked(btn, k=key):
                if btn.get_active() and self.cfg["mode"] != k:
                    self.cfg["mode"] = k
                    self.ov.save()
                    self.reset()
            b.connect("toggled", picked)
            row.append(b)
        self._pop_box.append(row)
        self._setting_row(tr("24-hour clock"), self._switch("hour24"))
        self._setting_row(tr("Show seconds"), self._switch("seconds"))
        self._setting_row(tr("Countdown (min)"), self._spin("countdown_min", 1, 600))
        self._setting_row(tr("Text size"), self._spin("size", 8, 120, 2))

    def apply(self):
        super().apply()
        if hasattr(self, "label"):
            self.tick()

    def _build_body(self):
        self.label = Gtk.Label(css_classes=["ld-clock"], xalign=1)
        self.body.append(self.label)
        self.controls = Gtk.Box(spacing=6, halign=Gtk.Align.END, margin_top=4)
        self.start_btn = Gtk.Button(label=tr("Start"), css_classes=["flat"])
        self.start_btn.connect("clicked", lambda _b: self.toggle_run())
        reset = Gtk.Button(icon_name="view-refresh-symbolic", css_classes=["flat"],
                           tooltip_text=tr("Reset"))
        reset.connect("clicked", lambda _b: self.reset())
        self.controls.append(self.start_btn)
        self.controls.append(reset)
        self.body.append(self.controls)

    def on_mode(self):
        self.controls.set_visible(self.interactive and self.cfg["mode"] in ("stopwatch", "countdown"))
        self.tick()

    def elapsed(self):
        return self.accum + (time.monotonic() - self.t0 if self.running else 0.0)

    def toggle_run(self):
        if self.running:
            self.accum, self.running = self.elapsed(), False
        else:
            self.t0, self.running = time.monotonic(), True
        self.tick()

    def reset(self):
        self.running, self.accum = False, 0.0
        self.on_mode()

    def tick(self):
        if not hasattr(self, "label"):
            return
        c, mode = self.cfg, self.cfg["mode"]
        done = False
        if mode == "clock":
            text = config.clock_text(datetime.datetime.now(), c["hour24"], c["seconds"])
        elif mode == "session":
            text = config.duration_text(time.time() - self.ov.session_started, c["seconds"])
        elif mode == "stopwatch":
            text = config.duration_text(self.elapsed(), True)
        else:
            left = c["countdown_min"] * 60 - self.elapsed()
            done = left <= 0
            text = config.duration_text(max(0, left + (0.999 if left > 0 else 0)), True)
        self.label.set_markup(f'<span size="{c["size"] * 1024}">{GLib.markup_escape_text(text)}</span>')
        self.label.set_css_classes(["ld-clock"] + (["done"] if done else []))
        if hasattr(self, "start_btn"):
            self.start_btn.set_label(tr("Pause") if self.running else tr("Start"))
        self.queue_resize()


class AchievementsCard(Card):
    KEY, TITLE = "achievements", "Achievement unlocked"

    def __init__(self, ov):
        self.queue, self.current, self.until = deque(), None, 0.0
        super().__init__(ov)
        self.icon = Gtk.Label(label=data.TROPHY, css_classes=["ld-ach-icon"])
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
        self.title, self.desc = (Gtk.Label(xalign=0, css_classes=["ld-ach-title"]),
                                 Gtk.Label(xalign=0, wrap=True, max_width_chars=36,
                                           css_classes=["ld-ach-desc"]))
        text.append(self.title)
        text.append(self.desc)
        row = Gtk.Box(spacing=12)
        row.append(self.icon)
        row.append(text)
        self.body.append(row)
        self._render()

    def build_settings(self):
        self._setting_row(tr("Display time (s)"), self._spin("duration", 2, 30))
        test = Gtk.Button(label=tr("Test"))
        test.connect("clicked", lambda _b: self.push(
            {"title": tr("Achievement unlocked"), "description": tr("Preview"), "icon": data.TROPHY}))
        self._pop_box.append(test)

    def push(self, ach):
        self.queue.append(ach)
        self.ov.refresh()

    def wants_display(self):
        return self.current is not None

    def tick(self):
        now = time.monotonic()
        if self.current and now >= self.until:
            self.current = None
        if self.current is None and self.queue:
            self.current = self.queue.popleft()
            self.until = now + self.cfg["duration"]
        self._render()
        self.ov.refresh()

    def _render(self):
        a = self.current or {"title": tr("Achievement unlocked"), "description": "", "icon": data.TROPHY}
        self.icon.set_label(a["icon"])
        self.title.set_label(a["title"])
        self.desc.set_label(a["description"])
        self.desc.set_visible(bool(a["description"]))


class NotesCard(Card):
    KEY, TITLE = "notes", "Notes"

    def __init__(self, ov):
        self._dirty, self._save_id, self._loading = False, 0, False
        super().__init__(ov)
        self.view = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, left_margin=10, right_margin=10,
                                 top_margin=8, bottom_margin=8, editable=False, cursor_visible=False)
        self.scroll = Gtk.ScrolledWindow(child=self.view, hexpand=True, vexpand=True,
                                         hscrollbar_policy=Gtk.PolicyType.NEVER)
        # les cases à cocher sont dessinées par-dessus la zone de défilement (cf. mdrender)
        self.area = Gtk.DrawingArea(hexpand=True, vexpand=True)
        for adj in (self.scroll.get_vadjustment(), self.scroll.get_hadjustment()):
            adj.connect("value-changed", lambda *_: self.area.queue_draw())
        stack = Gtk.Overlay(child=self.scroll)
        stack.add_overlay(self.area)
        self.body.append(stack)
        self.md = MarkdownRenderer(self.view, self.area)
        self.view.get_buffer().connect("changed", self._changed)
        self.load()
        self.apply()

    def extra_classes(self):
        return ["ld-notes"]

    def build_settings(self):
        self._setting_row(tr("Text size"), self._spin("size", 8, 40))
        self._setting_row(tr("Width"), self._spin("width", 200, 1200, 20))
        self._setting_row(tr("Height"), self._spin("height", 120, 1000, 20))

    def apply(self):
        super().apply()
        if hasattr(self, "md"):
            self.scroll.set_size_request(self.cfg["width"], self.cfg["height"])
            self.md.set_font_size(self.cfg["size"])

    def load(self):
        """(Re)lit la note en base — sauf modification locale pas encore enregistrée."""
        if self._dirty:
            return
        text = get_note(self.ov.con, self.ov.gid)
        b = self.view.get_buffer()
        if b.get_text(b.get_start_iter(), b.get_end_iter(), True) != text:
            self._loading = True
            b.set_text(text)
            self._loading = False

    def on_mode(self):
        self.view.set_editable(self.interactive)
        self.view.set_cursor_visible(self.interactive)
        self.md.set_interactive(self.interactive)
        if self.interactive:
            self.load()
            self.view.grab_focus()
        else:
            self.flush()

    def _changed(self, buf):
        if self._loading:
            return
        self._dirty = True
        if self._save_id:
            GLib.source_remove(self._save_id)
        self._save_id = GLib.timeout_add(700, self.flush)

    def flush(self):
        self._save_id = 0
        if self._dirty:
            b = self.view.get_buffer()
            text = b.get_text(b.get_start_iter(), b.get_end_iter(), True)   # marqueurs compris
            set_note(self.ov.con, self.ov.gid, text[:-1] if text.endswith("\n") else text)
            self._dirty = False
        return False


CARDS = (ClockCard, AchievementsCard, NotesCard)


# ------------------------------------------------------------------ application
def parse_args(argv):
    p = argparse.ArgumentParser(prog="ludodex-overlay", description="Ludodex in-game overlay")
    p.add_argument("--game", type=int, metavar="ID", help="game id (starts the overlay for it)")
    p.add_argument("--pid", type=int, metavar="PID",
                   help="with --game: process launched for the game (to find its window)")
    p.add_argument("--keep", action="store_true",
                   help="with --game: stay open without a running session (for testing)")
    p.add_argument("--toggle", action="store_true", help="open/close the running overlay")
    p.add_argument("--quit", action="store_true", help="stop the running overlay")
    return p.parse_args(argv)


class Overlay(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.win = None
        self.con = None
        self.gid = None
        self.interactive = False
        self.keep = False
        self.minimized = False
        self.root_pid, self.tracker, self._pids_at, self._pids = None, None, 0.0, set()
        self.cfg = config.load()
        self._save_id = 0

    # -- ligne de commande (la 2e instance transmet ses arguments à la première)
    def do_command_line(self, cl):
        try:
            ns = parse_args(cl.get_arguments()[1:])
        except SystemExit as e:
            return int(e.code or 0)
        if ns.quit:
            if self.win:
                self.shutdown()
            return 0
        if ns.game is not None and ns.game != self.gid:
            self.keep = ns.keep
            self.start_game(ns.game, ns.pid)
        if ns.toggle and self.win:
            self.set_interactive(not self.interactive)
        if self.win is None:
            self.quit()   # ni jeu ni fenêtre : rien à faire (ex. --toggle sans overlay lancé)
        return 0

    # -- démarrage / arrêt
    def start_game(self, gid, pid=None):
        if self.con is None:
            self.con = db()
        g = data.game(self.con, gid)
        if g is None:
            return
        self.gid, self.root_pid, self.game_name = gid, pid, g["name"]
        self.motifs, self._pids_at = data.motifs(self.con, gid), 0.0
        self.session_id, self.session_started = None, time.time()
        self._wait_until = time.monotonic() + SESSION_WAIT_SEC
        self.known = data.unlocked(self.con, gid)   # base : on n'annonce que les NOUVEAUX succès
        if self.win is None:
            self._build_window()
            self.hold()
            GLib.timeout_add(1000, self._tick)
            GLib.timeout_add_seconds(POLL_SEC, self._poll)
            self.shortcut = GlobalShortcut(lambda: self.set_interactive(not self.interactive))
            self.shortcut.start()
        if self.tracker is None:
            self.tracker = windows.GameWindow(self._game_pids, self.game_name, self._on_game_window,
                                              get_since=lambda: self.session_started)
            self.tracker.start(self)
        else:
            self.tracker.name, self.tracker.last = self.game_name, False    # autre jeu : on renotifie
        for c in self.cards:
            if isinstance(c, NotesCard):
                c.flush()
                c.load()
        self._poll()
        self.refresh()

    def shutdown(self, *_):
        for c in getattr(self, "cards", []):
            if isinstance(c, NotesCard):
                c.flush()
        self.save(now=True)
        if self.tracker:
            self.tracker.stop()
        self.win = None
        self.quit()

    # -- fenêtre
    def _build_window(self):
        css = Gtk.CssProvider()
        text = CSS % {"dim": self.cfg["dim"]}
        try:
            css.load_from_string(text)
        except AttributeError:
            css.load_from_data(text.encode())
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        win = Gtk.Window(title="Ludodex overlay")
        win.add_css_class("ludodex-overlay")
        if LS is not None and LS.is_supported():
            LS.init_for_window(win)
            LS.set_namespace(win, "ludodex-overlay")
            LS.set_layer(win, LS.Layer.OVERLAY)
            for edge in (LS.Edge.TOP, LS.Edge.BOTTOM, LS.Edge.LEFT, LS.Edge.RIGHT):
                LS.set_anchor(win, edge, True)
            LS.set_exclusive_zone(win, -1)   # pas de réservation, et pas décalé par une barre
            LS.set_keyboard_mode(win, LS.KeyboardMode.NONE)
            mon = self._monitor()
            if mon is not None:
                LS.set_monitor(win, mon)
        else:   # développement / compositeur sans layer-shell : simple fenêtre
            win.set_default_size(1280, 720)
        self.canvas = Canvas()
        self.canvas.on_drag_end = self.save
        win.set_child(self.canvas)
        self.cards = [cls(self) for cls in CARDS]
        for c in self.cards:
            self.canvas.add(c)
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_key)
        win.add_controller(keys)
        win.connect("map", lambda *_: self._apply_input())
        win.connect("close-request", lambda *_: self.shutdown() or True)
        self.win = win
        self.set_interactive(False)
        win.present()

    def _monitor(self):
        name = self.cfg["monitor"]
        if not name:
            return None
        mons = Gdk.Display.get_default().get_monitors()
        for i in range(mons.get_n_items()):
            if mons.get_item(i).get_connector() == name:
                return mons.get_item(i)
        return None

    def _on_key(self, _ctl, keyval, _code, state):
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        if self.interactive and ((keyval in (Gdk.KEY_ISO_Left_Tab, Gdk.KEY_Tab) and shift)
                                 or keyval == Gdk.KEY_Escape):
            self.set_interactive(False)
            return True
        return False

    # -- fenêtre du jeu : échelle et mise en veille
    def _game_pids(self):
        """PIDs du jeu : le processus lancé par Ludodex et ses descendants, plus ceux que les
        sources du jeu (dossier Steam, Heroic…) y rattachent. Relu au plus toutes les 2 s."""
        now = time.monotonic()
        if now - self._pids_at > 2.0:
            self._pids_at = now
            pids = set()
            if self.root_pid:
                pids |= windows.descendants(self.root_pid, windows.process_children())
            pids |= data.pids_of(self.motifs)
            self._pids = pids
        return self._pids

    def _on_game_window(self, win):
        """Appelé à chaque changement d'état de la fenêtre du jeu (None = introuvable : jamais
        réduit — on ne cache pas l'overlay sur une simple incertitude)."""
        self.minimized = bool(win and win.minimized)
        if os.environ.get("LUDODEX_OVERLAY_DEBUG"):
            print(f"overlay: fenêtre du jeu = {win}", file=sys.stderr)
        if self.minimized and self.interactive:
            self.set_interactive(False)      # jeu réduit : plus rien à piloter
        self._apply_presence()

    def _hidden(self):
        return self.minimized

    def _apply_presence(self):
        self.win.set_visible(not self._hidden())
        if not self._hidden():
            self._apply_input()

    # -- états
    def set_interactive(self, on):
        if on and self._hidden():
            return                           # jeu réduit : le raccourci n'ouvre rien
        self.interactive = on
        if LS is not None and LS.is_supported():
            LS.set_keyboard_mode(self.win, LS.KeyboardMode.EXCLUSIVE if on else LS.KeyboardMode.NONE)
        if on:
            self.win.add_css_class("interactive")
        else:
            self.win.remove_css_class("interactive")
        for c in self.cards:
            c.set_interactive(on)
        self.refresh()
        self._apply_input()
        if not on:
            self._apply_presence()           # fermé sans focus sur le jeu : on se retire

    def refresh(self):
        """Visibilité de chaque carte selon l'état, et région de saisie de la surface."""
        for c in self.cards:
            c.set_visible(c.cfg["enabled"] and (self.interactive or c.wants_display()))
        self.canvas.queue_allocate()
        self._apply_input()

    def _apply_input(self):
        """Fermé : région de saisie VIDE, la souris traverse jusqu'au jeu. Ouvert : toute la surface."""
        surf = self.win.get_surface() if self.win else None
        if surf is None:
            return
        if self.interactive:
            rect = cairo.RectangleInt(0, 0, max(1, self.win.get_width()), max(1, self.win.get_height()))
            surf.set_input_region(cairo.Region(rect))
        else:
            surf.set_input_region(cairo.Region())

    # -- minuteries
    def _tick(self):
        if self.win is None:
            return False
        for c in self.cards:
            if c.get_visible() or isinstance(c, AchievementsCard):
                c.tick()
        return True

    def _poll(self):
        """Fin de session → on s'éteint ; nouveaux succès → on les annonce."""
        if self.win is None:
            return False
        try:
            if self.session_id is None:
                s = data.open_session(self.con, self.gid)
                if s:
                    self.session_id, self.session_started = s
                elif time.monotonic() > self._wait_until and not self.keep:
                    self.shutdown()
                    return False
            elif not data.session_is_open(self.con, self.session_id) and not self.keep:
                self.shutdown()
                return False
            now = data.unlocked(self.con, self.gid)
        except Exception as e:   # base momentanément verrouillée, etc. : on réessaie au tour suivant
            print("overlay:", e)
            return True
        ach = next(c for c in self.cards if isinstance(c, AchievementsCard))
        for a in data.new_unlocks(self.known, now):
            ach.push(a)
        self.known = now
        return True

    # -- config
    def save(self, now=False):
        if now:
            if self._save_id:
                GLib.source_remove(self._save_id)
            self._save_id = 0
            config.save(self.cfg)
        elif not self._save_id:
            self._save_id = GLib.timeout_add(400, lambda: (setattr(self, "_save_id", 0),
                                                           config.save(self.cfg), False)[-1])
