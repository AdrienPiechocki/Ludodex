"""main.py — fenêtre principale (Win) et application (App)."""
import sys

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, GObject, Gtk, Pango

import picker as P
from i18n import available, get_lang, tr
from .theme import _install_css
from .pickview import PickViewMixin
from .library import LibraryMixin
from .retrospec import RetroMixin
from .notes import NotesMixin
from .achievements import AchievementsMixin
from .settingsui import SettingsMixin


class Win(PickViewMixin, LibraryMixin, RetroMixin, NotesMixin, AchievementsMixin, SettingsMixin, Adw.ApplicationWindow):

    def __init__(self, app, do_import=True):
        super().__init__(application=app, title="Ludodex", default_width=1000, default_height=720)
        _install_css()
        self.con = P.db()
        moved = P.import_all(self.con) if do_import else []  # met aussi à jour temps de jeu, sessions et rangement
        self.pool, self.current = [], None
        self.pool_inst, self.pool_owned, self.pick_stage = [], [], None
        self.mood_selected, self._mood_opts = set(), None
        self.show_hidden = P.get_setting(self.con, "show_hidden") == "1"  # réglage mémorisé
        self.show_not_installed = P.shows_not_installed(self.con)  # jeux possédés non installés
        self.pick_owned = P.pick_owned(self.con)  # tirage : prendre en compte les non installés
        self.hide_short_min = P.hide_short_min(self.con)  # frise : seuil (min) sous lequel une session est masquée
        self.mood_mode = P.get_setting(self.con, "mood_match") or "any"  # tags : « any » (OU) ou « all » (ET)
        self.exclude_selected = P.excluded_tags(self.con)  # tags écartés du tirage (réglage mémorisé)

        stack = Adw.ViewStack()
        self.stack = stack
        self._page_meta = {"pick": (tr("Pick"), "media-playlist-shuffle-symbolic"),
                           "lib": (tr("Library"), "view-grid-symbolic"),
                           "retro": (tr("Retrospective"), "document-open-recent-symbolic"),
                           "notes": (tr("Notes"), "document-edit-symbolic"),
                           "achievements": (tr("Achievements"), "emblem-ok-symbolic")}
        self.lib_sort = P.get_setting(self.con, "lib_sort")
        if self.lib_sort not in ("name", "recent", "played"):
            self.lib_sort = "name"
        self.lib_filter = "all"
        self.col_selected, self.col_game_ids, self._building_cols = set(), set(), False
        self._tiles, self._cover_queue, self._tex_cache, self._lib_gen = [], [], {}, 0
        # toutes les pages sont construites (leur état sert ailleurs) ; seuls les modules
        # activés sont ajoutés à la barre
        self._pages = {"pick": self._build_pick(), "lib": self._build_library_page(),
                       "retro": self._build_retro_page(), "notes": self._build_notes_page(),
                       "achievements": self._build_achievements_page()}
        self._sync_modules()
        stack.connect("notify::visible-child-name", self._on_page_changed)
        self._build_lib()

        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text=tr("Import games"))
        refresh.connect("clicked", self._on_import)
        header = Adw.HeaderBar(title_widget=Adw.ViewSwitcher(stack=stack, policy=Adw.ViewSwitcherPolicy.WIDE))
        header.pack_start(refresh)
        add_btn = Gtk.Button(icon_name="list-add-symbolic", tooltip_text=tr("Add a game"))
        add_btn.connect("clicked", self._open_add_game)
        header.pack_start(add_btn)
        settings = Gtk.Button(icon_name="preferences-system-symbolic", tooltip_text=tr("Settings"))
        settings.connect("clicked", self._open_settings)
        header.pack_end(settings)
        self.sort_btn = Gtk.MenuButton(icon_name="view-sort-descending-symbolic", tooltip_text=tr("Sort and filter"),
                                       menu_model=self._build_sort_menu())
        self.search_btn = Gtk.ToggleButton(icon_name="system-search-symbolic", tooltip_text=tr("Search"))
        self.search_btn.bind_property("active", self.search_bar, "search-mode-enabled",
                                      GObject.BindingFlags.BIDIRECTIONAL)
        header.pack_end(self.sort_btn)
        header.pack_end(self.search_btn)
        self.lib_widgets = [add_btn, self.sort_btn, self.search_btn]  # boutons propres à la bibliothèque
        self._on_page_changed()
        self._settings_win = None

        self.toasts = Adw.ToastOverlay(child=stack)
        view = Adw.ToolbarView(content=self.toasts)
        view.add_top_bar(header)
        self.set_content(view)
        shortcuts = Gtk.ShortcutController()
        shortcuts.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("<Control>f"),
                                                Gtk.CallbackAction.new(self._toggle_search)))
        self.add_controller(shortcuts)
        self._fetch_tags_bg()
        self._fetch_covers_bg()
        self.connect("notify::is-active", lambda w, _p: w.is_active() and self._refresh_stats())
        self.connect("close-request", self._on_close)
        if moved:
            self.toasts.add_toast(Adw.Toast.new(tr("{n} inactive game(s) set to paused", n=len(moved))))

    def _add_page(self, name):
        title, icon = self._page_meta[name]
        self.stack.add_titled_with_icon(self._pages[name], name, title, icon)

    def _sync_modules(self):
        """N'affiche que les modules activés (bibliothèque en tête, puis choix, rétrospective,
        notes, succès), en conservant la page courante si elle est toujours disponible.

        Reconstruction incrémentale : on ne touche qu'aux pages qui changent. Tout
        retirer puis tout ré-ajouter déclenche un crash libadwaita (l'animation de
        page « skip » sur un widget détruit)."""
        keep = self.stack.get_visible_child_name()
        want = ["lib"] + [n for n in ("pick", "retro", "notes", "achievements")
                          if P.module_enabled(self.con, n)]
        for name in ("pick", "retro", "notes", "achievements"):
            child = self.stack.get_child_by_name(name)
            if name not in want and child is not None:
                self.stack.remove(child)
        for name in want:
            if self.stack.get_child_by_name(name) is None:
                self._add_page(name)
        self.stack.set_visible_child_name(keep if keep and self.stack.get_child_by_name(keep) else "lib")
        self._on_page_changed()

    def _on_close(self, *_):
        P.close_open_sessions(self.con)  # fin de session = durée enregistrée
        self._flush_note()               # une note en cours d'édition est enregistrée
        return False


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id="local.ludodex")

    def do_activate(self):
        win = self.props.active_window or Win(self)
        win.present()


if __name__ == "__main__":
    sys.exit(App().run(sys.argv))
