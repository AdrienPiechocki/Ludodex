"""achievements.py — page « Achievements » : succès définis par l'utilisateur (YAML),
détectés dans les logs d'un jeu moddé (lecture incrémentale).

Chaque jeu peut être relié à un fichier de succès (YAML) et au fichier de log du
mod : la page liste les succès avec leur état (verrouillé / progression x/N /
débloqué avec date), un bouton « Rescan » relit les logs, et un dialogue permet
d'attacher, modifier ou retirer un suivi pour un jeu.
"""
import datetime

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

import picker as P
from i18n import tr

_TROPHY = "🏆"


def pick_file(fd, parent, kind, on_picked):
    """Sélecteur de fichier GTK >= 4.10 : filtre YAML pour les succès, puis
    `on_picked(chemin)` (asynchrone, fichier choisi par l'utilisateur).

    `parent` doit être la fenêtre (`Gtk.Window`) : `Gtk.FileDialog.open()` refuse
    un `Adw.Dialog`. Séparé du dialogue pour être testable : avec un faux `fd`
    (duck-typing), un appel à une méthode inexistante lève immédiatement
    (ex. `select_file`)."""
    if kind == "yaml":
        flt = Gtk.FileFilter()
        flt.set_name("YAML")
        flt.add_pattern("*.yaml")
        flt.add_pattern("*.yml")
        fd.set_default_filter(flt)

    def cb(fdr, res):
        try:
            f = fdr.open_finish(res)
        except GLib.Error:
            return
        on_picked(f.get_path())

    fd.open(parent, None, cb)


