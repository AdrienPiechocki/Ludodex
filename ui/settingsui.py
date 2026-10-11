"""settingsui.py — fenêtre de réglages et récupération des couvertures SteamGridDB."""
import threading

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, GObject, Gtk, Pango

import picker as P
from i18n import available, get_lang, tr


class SettingsMixin:

    def _open_settings(self, *_):
        # Les réglages propres à un module n'apparaissent que sur la page de ce module ;
        # les réglages généraux (langue, modules, plateformes, couvertures) restent visibles partout.
        scope = self.stack.get_visible_child_name() or "lib"
        if self._settings_win:
            if getattr(self, "_settings_scope", None) == scope:
                return self._settings_win.present(self)
            self._settings_win.close()  # la page active a changé : on reconstruit les bons réglages
            self._settings_win = None
        self._settings_scope = scope
        self._module_sw = self._import_sw = self._auto_rows = None
        self._hide_short_scale = self._hide_short_row = None
        self._lib_group = self._auto_group = self._retro_group = self._pick_group = None
        self._pick_owned_chk = None
        self._show_hidden_chk = self._show_not_installed_chk = None
        page = Adw.PreferencesPage()

        # --- réglages généraux (toutes pages) ---
        lang_group = Adw.PreferencesGroup()
        langs = available()  # une entrée par fichier lang/*.json, nommée dans sa propre langue
        lang_row = Adw.ComboRow(title=tr("Language"), model=Gtk.StringList.new([n for _, n in langs]),
                                selected=[c for c, _ in langs].index(get_lang()))
        lang_row.connect("notify::selected", self._on_lang)  # connecté après le choix initial : pas de faux déclenchement
        lang_group.add(lang_row)
        page.add(lang_group)
        mod_group = Adw.PreferencesGroup(
            title=tr("Modules"),
            description=tr("Hide a screen from the main window. Nothing is deleted."))
        self._module_sw = {}
        for name, label in (("pick", "Pick"), ("retro", "Retrospective"), ("notes", "Notes")):
            sw = Adw.SwitchRow(title=tr(label), active=P.module_enabled(self.con, name))
            sw.connect("notify::active", self._on_module, name)
            mod_group.add(sw)
            self._module_sw[name] = sw
        page.add(mod_group)

        # --- réglages du module actif ---
        if scope == "lib":
            self._lib_group = lib_group = Adw.PreferencesGroup(title=tr("Library"))
            chk = Gtk.CheckButton(active=self.show_hidden, valign=Gtk.Align.CENTER)
            chk.connect("toggled", self._on_show_hidden)
            chk_row = Adw.ActionRow(title=tr("Show hidden games"), activatable_widget=chk)
            chk_row.add_prefix(chk)
            lib_group.add(chk_row)
            self._show_hidden_chk = chk  # référence pour les tests
            nchk = Gtk.CheckButton(active=self.show_not_installed, valign=Gtk.Align.CENTER)
            nchk.connect("toggled", self._on_show_not_installed)
            nrow = Adw.ActionRow(title=tr("Show not installed games"), activatable_widget=nchk)
            nrow.add_prefix(nchk)
            lib_group.add(nrow)
            self._show_not_installed_chk = nchk  # référence pour les tests
            page.add(lib_group)
            self._auto_group = auto_group = Adw.PreferencesGroup(title=tr("Automatic collections"))
            self._auto_rows = {}
            for src, label in P.AUTO_SOURCES:
                sw = Adw.SwitchRow(title=tr(label), active=P.auto_collection_enabled(self.con, src))
                sw.connect("notify::active", self._on_auto_collection, src)
                auto_group.add(sw)
                self._auto_rows[src] = sw
                if src != "custom" and not P.source_enabled(self.con, src):
                    sw.set_visible(False)  # plateforme désactivée → sa collection auto disparaît aussi
            page.add(auto_group)
        elif scope == "retro":
            self._retro_group = retro_group = Adw.PreferencesGroup(title=tr("Retrospective"))
            rscale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, digits=0, draw_value=True,
                               value_pos=Gtk.PositionType.RIGHT, valign=Gtk.Align.CENTER, width_request=180)
            rscale.set_round_digits(0)
            rscale.set_adjustment(Gtk.Adjustment(lower=0, upper=15, step_increment=1, page_increment=1))
            rscale.set_value(self.hide_short_min)
            rrow = Adw.ActionRow(title=self._hide_short_label(self.hide_short_min),
                                 subtitle=tr("In the weekly timeline"))
            rrow.add_suffix(rscale)

            def _on_scale(scale, row=rrow):
                v = int(scale.get_value())
                row.set_title(self._hide_short_label(v))
                self._on_hide_short(v)

            rscale.connect("value-changed", _on_scale)
            retro_group.add(rrow)
            page.add(retro_group)
            self._hide_short_scale = rscale  # références pour les tests
            self._hide_short_row = rrow
        elif scope == "pick":
            self._pick_group = pick_group = Adw.PreferencesGroup(title=tr("Pick"))
            pchk = Gtk.CheckButton(active=self.pick_owned, valign=Gtk.Align.CENTER)
            pchk.connect("toggled", self._on_pick_owned)
            prow = Adw.ActionRow(title=tr("Take not installed games into account"),
                                 subtitle=tr("In the draw and its tags"),
                                 activatable_widget=pchk)
            prow.add_prefix(pchk)
            pick_group.add(prow)
            page.add(pick_group)
            self._pick_owned_chk = pchk  # référence pour les tests
        elif scope == "notes":
            notes_group = Adw.PreferencesGroup(title=tr("Notes"))
            spin = Adw.SpinRow.new_with_range(8, 40, 1)
            spin.set_title(tr("Text size"))
            spin.set_subtitle(tr("In points"))
            spin.set_value(self.notes_font_size)
            spin.connect("notify::value", self._on_notes_font)  # connecté après la valeur initiale
            notes_group.add(spin)
            page.add(notes_group)
            self._notes_group = notes_group   # référence pour les tests
            self._notes_font_spin = spin

        # --- suite des réglages généraux ---
        imp_group = Adw.PreferencesGroup(
            title=tr("Platforms to import"),
            description=tr("Uncheck a platform to hide its games everywhere; nothing is deleted."))
        self._import_sw = {}
        for src, label in P.IMPORT_SOURCES:
            sw = Adw.SwitchRow(title=tr(label), active=P.source_enabled(self.con, src))
            sw.connect("notify::active", self._on_import_source, src)
            imp_group.add(sw)
            self._import_sw[src] = sw
        page.add(imp_group)
        group = Adw.PreferencesGroup(
            title=tr("Covers (SteamGridDB)"),
            description=tr("Generate the API key in your account preferences on steamgriddb.com."))
        self.key_row = Adw.PasswordEntryRow(title=tr("API key"), text=P.sgdb_key(), show_apply_button=True)
        self.key_row.connect("apply", self._on_key)
        group.add(self.key_row)
        self.cover_row = Adw.ActionRow(title=tr("Covers"), subtitle=self._covers_summary())
        self.cover_btn = Gtk.Button(label=tr("Fetch"), valign=Gtk.Align.CENTER, css_classes=["suggested-action"])
        self.cover_btn.connect("clicked", self._on_covers)
        self.cover_row.add_suffix(self.cover_btn)
        group.add(self.cover_row)
        page.add(group)

        win = Adw.PreferencesDialog(search_enabled=False, content_width=440, content_height=460)
        win.add(page)
        win.connect("closed", self._on_settings_closed)
        self._settings_win = win
        win.present(self)

    def _on_lang(self, row, _p):
        code = available()[row.get_selected()][0]
        if code == get_lang():
            return
        P.save_lang(self.con, code)
        GLib.idle_add(self._rebuild)

    def _on_module(self, sw, _p, name):
        P.set_setting(self.con, f"module_{name}", "1" if sw.get_active() else "0")
        self._sync_modules()

    def _on_notes_font(self, spin, _p):
        v = int(spin.get_value())
        P.set_setting(self.con, "notes_font_size", str(v))
        self._apply_note_font(v)

    def _rebuild(self):
        """Les textes sont posés à la construction : on rouvre la fenêtre dans la nouvelle langue."""
        from .main import Win  # import tardif : évite le cycle main <-> settingsui
        app = self.get_application()
        if self._settings_win:
            self._settings_win.close()
        new = Win(app, do_import=False)  # pas de nouvel import : rien n'a changé dans la bibliothèque
        new.present()
        new._open_settings()
        self.close()
        return False

    def _on_settings_closed(self, *_):
        self._settings_win = None
        return False

    def _toast_settings(self, msg):
        (self._settings_win or self.toasts).add_toast(Adw.Toast.new(GLib.markup_escape_text(msg)))

    def _on_key(self, row):
        P.save_sgdb_key(row.get_text())
        self._toast_settings(tr("Key saved") if row.get_text().strip() else tr("Key cleared"))

    def _on_covers(self, *_):
        if not P.sgdb_key():
            return self._toast_settings(tr("Enter the API key first (then confirm with ✓)"))
        if getattr(self, "_covering", False):
            return self._toast_settings(tr("Already fetching"))
        self._covering = True
        self.cover_btn.set_sensitive(False)
        self.cover_row.set_subtitle(tr("Fetching…"))
        errors = []

        def progress(msg):
            if msg.startswith("!"):
                errors.append(msg.lstrip("! "))

        def work():
            n = 0
            try:
                con = P.db()
                con.execute("UPDATE games SET cover_fetched=0 WHERE cover_path=''")  # relance manuelle : on retente tout
                con.commit()
                n = P.fetch_missing_covers(con, limit=500, progress=progress)
                con.close()
            except Exception as e:
                errors.append(str(e))
            finally:
                self._covering = False
            GLib.idle_add(self._covers_done, n, errors)
        threading.Thread(target=work, daemon=True).start()

    def _covers_done(self, n, errors):
        if self._settings_win:
            self.cover_btn.set_sensitive(True)
            self.cover_row.set_subtitle(self._covers_summary())
        self._toast_settings(tr("{n} cover(s) fetched", n=n) + (f" · {errors[0]}" if errors else ""))
        if n:
            self._build_lib()
        self._refresh_current_cover()
        return False

    # ---------- réglages : SteamGridDB ----------
    def _covers_summary(self):
        # Même filtre que la grille : le compte suit les jeux réellement affichés
        # (masqués, non installés et plateformes désactivées compris).
        have, total = self.con.execute(
            f"SELECT COALESCE(SUM(cover_path != '' OR custom_cover != ''), 0), COUNT(*) "
            f"FROM games {self._lib_where()}").fetchone()
        return tr("{have}/{total} games have a cover", have=have, total=total)

    def _refresh_covers_summary(self):
        """Le compteur suit les jeux affichés : on le rafraîchit quand un filtre d'affichage change."""
        if self._settings_win and getattr(self, "cover_row", None):
            self.cover_row.set_subtitle(self._covers_summary())

    def _on_sgdb_id(self, gid, row):
        txt = row.get_text().strip()
        if not txt.isdigit():
            return self.toasts.add_toast(Adw.Toast.new(tr("Invalid ID: a number, visible in the game's URL on steamgriddb.com")))
        if not P.sgdb_key():
            return self.toasts.add_toast(Adw.Toast.new(tr("Enter the API key first (⚙ at the top)")))
        self._upd(gid, "sgdb_id", int(txt))

        def work():
            try:
                con = P.db()
                ok = P.fetch_cover(con, con.execute("SELECT * FROM games WHERE id=?", (gid,)).fetchone())
                con.close()
                msg = tr("Cover fetched") if ok else tr("No cover for this ID")
            except Exception as e:
                msg = tr("Failed: {e}", e=e)
            GLib.idle_add(self._cover_one_done, msg)
        threading.Thread(target=work, daemon=True).start()

    def _cover_one_done(self, msg):
        self.toasts.add_toast(Adw.Toast.new(GLib.markup_escape_text(msg)))
        self._refresh_current_cover()
        return False

    def _fetch_covers_bg(self, include_not_installed=False):
        if not P.sgdb_key() or getattr(self, "_covering", False):
            return
        self._covering = True

        def work():
            n = 0
            try:
                con = P.db()
                n = P.fetch_missing_covers(con, include_not_installed=include_not_installed)
                con.close()
            finally:
                self._covering = False
            if n:
                GLib.idle_add(self._after_covers)
        threading.Thread(target=work, daemon=True).start()

    def _after_covers(self):
        self._build_lib()
        self._refresh_current_cover()
        return False
