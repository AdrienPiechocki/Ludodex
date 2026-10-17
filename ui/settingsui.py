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
        # Trois onglets fixes — Général / Modules / Connexions : le contenu ne dépend
        # plus de la page principale active, chaque réglage vit à son onglet.
        if self._settings_win:
            return self._settings_win.present(self)
        self._module_sw = self._import_sw = self._auto_rows = None
        self._hide_short_scale = self._hide_short_row = None
        self._retro_enable_sw = None
        self._lib_group = self._auto_group = self._retro_group = None
        self._show_hidden_chk = self._group_games_chk = None
        self._ach_show_hidden_chk = self._ach_show_hidden_row = None
        self._ra_user_row = self._ra_key_row = None
        self._ra_sync_row = self._ra_enabled_sw = None   # groupes réouvrables à l'identique
        self._steam_key_row = self._steam_id_row = None
        self._steam_sync_row = self._steam_enabled_sw = self._steam_sync_btn = None
        self._notes_group = self._notes_font_spin = None   # curseur réaffiché à l'identique
        self._settings_stack = self._settings_switcher = self._settings_overlay = None

        # L'ossature : trois pages empilées sous un sélecteur d'onglets porté par
        # l'en-tête (le même motif que la fenêtre principale), les toasts DANS le
        # dialogue — une notification qui se perdrait derrière ne dirait rien.
        stack = Adw.ViewStack(vexpand=True, hexpand=True)
        pages = {}
        for name, cle, icone in (("general", "General", "preferences-system-symbolic"),
                                 ("modules", "Modules", "view-grid-symbolic"),
                                 ("connections", "Connections", "network-workgroup-symbolic")):
            pg = Adw.PreferencesPage()
            stack.add_titled_with_icon(pg, name, tr(cle), icone)
            pages[name] = pg
        switcher = Adw.ViewSwitcher(stack=stack, policy=Adw.ViewSwitcherPolicy.WIDE)
        overlay = Adw.ToastOverlay(child=stack)
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar(title_widget=switcher))
        toolbar.set_content(overlay)
        self._settings_stack, self._settings_switcher, self._settings_overlay = stack, switcher, overlay

        # --- onglet Général : langue, puis plateformes à importer ---
        page = pages["general"]
        lang_group = Adw.PreferencesGroup()
        langs = available()  # une entrée par fichier lang/*.json, nommée dans sa propre langue
        lang_row = Adw.ComboRow(title=tr("Language"), model=Gtk.StringList.new([n for _, n in langs]),
                                selected=[c for c, _ in langs].index(get_lang()))
        lang_row.connect("notify::selected", self._on_lang)  # connecté après le choix initial : pas de faux déclenchement
        lang_group.add(lang_row)
        page.add(lang_group)

        # Overlay in-game : addon flatpak séparé, démarré à chaque lancement
        # de jeu (absent → le jeu se lance simplement, sans alerte).
        ov_group = Adw.PreferencesGroup()
        ov_row = Adw.SwitchRow(title=tr("In-game overlay"),
                               active=P.get_setting(self.con, "overlay", "1") == "1")
        ov_row.connect("notify::active", self._on_overlay_toggle)
        ov_group.add(ov_row)
        # Libération de la souris : réglage de Ludodex (transmis à l'overlay
        # par flag au lancement). À OFF, un FPS à souris capturée garde sa
        # souris même quand l'overlay est ouvert.
        souris_row = Adw.SwitchRow(title=tr("Release game mouse"),
                                   active=P.get_setting(self.con, "souris_libre", "1") == "1")
        souris_row.connect("notify::active", self._on_souris_libre_toggle)
        ov_group.add(souris_row)
        page.add(ov_group)

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

        # --- onglet Modules : quels écrans existent, puis le réglage de chacun ---
        page = pages["modules"]
        mod_group = Adw.PreferencesGroup(
            title=tr("Modules"),
            description=tr("Hide a screen from the main window. Nothing is deleted."))
        self._module_sw = {}
        for name, label in (("pick", "Pick"), ("retro", "Retrospective"), ("notes", "Notes"),
                            ("achievements", "Achievements")):
            sw = Adw.SwitchRow(title=tr(label), active=P.module_enabled(self.con, name))
            sw.connect("notify::active", self._on_module, name)
            mod_group.add(sw)
            self._module_sw[name] = sw
        page.add(mod_group)

        # Bibliothèque : le masquage des jeux. Les non-installés, eux, ont leur œil
        # dans la barre d'outils — un seul pilote, plus deux cases à tenir d'accord.
        self._lib_group = lib_group = Adw.PreferencesGroup(title=tr("Library"))
        chk = Gtk.CheckButton(active=self.show_hidden, valign=Gtk.Align.CENTER)
        chk.connect("toggled", self._on_show_hidden)
        chk_row = Adw.ActionRow(title=tr("Show hidden games"), activatable_widget=chk)
        chk_row.add_prefix(chk)
        lib_group.add(chk_row)
        self._show_hidden_chk = chk  # référence pour les tests
        # ordre de la grille : trois paliers — coché par défaut
        gchk = Gtk.CheckButton(active=self.lib_group, valign=Gtk.Align.CENTER)
        gchk.connect("toggled", self._on_group_games)
        grow = Adw.ActionRow(title=tr("Group games"),
                             subtitle=tr("Installed, then not installed, then hidden"),
                             activatable_widget=gchk)
        grow.add_prefix(gchk)
        lib_group.add(grow)
        self._group_games_chk = gchk  # référence pour les tests
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

        # Rétrospective : le seuil des sessions courtes, puis les frises visibles
        self._retro_group = retro_group = Adw.PreferencesGroup(title=tr("Retrospective"))
        rscale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, digits=0, draw_value=True,
                           value_pos=Gtk.PositionType.RIGHT, valign=Gtk.Align.CENTER, width_request=180)
        rscale.set_round_digits(0)
        rscale.set_adjustment(Gtk.Adjustment(lower=0, upper=15, step_increment=1, page_increment=1))
        rscale.set_value(self.hide_short_min)
        rrow = Adw.ActionRow(title=self._hide_short_label(self.hide_short_min),
                             subtitle=tr("In the timelines"))
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

        timelines = Adw.PreferencesGroup(title=tr("Timelines"))
        self._retro_enable_sw = {}
        for kind, label in (("week", "This week"), ("month", "This month"), ("year", "This year")):
            sw = Adw.SwitchRow(title=tr(label), active=P.retro_enabled(self.con, kind))
            sw.connect("notify::active", self._on_retro_enable, kind)
            timelines.add(sw)
            self._retro_enable_sw[kind] = sw
        page.add(timelines)

        # Notes : la taille du texte
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

        # Succès : le masquage des succès secrets
        ach_group = Adw.PreferencesGroup(title=tr("Achievements"))
        chk = Gtk.CheckButton(active=P.show_hidden(self.con), valign=Gtk.Align.CENTER)
        chk.connect("toggled", self._on_ach_show_hidden)
        chk_row = Adw.ActionRow(title=tr("Reveal hidden achievements"),
                                subtitle=tr("Names and descriptions shown, even while locked"),
                                activatable_widget=chk)
        chk_row.add_prefix(chk)
        ach_group.add(chk_row)
        self._ach_show_hidden_chk = chk  # référence pour les tests
        self._ach_show_hidden_row = chk_row  # référence pour les tests
        page.add(ach_group)

        # --- onglet Connexions : SteamGridDB, puis RetroAchievements et Steam ---
        page = pages["connections"]
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

        # Le compte RetroAchievements (source unique avec le dialogue de la page).
        ra_group = Adw.PreferencesGroup(
            title="RetroAchievements",
            description=tr("The key is on your account's control panel page "
                           "(retroachievements.org/controlpanel.php)."))
        # Coupé : plus aucun succès RA affiché nulle part, les jeux RA-seuls
        # quittent la liste de gauche, aucun appel réseau — et le groupe
        # s'éteint sous les yeux plutôt que de rester cliquable pour rien.
        enabled = P.ra_enabled(self.con)
        sw = Adw.SwitchRow(title=tr("Use RetroAchievements"),
                           subtitle=tr("Show and sync achievements from retroachievements.org"),
                           active=enabled)
        sw.connect("notify::active", self._on_ra_enabled)
        ra_group.add(sw)
        self._ra_enabled_sw = sw  # référence pour les tests
        user_row = Adw.EntryRow(title=tr("Username"), text=P.ra_user(self.con),
                                show_apply_button=True)
        user_row.connect("apply", self._on_ra_credentials)
        ra_group.add(user_row)
        key_row = Adw.PasswordEntryRow(title=tr("Web API key"), text=P.ra_key(self.con),
                                       show_apply_button=True)
        key_row.connect("apply", self._on_ra_credentials)
        ra_group.add(key_row)
        sync_row = Adw.ActionRow(title=tr("Link my games"),
                                 subtitle=tr("Match your played games and download "
                                             "their achievements"))
        sync_btn = Gtk.Button(label=tr("Sync account"), valign=Gtk.Align.CENTER,
                              css_classes=["suggested-action"])
        sync_btn.connect("clicked", self._on_ra_sync)
        sync_row.add_suffix(sync_btn)
        ra_group.add(sync_row)
        for w in (user_row, key_row, sync_row):
            w.set_sensitive(enabled)
        self._ra_user_row, self._ra_key_row, self._ra_sync_row = user_row, key_row, sync_row
        page.add(ra_group)

        # Même chose pour Steam : une troisième source de succès, coupable
        # d'un trait. « Steam » est une marque : comme « RetroAchievements »,
        # le titre de groupe n'est pas traduit.
        st_group = Adw.PreferencesGroup(
            title="Steam",
            description=tr("The key can be generated on steamcommunity.com/dev/apikey."))
        st_enabled = P.steam_enabled(self.con)
        st_sw = Adw.SwitchRow(title=tr("Use Steam achievements"),
                              subtitle=tr("Show and sync achievements from your Steam account"),
                              active=st_enabled)
        st_sw.connect("notify::active", self._on_steam_enabled)
        st_group.add(st_sw)
        self._steam_enabled_sw = st_sw  # référence pour les tests
        # Pas de `subtitle` sur une Adw.EntryRow : l'indication sur l'ID va
        # donc dans le titre de la ligne — une seule ligne, une seule lecture.
        st_key_row = Adw.PasswordEntryRow(title=tr("Web API key"),
                                          text=P.steam_api_key(self.con),
                                          show_apply_button=True)
        st_key_row.connect("apply", self._on_steam_credentials)
        st_group.add(st_key_row)
        st_id_row = Adw.EntryRow(title=tr("Steam ID (in your profile URL)"),
                                 text=P.steam_id64(self.con), show_apply_button=True)
        st_id_row.connect("apply", self._on_steam_credentials)
        st_group.add(st_id_row)
        st_sync_row = Adw.ActionRow(title=tr("Sync my games"),
                                    subtitle=tr("Download the achievements of your games "
                                                "on Steam"))
        st_sync_btn = Gtk.Button(label=tr("Sync account"), valign=Gtk.Align.CENTER,
                                 css_classes=["suggested-action"])
        st_sync_btn.connect("clicked", self._on_steam_sync)
        st_sync_row.add_suffix(st_sync_btn)
        st_group.add(st_sync_row)
        for w in (st_key_row, st_id_row, st_sync_row):
            w.set_sensitive(st_enabled)
        self._steam_key_row, self._steam_id_row, self._steam_sync_row, \
            self._steam_sync_btn = st_key_row, st_id_row, st_sync_row, st_sync_btn
        page.add(st_group)

        win = Adw.Dialog(content_width=560, content_height=560)
        win.set_child(toolbar)
        win.connect("closed", self._on_settings_closed)
        self._settings_win = win
        win.present(self)

    def _on_lang(self, row, _p):
        code = available()[row.get_selected()][0]
        if code == get_lang():
            return
        P.save_lang(self.con, code)
        GLib.idle_add(self._rebuild)

    def _on_overlay_toggle(self, row, _ps):
        """Réglage « Overlay in-game » : prochains lancements seulement."""
        P.set_setting(self.con, "overlay", "1" if row.active else "0")

    def _on_souris_libre_toggle(self, row, _ps):
        """Réglage « Libérer la souris des jeux » : prochains lancements seulement.

        Transmis à l'overlay par flag (`--souris-libre` / `--sans-souris-libre`)
        au lancement de la session — l'overlay ne stocke plus ce réglage."""
        P.set_setting(self.con, "souris_libre", "1" if row.active else "0")

    def _on_module(self, sw, _p, name):
        P.set_setting(self.con, f"module_{name}", "1" if sw.get_active() else "0")
        self._sync_modules()

    def _on_notes_font(self, spin, _p):
        v = int(spin.get_value())
        # Mémoriser AVANT d'écrire : `notes_font_size` est la valeur que les réglages
        # réaffichent en rouvrant. Tant qu'elle ne suit pas, on rouvre les réglages
        # sur l'ancien chiffre — et une notification tardive du curseur réécrit même
        # l'ancienne valeur par-dessus celle qu'on vient de choisir.
        self.notes_font_size = v
        P.set_setting(self.con, "notes_font_size", str(v))
        self._apply_note_font(v)

    def _on_retro_enable(self, sw, _p, kind):
        P.set_setting(self.con, f"retro_enable_{kind}", "1" if sw.get_active() else "0")
        self._refresh_retro()

    def _rebuild(self):
        """Les textes sont posés à la construction : on rouvre la fenêtre dans la nouvelle langue."""
        from .main import Win  # import tardif : évite le cycle main <-> settingsui
        app = self.get_application()
        if self._settings_win:
            self._settings_win.close()
        new = Win(app, do_import=False)  # pas de nouvel import : rien n'a changé dans la bibliothèque
        new._present()
        new._open_settings()
        self.close()
        return False

    def _on_settings_closed(self, *_):
        self._settings_win = None
        return False

    def _toast_settings(self, msg):
        """Toast DANS le dialogue ouvert — celui de la fenêtre se perdrait derrière."""
        cible = self._settings_overlay if self._settings_win is not None else None
        (cible if cible is not None else self.toasts).add_toast(
            Adw.Toast.new(GLib.markup_escape_text(msg)))

    def _on_key(self, row):
        P.save_sgdb_key(row.get_text())
        self._toast_settings(tr("Key saved") if row.get_text().strip() else tr("Key cleared"))

    def _on_ach_show_hidden(self, chk):
        """Affiche/masque les succès secrets (verrouillés) partout dans l'app."""
        P.set_setting(self.con, "ach_show_hidden", "1" if chk.get_active() else "0")
        if hasattr(self, "_refresh_achievements"):
            self._refresh_achievements()   # le volet droit est sur la même page

    def _on_ra_enabled(self, row, _p):
        """Couche/ramène RetroAchievements depuis les réglages des succès.

        À OFF, les succès RA ne s'affichent plus nulle part, les jeux qui n'ont
        que ce lien quittent la liste de gauche, et plus aucun appel réseau n'est
        lancé (synchro manuelle ou actualisation automatique). Le scan YAML, lui,
        ne dépend pas de ce réglage et continue de tourner."""
        on = row.get_active()
        P.set_ra_enabled(self.con, on)
        for w in (self._ra_user_row, self._ra_key_row, self._ra_sync_row):
            if w is not None:
                w.set_sensitive(on)
        if hasattr(self, "_refresh_achievements"):
            self._refresh_achievements()

    def _on_ra_credentials(self, *_row):
        """Enregistre identifiant + clé (chaque ligne confirme la sienne ; on lit
        les deux pour ne jamais écraser la valeur de l'autre par une chaîne vide)."""
        P.set_ra_credentials(self.con, self._ra_user_row.get_text(),
                             self._ra_key_row.get_text())
        self._toast_settings(tr("RetroAchievements account saved: {user}",
                                user=self._ra_user_row.get_text().strip()))

    def _on_ra_sync(self, *_btn):
        self._on_ra_credentials()   # on enregistre d'abord ce qui est saisi
        try:
            n = P.sync_account(self.con)
        except (P.RaError, OSError) as e:
            return self._toast_settings(str(e))
        if hasattr(self, "_refresh_achievements"):
            self._refresh_achievements()
        self._toast_settings(tr("{n} game(s) linked to RetroAchievements", n=n))

    def _on_steam_enabled(self, row, _p):
        """Couche/ramène les succès Steam depuis les réglages des succès.

        À OFF, aucun succès Steam ne s'affiche plus nulle part, les jeux qui
        n'ont que cette source quittent la liste de gauche, et plus aucun appel
        réseau n'est lancé (synchro manuelle ou actualisation à la partie). Le
        scan YAML, lui, ne dépend pas de ce réglage et continue de tourner."""
        on = row.get_active()
        P.set_steam_enabled(self.con, on)
        for w in (self._steam_key_row, self._steam_id_row, self._steam_sync_row):
            if w is not None:
                w.set_sensitive(on)
        if hasattr(self, "_refresh_achievements"):
            self._refresh_achievements()

    def _on_steam_credentials(self, *_row):
        """Enregistre clé Web API + SteamID64 (chaque ligne confirme la sienne ;
        on lit les deux pour ne jamais écraser la valeur de l'autre par vide)."""
        P.set_steam_credentials(self.con, self._steam_key_row.get_text(),
                                self._steam_id_row.get_text())
        self._toast_settings(tr("Steam settings saved"))

    def _on_steam_sync(self, *_btn):
        """Télécharge les succès des jeux du compte, SANS figer l'interface.

        Le travail part dans un fil de fond doté de sa propre connexion (le
        motif exact de la récupération des couvertures) : la Steam Web API
        répond en ~0,3 s par appel et le compte compte des centaines de jeux
        — deux appels par jeu, soit l'application gelée une quarantaine de
        secondes sur le fil GTK principal. Le retour se fait donc par
        `GLib.idle_add`, avec le bouton grisé et la ligne en « Fetching… »."""
        self._on_steam_credentials()   # on enregistre d'abord ce qui est saisi
        if getattr(self, "_steam_syncing", False):
            return self._toast_settings(tr("Already fetching"))
        self._steam_syncing = True
        if self._steam_sync_btn is not None:
            self._steam_sync_btn.set_sensitive(False)
        if self._steam_sync_row is not None:
            self._steam_sync_row.set_subtitle(tr("Fetching…"))

        def work():
            n, err = 0, None
            try:
                con = P.db()          # connexion du fil : `self.con` lui est interdit
                try:
                    # Une minute d'attente sur un vrai compte : on affiche où
                    # en est la synchro plutôt qu'un « Fetching… » figé.
                    def progress(i, total):
                        GLib.idle_add(self._steam_sync_progress, i, total)
                    n = P.sync_steam_games(con, progress=progress)
                finally:
                    con.close()
            except Exception as e:    # rien ne doit laisser le bouton bloqué
                err = str(e) or type(e).__name__
            finally:
                self._steam_syncing = False
            GLib.idle_add(self._steam_sync_done, n, err)

        threading.Thread(target=work, daemon=True).start()

    def _steam_sync_progress(self, i, total):
        """Retour du fil de fond : « Fetching… (42/122) » en cours de route."""
        if self._steam_sync_row is not None:
            self._steam_sync_row.set_subtitle(f"{tr('Fetching…')} ({i}/{total})")
        return False

    def _steam_sync_done(self, n, err):
        """Retour du fil de fond : le réglage a pu être fermé entre-temps."""
        if self._steam_sync_btn is not None:
            self._steam_sync_btn.set_sensitive(True)
        if self._steam_sync_row is not None:
            self._steam_sync_row.set_subtitle(
                tr("Download the achievements of your games on Steam"))
        if hasattr(self, "_refresh_achievements"):
            self._refresh_achievements()
        self._toast_settings(err or tr("{n} game(s) synced", n=n))
        return False

    def _on_covers(self, *_):
        # Pas de garde sur la clé : le CDN de Steam fournit sans clé, les jeux Steam
        # sont donc couverts même quand SteamGridDB est hors d'atteinte.
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
                # relance manuelle : on retente tout — avec une clé, y compris les
                # couvertures d'origine CDN, que SGDB remplace
                con.execute("UPDATE games SET cover_fetched=0 WHERE cover_path=''"
                            + (" OR cover_src != 'sgdb'" if P.sgdb_key() else ""))
                con.commit()
                # `include_not_installed=True` : c'est un geste EXPLICITE de
                # l'utilisateur qui demande toutes les couvertures manquantes. Sans
                # lui, la boucle limite à `installed=1` — et les 236 démos de la
                # base ne sont JAMAIS installées : elles restaient hors d'atteinte,
                # malgré le retrait du « Demo » qui leur trouvait pourtant le jeu
                # complet (mesuré : 15/25 couvertes une fois sélectionnées).
                n = P.fetch_missing_covers(con, limit=500, progress=progress,
                                           include_not_installed=True)
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
        if getattr(self, "_covering", False):
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
