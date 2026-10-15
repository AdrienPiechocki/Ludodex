"""main.py — fenêtre principale (Win) et application (App)."""
import sys

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango

import picker as P
from i18n import available, get_lang, tr
from .theme import _install_css
from .pickview import PickViewMixin
from .library import LibraryMixin
from .retrospec import RetroMixin
from .notes import NotesMixin
from .achievements import AchievementsMixin
from .settingsui import SettingsMixin

MODULES = ("pick", "retro", "notes", "achievements")  # ordre CANONIQUE de la barre de navigation


class Win(PickViewMixin, LibraryMixin, RetroMixin, NotesMixin, AchievementsMixin, SettingsMixin, Adw.ApplicationWindow):

    def __init__(self, app, do_import=True):
        super().__init__(application=app, title="Ludodex", default_width=1000, default_height=720)
        _install_css()
        self.con = P.db()
        moved = P.import_all(self.con) if do_import else []  # met aussi à jour temps de jeu, sessions et rangement
        self.pool, self.current, self._tires = [], None, set()
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
        # regroupement en trois paliers (installés > non installés > masqués) :
        # coché par défaut — seule une valeur explicite « 0 » le désactive
        self.lib_group = P.get_setting(self.con, "lib_group") != "0"
        self.col_selected, self.col_game_ids, self._building_cols = set(), set(), False
        self._tiles, self._cover_queue, self._tex_cache, self._lib_gen = [], [], {}, 0
        # toutes les pages sont construites (leur état sert ailleurs) ; seuls les modules
        # activés sont ajoutés à la barre
        self._pages = {"pick": self._build_pick(), "lib": self._build_library_page(),
                       "retro": self._build_retro_page(), "notes": self._build_notes_page(),
                       "achievements": self._build_achievements_page()}
        # le sélecteur doit exister avant _sync_modules : il se détache et se rebranche
        # à chaque reconstruction de la barre
        self._switcher = Adw.ViewSwitcher(stack=stack, policy=Adw.ViewSwitcherPolicy.WIDE)
        self._sync_modules()
        stack.connect("notify::visible-child-name", self._on_page_changed)
        self._build_lib()

        # L'en-tête ne garde que l'import, les onglets et les réglages : les
        # commandes de la bibliothèque (recherche, tri, filtre, non-installés,
        # ajout) vivent dans la barre au-dessus des tuiles, sur la page elle-même
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text=tr("Import games"))
        refresh.connect("clicked", self._on_import)
        header = Adw.HeaderBar(title_widget=self._switcher)
        header.pack_start(refresh)
        settings = Gtk.Button(icon_name="preferences-system-symbolic", tooltip_text=tr("Settings"))
        settings.connect("clicked", self._open_settings)
        header.pack_end(settings)
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
        """N'affiche que les modules activés, TOUJOURS dans l'ordre canonique
        (bibliothèque en tête, puis choix, rétrospective, notes, succès), en
        conservant la page courante si elle est toujours disponible.

        Un `Adw.ViewStack` ne sait qu'APPENDRE ses pages : sans reconstruction,
        un module ré-activé revient systématiquement en DERNIER de la barre de
        navigation, et chaque aller-retour le fait glisser d'un cran de plus.
        Insérer à une position n'est pas possible ici : PyGObject n'expose pas
        `adw_view_stack_insert_before` (écrasée par `GtkWidget.insert_before`,
        qui ne fait rien de tel) et `AdwViewStackPage:visible` n'est pas
        honoré par le sélecteur. D'où la reconstruction complète — MAIS en
        détachant d'abord le `Adw.ViewSwitcher` : laisser un sélecteur branché
        pendant que son modèle de pages se vide plante libadwaita (SIGSEGV)
        dans environ six cas sur dix, d'où l'implémentation incrémentale de
        départ."""
        want = ["lib"] + [n for n in MODULES if P.module_enabled(self.con, n)]
        pages = self.stack.get_pages()
        actuel = [pages.get_item(i).get_name() for i in range(pages.get_n_items())]
        if actuel == want:
            return                      # rien n'a bougé : pas de reconstruction inutile
        keep = self.stack.get_visible_child_name()
        self._switcher.set_stack(None)  # le sélecteur lâche le stack pendant la reconstruction
        for name in actuel:
            self.stack.remove(self.stack.get_child_by_name(name))
        for name in want:
            self._add_page(name)
        self.stack.set_visible_child_name(keep if keep in want else "lib")
        self._switcher.set_stack(self.stack)
        self._on_page_changed()

    def _on_close(self, *_):
        P.close_open_sessions(self.con)  # fin de session = durée enregistrée
        self._flush_note()               # une note en cours d'édition est enregistrée
        return False

    def _on_game_exit(self, sid):
        """Le processus d'un jeu que nous avons lancé vient de se terminer : sa
        session se clôt ici, sur le FIL PRINCIPAL — le fil d'attente (un autre
        fil) ne touche jamais à la connexion, et la session a pu être fermée
        entre-temps (fermeture de l'application, lancement suivant) : le cœur
        s'en charge, `close_session` est sans effet dans ce cas."""
        GLib.idle_add(lambda: (P.close_session(self.con, sid), False)[1])

    def _present(self):
        """Présente la fenêtre — maximisée. Démarrage comme reconstruction après
        changement de langue : deux chemins, une seule façon d'ouvrir."""
        self.present()
        self.maximize()


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id="local.ludodex")

    def do_activate(self):
        if (ancienne := self.props.active_window) is None:
            Win(self)._present()   # démarrage : ouverture maximisée
        else:
            ancienne.present()     # réactivation d'une instance déjà ouverte : on ne rebascule pas


if __name__ == "__main__":
    sys.exit(App().run(sys.argv))