class AchievementsMixin:

    # ---------- page « Achievements » : liste des jeux à gauche, succès à droite ----------
    def _build_achievements_page(self):
        self.ach_game_id = None       # jeu sélectionné dans la liste de gauche
        self._ach_filling = False     # reconstruction de la liste : on ignore la sélection
        self._ach_entries = {}        # gid → {ach_id → état} (lu par les tests / rafraîchissement)
        self._ach_rows = {}           # nom du succès → état (ligne construite)
        self._ach_group = None        # Gtk.ListBox courante (contient les Adw.ActionRow)
        self._tracker_dlg = None

        # --- volet gauche : liste des jeux suivis (comme la page Notes) ---
        head = Gtk.Box(spacing=6, margin_top=8, margin_bottom=6, margin_start=12, margin_end=6)
        head.append(Gtk.Label(label=tr("Achievements"), xalign=0, hexpand=True,
                              css_classes=["heading"]))
        add = Gtk.Button(icon_name="list-add-symbolic", tooltip_text=tr("Set up a tracker"),
                         valign=Gtk.Align.CENTER)
        add.connect("clicked", lambda *_: self._open_tracker_dialog())
        head.append(add)
        self.ach_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE,
                                    css_classes=["navigation-sidebar"])
        self.ach_list.connect("row-selected", self._on_ach_game_selected)
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        left.append(head)
        left.append(Gtk.ScrolledWindow(child=self.ach_list, vexpand=True,
                                       hscrollbar_policy=Gtk.PolicyType.NEVER))

        # --- volet droit : succès du jeu sélectionné ---
        self.ach_title = Gtk.Label(xalign=0, hexpand=True, css_classes=["title-2"])
        self.ach_subtitle = Gtk.Label(xalign=0, css_classes=["dim-label"], visible=False)
        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        title_box.append(self.ach_title)
        title_box.append(self.ach_subtitle)
        self.ach_rescan = Gtk.Button(icon_name="view-refresh-symbolic",
                                     tooltip_text=tr("Scan game logs"), valign=Gtk.Align.CENTER)
        self.ach_rescan.connect("clicked", lambda *_: self._refresh_achievements())
        self.ach_gear = Gtk.Button(icon_name="preferences-system-symbolic", css_classes=["flat"],
                                   tooltip_text=tr("Tracker settings"), valign=Gtk.Align.CENTER)
        self.ach_gear.connect("clicked", lambda *_: self._open_tracker_dialog(self.ach_game_id))
        head_r = Gtk.Box(spacing=6, margin_top=8, margin_bottom=6, margin_start=12, margin_end=6)
        head_r.append(title_box)
        head_r.append(self.ach_rescan)
        head_r.append(self.ach_gear)
        # Les Adw.ActionRow doivent vivre dans un Gtk.ListBox (posées dans un Gtk.Box
        # direct, libadwaita émet un avertissement et peut faire planter GTK).
        self.achievements_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                        margin_top=6, margin_bottom=12)
        scroll_r = Gtk.ScrolledWindow(child=self.achievements_box, vexpand=True,
                                      hscrollbar_policy=Gtk.PolicyType.NEVER)
        detail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        detail.append(head_r)
        detail.append(scroll_r)

        setup_btn = Gtk.Button(label=tr("Set up a tracker"), halign=Gtk.Align.CENTER,
                               css_classes=["pill", "suggested-action"])
        setup_btn.connect("clicked", lambda *_: self._open_tracker_dialog())
        self.ach_empty = Adw.StatusPage(icon_name="emblem-ok-symbolic", vexpand=True,
                                        title=tr("Achievement tracker"),
                                        description=tr("Attach a YAML file of achievements and a game "
                                                       "log file to follow them."))
        self.ach_empty.set_child(setup_btn)
        self.ach_right = Gtk.Stack()
        self.ach_right.add_named(self.ach_empty, "empty")
        self.ach_right.add_named(detail, "detail")
        self.ach_right.set_visible_child_name("empty")

        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.set_start_child(left)
        paned.set_end_child(self.ach_right)
        paned.set_resize_start_child(False)
        paned.set_shrink_start_child(False)
        paned.set_position(260)

        self._refresh_achievements()
        return paned

    def _refresh_achievements(self):
        """Reconstruit la liste des jeux suivis et le détail du jeu sélectionné."""
        self._fill_ach_list()
        if self.ach_game_id is not None:
            self._show_ach_game(self.ach_game_id)
        else:
            self._ach_entries = {}
            self._ach_rows = {}
            self.ach_right.set_visible_child_name("empty")

    def _fill_ach_list(self):
        """Remplit la liste de gauche ; la sélection courante est conservée si possible."""
        self._ach_filling = True
        try:
            while (child := self.ach_list.get_first_child()) is not None:
                self.ach_list.remove(child)
            self._ach_row_by_gid = {}
            tracked = P.tracked_games(self.con)
            for gid, name, _yaml, _log in tracked:
                row = Adw.ActionRow(title=GLib.markup_escape_text(name))
                row._gid = gid
                self.ach_list.append(row)
                self._ach_row_by_gid[gid] = row
            if self.ach_game_id not in self._ach_row_by_gid:
                # premier jeu (ou sélection disparue) : on montre le premier de la liste
                self.ach_game_id = tracked[0][0] if tracked else None
            row = self._ach_row_by_gid.get(self.ach_game_id)
            if row is not None:
                self.ach_list.select_row(row)
        finally:
            self._ach_filling = False

    def _on_ach_game_selected(self, _lb, row):
        if row is None or self._ach_filling:
            return
        gid = getattr(row, "_gid", None)
        if gid is None or gid == self.ach_game_id:
            return
        self._show_ach_game(gid)

    def _show_ach_game(self, gid):
        """Scanne le log du jeu et remplit le volet droit avec ses succès."""
        self.ach_game_id = gid
        g = self.con.execute("SELECT name FROM games WHERE id=?", (gid,)).fetchone()
        name = g["name"] if g else ""
        self.ach_title.set_text(name)
        while (child := self.achievements_box.get_first_child()) is not None:
            self.achievements_box.remove(child)
        self._ach_rows = {}
        self._ach_entries = {}
        group = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"],
                            margin_start=12, margin_end=12)
        self._ach_group = group
        self.achievements_box.append(group)
        self.ach_subtitle.set_visible(False)
        try:
            state = P.scan(self.con, gid)
        except FileNotFoundError as e:
            self._add_game_error(name, tr("log file missing: {path}", path=e.filename))
            self.ach_right.set_visible_child_name("detail")
            return
        except ValueError as e:
            self._add_game_error(name, str(e))
            self.ach_right.set_visible_child_name("detail")
            return
        done = sum(1 for a in state if a["done"])
        self.ach_subtitle.set_text(tr("{done}/{total} unlocked", done=done, total=len(state)))
        self.ach_subtitle.set_visible(True)
        self._ach_entries[gid] = {a["id"]: a for a in state}
        for a in state:
            self._append_achievement_row(a)
        self.ach_right.set_visible_child_name("detail")

    def _add_game_error(self, name, message):
        row = Adw.ActionRow(title=GLib.markup_escape_text(name),
                            subtitle=GLib.markup_escape_text(message))
        row.set_activatable(False)
        row.add_suffix(Gtk.Image(icon_name="dialog-warning-symbolic"))
        self._ach_group.append(row)

    def _append_achievement_row(self, a):
        """Une ligne par succès : icône, nom, description/progression, état."""
        row = Adw.ActionRow(title=GLib.markup_escape_text(a["name"]))
        row.set_activatable(False)
        lead = Gtk.Label(label=a["icon"] or _TROPHY,
                         css_classes=["accent"] if a["done"] else ["dim-label"])
        row.add_prefix(lead)
        if a["description"]:
            row.set_subtitle(GLib.markup_escape_text(a["description"]))
        elif not a["done"] and a["target"] > 1:
            row.set_subtitle(f"{a['progress']}/{a['target']}")
        if a["done"]:
            date = datetime.datetime.fromtimestamp(a["unlocked_at"]).strftime("%d/%m/%Y")
            row.add_suffix(Gtk.Label(label=tr("Unlocked {date}", date=date),
                                     css_classes=["accent"]))
        elif a["target"] > 1:
            row.add_suffix(Gtk.Label(label=f"{a['progress']}/{a['target']}",
                                     css_classes=["dim-label"]))
        self._ach_group.append(row)
        self._ach_rows[a["name"]] = a

    # ---------- dialogue de configuration ----------
    def _open_tracker_dialog(self, gid=None):
        if self._tracker_dlg is not None:
            self._tracker_dlg.present(self)
            return

        games = self.con.execute(
            f"SELECT id, name FROM games {self._lib_where(trailing=True)} ORDER BY name COLLATE NOCASE"
        ).fetchall()
        dlg = Adw.Dialog(title=tr("Set up a tracker"), content_width=620, content_height=420)
        self._tracker_dlg = dlg
        dlg._games = games
        w = self._tracker_dialog_body(dlg, games, gid)
        dlg.set_child(w)

        def on_key(_c, keyval, *_a):
            if keyval == 0xff1b:  # Escape
                dlg.close()
                return True
            return False
        key = Gtk.EventControllerKey()
        key.connect("key-pressed", on_key)
        dlg.add_controller(key)
        dlg.connect("closed", lambda *_: setattr(self, "_tracker_dlg", None))
        dlg.present(self)

    def _tracker_dialog_body(self, dlg, games, gid):
        grid = Gtk.Grid(row_spacing=10, column_spacing=14, margin_top=16,
                        margin_bottom=24, margin_start=24, margin_end=24)
        row = [0]

        def label(text):
            grid.attach(Gtk.Label(label=tr(text), xalign=0), 0, row[0], 1, 1)
            row[0] += 1

        # --- jeu : liste déroulante (présélectionnée si gid fourni) ---
        label("Game")
        store = Gtk.StringList()
        for g in games:
            store.append(g["name"])
        drop = Gtk.DropDown(model=store, hexpand=True, valign=Gtk.Align.CENTER)
        if gid is not None:
            for i, g in enumerate(games):
                if g["id"] == gid:
                    drop.set_selected(i)
                    break
        grid.attach(drop, 1, row[0] - 1, 2, 1)

        # --- fichiers : YAML des succès + log du jeu ---
        label("Achievements file (YAML)")
        yaml_text = Gtk.Label(xalign=0, hexpand=True, wrap=True, css_classes=["dim-label"])
        log_text = Gtk.Label(xalign=0, hexpand=True, wrap=True, css_classes=["dim-label"])
        yaml_btn = Gtk.Button(label=tr("Choose…"))
        log_btn = Gtk.Button(label=tr("Choose…"))
        ybox = Gtk.Box(spacing=8)
        ybox.append(yaml_btn)
        ybox.append(yaml_text)
        grid.attach(ybox, 1, row[0] - 1, 2, 1)
        label("Log file")
        lbox = Gtk.Box(spacing=8)
        lbox.append(log_btn)
        lbox.append(log_text)
        grid.attach(lbox, 1, row[0] - 1, 2, 1)

        paths = {"yaml": "", "log": ""}
        if gid is not None:
            cur = P.tracker_paths(self.con, gid)
            if cur:
                paths["yaml"], paths["log"] = cur
                yaml_text.set_text(cur[0])
                log_text.set_text(cur[1])

        def pick(button, kind, fd_factory=None):
            if fd_factory is None:
                fd_factory = Gtk.FileDialog if hasattr(Gtk, "FileDialog") else None

            def on_picked(path):
                paths[kind] = path
                (yaml_text if kind == "yaml" else log_text).set_text(path)

            if fd_factory is not None:  # GTK >= 4.10
                pick_file(fd_factory(), self, kind, on_picked)
            else:  # GTK < 4.10 : sélecteur natif
                fc = Gtk.FileChooserNative(title=tr("Choose a file"),
                                           action=Gtk.FileChooserAction.OPEN,
                                           transient_for=self)
                if kind == "yaml":
                    flt = Gtk.FileFilter()
                    flt.add_pattern("*.yaml")
                    flt.add_pattern("*.yml")
                    fc.add_filter(flt)
                if fc.run() == Gtk.ResponseType.ACCEPT:
                    paths[kind] = fc.get_file().get_path()
                    (yaml_text if kind == "yaml" else log_text).set_text(fc.get_file().get_path())
                fc.destroy()

        yaml_btn.connect("clicked", lambda b: pick(b, "yaml"))
        log_btn.connect("clicked", lambda b: pick(b, "log"))
        dlg._pick = pick   # référence pour les tests (appel direct → exceptions remontent)

        # --- actions ---
        save = Gtk.Button(label=tr("Save"), css_classes=["suggested-action"])
        grid.attach(save, 0, row[0], 1, 1)
        remove = Gtk.Button(label=tr("Remove tracker"))
        grid.attach(remove, 1, row[0], 1, 1)

        def on_save(_b):
            g = games[drop.get_selected()]
            if not paths["yaml"] or not paths["log"]:
                self.toasts.add_toast(Adw.Toast.new(
                    tr("Choose a YAML file and a log file first.")))
                return
            P.set_tracker(self.con, g["id"], paths["yaml"], paths["log"])
            dlg.close()
            self._refresh_achievements()

        def on_remove(_b):
            if gid is not None:
                P.clear_tracker(self.con, gid)
            dlg.close()
            self._refresh_achievements()

        save.connect("clicked", on_save)
        remove.connect("clicked", on_remove)

        scroll = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroll.set_child(grid)
        view = Adw.ToolbarView(content=scroll)
        view.add_top_bar(Adw.HeaderBar())
        return view