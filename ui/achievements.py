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

# Fréquence de sondage des logs des jeux suivis. Le sondage ne coûte qu'un
# `stat()` par jeu suivi, et ne déclenche un scan que si le log a changé.
ACH_POLL_SECONDS = 5


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
        self.ach_filter = "all"       # tri/filtre appliqués au volet droit (persistants)
        self.ach_sort = "origin"
        self.ach_query = ""
        self.ach_game_query = ""      # recherche appliquée au volet GAUCHE (liste des jeux)
        self._ach_state = []          # succès du jeu courant AVANT tri/filtre
        self._ra_state = []           # idem pour les succès RetroAchievements
        self._steam_state = []        # idem pour les succès Steam
        self._ach_ra_linked = False
        self._ach_show_hidden = P.show_hidden(self.con)  # réglage global des succès cachés
        self._ach_watcher = None      # LogWatcher : détecte les logs en cours d'écriture
        self._ach_watch_id = None     # id du timer GLib (démarré une seule fois)
        self._tracker_dlg = None
        self._reset_dlg = None

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
        # Recherche : même implantation que la page Notes — une barre entre
        # l'en-tête et la liste. Elle ne filtre QUE la liste de gauche.
        self.ach_game_search = Gtk.SearchEntry()
        if hasattr(self.ach_game_search, "set_placeholder_text"):  # GTK >= 4.10
            self.ach_game_search.set_placeholder_text(tr("Search games…"))
        self.ach_game_search.connect("search-changed", self._on_ach_game_filter)
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        left.append(head)
        left.append(self.ach_game_search)
        left.append(Gtk.ScrolledWindow(child=self.ach_list, vexpand=True,
                                       hscrollbar_policy=Gtk.PolicyType.NEVER))

        # --- volet droit : succès du jeu sélectionné ---
        self.ach_title = Gtk.Label(xalign=0, hexpand=True, css_classes=["title-2"])
        self.ach_subtitle = Gtk.Label(xalign=0, css_classes=["dim-label"], visible=False)
        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        title_box.append(self.ach_title)
        title_box.append(self.ach_subtitle)
        # Bouton unique : relit le log local ET synchronise RetroAchievements
        # (si le jeu est lié au compte). Un seul geste, pas deux boutons.
        self.ach_rescan = Gtk.Button(icon_name="view-refresh-symbolic",
                                     tooltip_text=tr("Refresh"), valign=Gtk.Align.CENTER)
        self.ach_rescan.connect("clicked", lambda *_: self._resync_game(self.ach_game_id))
        self.ach_gear = Gtk.Button(icon_name="preferences-system-symbolic", css_classes=["flat"],
                                   tooltip_text=tr("Achievement settings"), valign=Gtk.Align.CENTER)
        self.ach_gear.connect("clicked", lambda *_: self._open_tracker_dialog(self.ach_game_id))
        self.ach_reset = Gtk.Button(icon_name="edit-clear-all-symbolic", css_classes=["flat"],
                                    tooltip_text=tr("Reset achievements"), valign=Gtk.Align.CENTER)
        self.ach_reset.connect("clicked", lambda *_: self._reset_achievements())
        head_r = Gtk.Box(spacing=6, margin_top=8, margin_bottom=6, margin_start=12, margin_end=6)
        head_r.append(title_box)
        head_r.append(self.ach_rescan)
        head_r.append(self.ach_gear)
        head_r.append(self.ach_reset)
        # --- barre d'outils : recherche + filtre + tri (appliqués au volet droit) ---
        self.ach_search = Gtk.SearchEntry(hexpand=True,
                                          placeholder_text=tr("Search achievements"))
        self.ach_search.connect("search-changed", self._on_ach_tool_changed)
        self.ach_filter_dd = Gtk.DropDown.new_from_strings([tr(f) for f in
                                                            ("All", "Unlocked", "Locked")])
        self.ach_filter_dd.connect("notify::selected", self._on_ach_tool_changed)
        self.ach_sort_dd = Gtk.DropDown.new_from_strings([
            tr("Original order"), tr("Title A→Z"), tr("Title Z→A"), tr("Unlocked first"),
            tr("Locked first")])
        self.ach_sort_dd.connect("notify::selected", self._on_ach_tool_changed)
        # Les Adw.ActionRow doivent vivre dans un Gtk.ListBox (posées dans un Gtk.Box
        # direct, libadwaita émet un avertissement et peut faire planter GTK).
        self.achievements_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                        margin_top=6, margin_bottom=12)
        tools = Gtk.Box(spacing=6, margin_start=12, margin_end=12, margin_top=2)
        tools.append(self.ach_search)
        tools.append(self.ach_filter_dd)
        tools.append(self.ach_sort_dd)
        scroll_r = Gtk.ScrolledWindow(child=self.achievements_box, vexpand=True,
                                      hscrollbar_policy=Gtk.PolicyType.NEVER)
        detail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        detail.append(head_r)
        detail.append(tools)
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
        self._start_ach_watcher()
        return paned

    # ---------- actualisation automatique des logs des jeux suivis ----------
    def _start_ach_watcher(self):
        """Démarre le sondage des logs (une seule fois, pour toute la fenêtre).

        Le jeu est repéré par l'écriture de son fichier de log : pas besoin de
        connaître son processus, et aucun travail tant que rien ne bouge."""
        if self._ach_watch_id is not None:
            return
        self._ach_watch_id = GLib.timeout_add_seconds(ACH_POLL_SECONDS, self._poll_achievements)

    def _stop_ach_watcher(self):
        if self._ach_watch_id is not None:
            GLib.source_remove(self._ach_watch_id)
            self._ach_watch_id = None

    def _poll_achievements(self):
        """Re-scanne les jeux dont le log a changé ; retourne True pour recommencer.

        « write » : le jeu écrit = il tourne → actualisation périodique.
        « closed » : les écritures viennent de s'arrêter = le jeu vient de se
        fermer → un dernier scan pour récupérer les dernières lignes.

        Une partie repérée met à jour les TROIS sources du jeu : le suivi YAML
        local, la synchro RetroAchievements (jeu lié), la synchro Steam (jeu
        d'origine Steam) — chacune seulement si elle est activée dans les
        réglages. Un seul snapshot avant/après les couvre donc toutes, et un
        seul toast annonce tous les nouveaux succès."""
        if not P.module_enabled(self.con, "achievements"):
            return True   # module masqué : on sonde mais on ne fait rien
        if self._ach_watcher is None:
            self._ach_watcher = P.LogWatcher()
        for gid, _reason in self._ach_watcher.poll(self.con):
            avant = self._unlocked_snapshot(gid)
            fait_scan = self._scan_tracked(gid)        # les trois doivent tourner
            fait_ra = self._sync_ra_played(gid)         # (pas de `or` : court-circuit)
            fait_steam = self._sync_steam_played(gid)
            nouveaux = self._unlocked_snapshot(gid) - avant
            touche = fait_scan or fait_ra or fait_steam
            if nouveaux:
                g = self.con.execute("SELECT name FROM games WHERE id=?", (gid,)).fetchone()
                self.toasts.add_toast(Adw.Toast.new(tr("{n} new achievement(s) unlocked in {game}",
                                                       n=len(nouveaux),
                                                       game=g["name"] if g else "")))
                touche = True
            if touche:
                self._on_auto_scan(gid)
        return True

    def _unlocked_snapshot(self, gid):
        """Ensemble des succès ACQUIS du jeu : log YAML, RetroAchievements, Steam.

        Deux snapshots encadrant un sondage donnent d'un coup les déblocages des
        trois sources : ni toast en double, ni succès oublié."""
        out = {("log", r["ach_id"]) for r in self.con.execute(
            "SELECT ach_id FROM achievements WHERE game_id=? AND unlocked_at>0", (gid,))}
        out |= {("ra", r["ach_id"]) for r in self.con.execute(
            "SELECT ach_id FROM ra_achievements WHERE game_id=? AND earned_at>0", (gid,))}
        return out | {("steam", r["ach_id"]) for r in self.con.execute(
            "SELECT ach_id FROM steam_achievements WHERE game_id=? AND earned_at>0", (gid,))}

    def _scan_tracked(self, gid):
        """Relit le log YAML du jeu. Renvoie True si le scan a tourné.

        Les nouveaux succès sont relevés par le snapshot, pas ici : cette méthode
        ne fait que lire. Un log ou un YAML cassé n'interrompt rien — l'erreur est
        déjà affichée à l'ouverture du jeu."""
        if P.tracker_paths(self.con, gid) is None:
            return False   # jeu suivi par une seule source téléchargée : rien à lire
        try:
            P.scan(self.con, gid)
        except (FileNotFoundError, ValueError):
            return False
        return True

    def _sync_ra_played(self, gid):
        """Synchronise RetroAchievements pour un jeu en train d'être joué.

        Même principe que le log : la détection de la partie suffit à déclencher
        la synchro, sans intervention. Un souci réseau n'interrompt rien — la
        prochaine écriture réessaiera. Renvoie True si la synchro a tourné."""
        if not P.ra_enabled(self.con) or P.linked(self.con, gid) is None:
            return False
        try:
            P.sync_game(self.con, gid)
        except (P.RaError, OSError):
            return False
        return True

    def _sync_steam_played(self, gid):
        """Synchronise les succès Steam pour un jeu en train d'être joué.

        C'est le `appmanifest_<appid>.acf` qui bouge quand Steam lance un jeu :
        même détection, même cadence et même tolérance d'erreur qu'avec le log.
        Renvoie True si la synchro a tourné."""
        if not P.steam_enabled(self.con) or P.steam_appid(self.con, gid) is None:
            return False
        try:
            P.sync_steam_game(self.con, gid)
        except (P.SteamError, OSError):
            return False
        return True

    def _on_auto_scan(self, gid):
        """Met à jour l'affichage après un scan automatique, sans perdre la sélection."""
        self._fill_ach_list()                    # compteur de la liste de gauche
        if self.ach_game_id == gid and self.stack.get_visible_child_name() == "achievements":
            self._show_ach_game(gid)             # conserve tri/filtre : ce sont des réglages de page

    def _refresh_achievements(self):
        """Reconstruit la liste des jeux suivis et le détail du jeu sélectionné."""
        self._ach_show_hidden = P.show_hidden(self.con)  # réglage modifiable dans les préférences
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
            tous = P.achievement_games(self.con)  # suivi YAML et/ou lien RetroAchievements
            # Filtre insensible à la casse sur le nom du jeu.
            q = self.ach_game_query.strip().casefold()
            games = [g for g in tous if not q or q in g[1].casefold()]
            for gid, name, _has_tracker, _has_ra in games:
                row = Adw.ActionRow(title=GLib.markup_escape_text(name))
                row._gid = gid
                self.ach_list.append(row)
                self._ach_row_by_gid[gid] = row
            if self.ach_game_id not in self._ach_row_by_gid:
                # Un jeu suivi que la recherche MASQUE ne fait pas changer de jeu :
                # le volet droit reste sur la partie en cours, seule la liste reflète
                # la recherche. C'est la perte du suivi (recherche vide) qui bascule
                # sur le premier jeu, comme auparavant.
                if not (q and any(g[0] == self.ach_game_id for g in tous)):
                    self.ach_game_id = games[0][0] if games else None
            row = self._ach_row_by_gid.get(self.ach_game_id)
            if row is not None:
                self.ach_list.select_row(row)
        finally:
            self._ach_filling = False

    def _on_ach_game_filter(self, entry):
        """Recherche dans la liste de gauche : seul le volet gauche est filtré.

        Écrire ne doit JAMAIS changer de jeu — on ne bascule qu'au clic. Un jeu
        masqué par la recherche reste donc le jeu affiché (aucune ligne n'est
        alors sélectionnée), et il reparaît sélectionné dès qu'on efface.
        """
        self.ach_game_query = entry.get_text()
        self._fill_ach_list()

    def _on_ach_game_selected(self, _lb, row):
        if row is None or self._ach_filling:
            return
        gid = getattr(row, "_gid", None)
        if gid is None or gid == self.ach_game_id:
            return
        self._show_ach_game(gid)

    def _on_ach_tool_changed(self, *_args):
        """Tri/filtre/recherche modifiés : on redessine sans re-scan ni appel réseau."""
        self.ach_filter = P.ACHIEVEMENT_FILTERS[self.ach_filter_dd.get_selected()]
        self.ach_sort = P.ACHIEVEMENT_SORTS[self.ach_sort_dd.get_selected()]
        self.ach_query = self.ach_search.get_text() or ""
        self._render_ach_lists()

    def _show_ach_game(self, gid):
        """Charge les succès du jeu (log et/ou caches RA et Steam), puis les affiche."""
        self.ach_game_id = gid
        g = self.con.execute("SELECT name FROM games WHERE id=?", (gid,)).fetchone()
        self._ach_name = g["name"] if g else ""
        self.ach_title.set_text(self._ach_name)
        self._ach_error = None
        self.ach_subtitle.set_visible(False)
        # L'état local doit être vidé AVANT de recharger : sinon un jeu sans suivi
        # YAML (ex. suivi uniquement par RetroAchievements) réaffiche les succès
        # du jeu précédemment sélectionné.
        self._ach_state = []

        if P.tracker_paths(self.con, gid) is not None:
            try:
                state = P.scan(self.con, gid)
            except FileNotFoundError as e:
                self._ach_error = tr("log file missing: {path}", path=e.filename)
            except ValueError as e:
                self._ach_error = str(e)
            else:
                self._ach_entries[gid] = {a["id"]: a for a in state}
                self._ach_state = state
        # Réglages « Use RetroAchievements » / « Use Steam achievements »
        # (réglages → Succès) : à OFF, la source n'est pas chargée — donc ni
        # affichée, ni comptée — et le jeu n'est pas considéré comme lié, sinon
        # la section « synchroniser » se proposerait pour rien.
        ra_on = P.ra_enabled(self.con)
        self._ra_state = P.ra_achievements(self.con, gid) if ra_on else []
        self._ach_ra_linked = ra_on and P.linked(self.con, gid) is not None
        self._steam_state = P.steam_achievements(self.con, gid) \
            if P.steam_enabled(self.con) else []
        # Le compteur affiché sous le titre additionne les TROIS sources : un jeu
        # affiche son nombre au même endroit, d'où que viennent ses succès.
        done = sum(1 for a in self._ach_state if a["done"]) \
            + sum(1 for a in self._ra_state if a["earned_at"]) \
            + sum(1 for a in self._steam_state if a["earned_at"])
        total = len(self._ach_state) + len(self._ra_state) + len(self._steam_state)
        if total:
            self.ach_subtitle.set_text(tr("{done}/{total} unlocked", done=done, total=total))
            self.ach_subtitle.set_visible(True)
        self._render_ach_lists()
        self.ach_right.set_visible_child_name("detail")

    def _render_ach_lists(self):
        """Redessine le volet droit à partir de l'état chargé, selon le tri/filtre courants."""
        while (child := self.achievements_box.get_first_child()) is not None:
            self.achievements_box.remove(child)
        self._ach_rows = {}
        self._ach_group = None
        gid = self.ach_game_id
        # Les trois listes passent par le même tri et le même filtre (logique
        # partagée). `show_hidden` est indispensable : sans lui, la recherche
        # matcherait le vrai nom d'un succès encore masqué et en trahirait le
        # spoiler.
        local = P.sort_achievements(
            P.filter_achievements(self._ach_state, self.ach_filter, self.ach_query,
                                  show_hidden=self._ach_show_hidden),
            self.ach_sort)
        ra_rows = P.sort_achievements(
            P.filter_achievements(self._ra_state, self.ach_filter, self.ach_query,
                                  show_hidden=self._ach_show_hidden),
            self.ach_sort)
        steam_rows = P.sort_achievements(
            P.filter_achievements(self._steam_state, self.ach_filter, self.ach_query,
                                  show_hidden=self._ach_show_hidden),
            self.ach_sort)
        if self._ach_error:
            self._ach_group = self._new_ach_group()
            self._add_game_error(self._ach_name, self._ach_error)
            self.ach_right.set_visible_child_name("detail")
            return
        if local:
            self._ach_group = self._new_ach_group()
            for a in local:
                self._append_achievement_row(a)
        if ra_rows:
            self._append_ra_section(gid, ra_rows)
        elif self._ach_ra_linked and not self._ra_state:
            # lié au compte mais sans succès en cache : on propose la synchro
            # plutôt que d'afficher « aucun succès » (le téléchargement manque).
            self._append_ra_section(gid, [], empty=True)
        if steam_rows:
            self._append_cached_rows(steam_rows)
        rendered = (bool(local) or bool(ra_rows) or bool(steam_rows)
                    or (self._ach_ra_linked and not self._ra_state))
        if not rendered:
            self._ach_group = self._new_ach_group()
            # distingue « rien pour ce jeu » de « tout est masqué par le filtre »
            msg = (tr("no achievement matches the filter")
                   if (self._ach_state or self._ra_state or self._steam_state)
                   else tr("no achievements for this game"))
            self._add_game_error(self._ach_name, msg)

    def _new_ach_group(self):
        """Une Gtk.ListBox « boxed-list » (conteneur attendu pour les Adw.ActionRow)."""
        group = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"],
                            margin_start=12, margin_end=12, margin_top=6, margin_bottom=6)
        self.achievements_box.append(group)
        return group

    # ---------- succès téléchargés : RetroAchievements et Steam, sans en-tête ----------
    def _append_ra_section(self, gid, rows, empty=False):
        """Ajoute les succès RetroAchievements AU MÊME groupe que les succès locaux.

        L'en-tête « RetroAchievements » (titre + compteur) a été retiré : le
        compte total est déjà affiché dans le sous-titre, au-dessus de la barre
        de recherche. Les sources sont donc rendues d'un seul bloc — un
        second `boxed-list` posé juste en dessous aurait l'air d'une rupture."""
        if empty:
            if self._ach_group is None:
                self._ach_group = self._new_ach_group()
            group = self._ach_group
            row = Adw.ActionRow(title=tr("Synchronise RetroAchievements to see them"))
            row.set_activatable(False)
            # Attention : `row.get_last_child()` renvoie la Gtk.Box INTERNE de
            # l'ActionRow, pas le widget ajouté via add_suffix(). On garde donc
            # une référence explicite sur le bouton.
            sync_btn = Gtk.Button(icon_name="view-refresh-symbolic", css_classes=["flat"],
                                  tooltip_text=tr("Refresh"), valign=Gtk.Align.CENTER)
            sync_btn.connect("clicked", lambda *_: self._resync_game(gid))
            row.add_suffix(sync_btn)
            group.append(row)
            return
        self._append_cached_rows(rows)

    def _append_cached_rows(self, rows):
        """Ajoute des succès téléchargés (RetroAchievements, Steam) au groupe courant."""
        if self._ach_group is None:
            self._ach_group = self._new_ach_group()
        for a in rows:
            self._ach_group.append(self._source_row(a))

    def _source_row(self, a):
        """Ligne d'un succès téléchargé (RetroAchievements ou Steam).

        Trait pour trait identique, sauf la mention des points : Steam n'en
        attribue pas, et afficher « 0 pts » serait mensonger — la ligne se
        contente alors de son cadenas.

        Un succès `hidden` (le drapeau spoiler de Steam, absent de
        RetroAchievements) reste sous libellé générique tant qu'il est
        verrouillé : c'est le même verdict que la recherche, tiré de
        `core.tracker.is_masked`, sinon le nom réel resterait lisible ici
        pendant que la recherche le prétendrait introuvable."""
        done = bool(a["earned_at"])
        mask = P.is_masked(a, self._ach_show_hidden)
        row = Adw.ActionRow(title=tr("Hidden achievement") if mask
                            else GLib.markup_escape_text(a["title"]))
        row.set_activatable(False)
        row.add_prefix(Gtk.Label(label="❔" if mask else ("🏆" if done else "🔒"),
                                 css_classes=["accent"] if (done and not mask) else ["dim-label"]))
        if a["description"] and not mask:
            row.set_subtitle(GLib.markup_escape_text(a["description"]))
        if not mask:  # ni date ni points sous le masque : ils trahiraient le succès
            if done:
                date = datetime.datetime.fromtimestamp(a["earned_at"]).strftime("%d/%m/%Y")
                row.add_suffix(Gtk.Label(label=tr("Unlocked {date}", date=date),
                                         css_classes=["accent"]))
            elif a.get("points") is not None:
                row.add_suffix(Gtk.Label(label=tr("{points} pts", points=a["points"]),
                                         css_classes=["dim-label"]))
        return row

    def _resync_game(self, gid):
        """Bouton 🔄 unique : relit le log local ET synchronise les sources
        téléchargées (RetroAchievements, Steam).

        Chaque synchro n'a lieu que si la source est activée dans les réglages
        et concerne réellement le jeu (lié au compte, ou d'origine Steam) —
        sinon ce serait un appel API inutile. Une erreur n'empêche pas le
        rechargement des autres sources."""
        if gid is None:
            return
        if P.ra_enabled(self.con) and P.linked(self.con, gid) is not None:
            try:
                P.sync_game(self.con, gid)
            except (P.RaError, OSError) as e:
                self.toasts.add_toast(Adw.Toast.new(str(e)))
        if P.steam_enabled(self.con) and P.steam_appid(self.con, gid) is not None:
            try:
                P.sync_steam_game(self.con, gid)
            except (P.SteamError, OSError) as e:
                self.toasts.add_toast(Adw.Toast.new(str(e)))
        self._show_ach_game(gid)

    def _add_game_error(self, name, message):
        row = Adw.ActionRow(title=GLib.markup_escape_text(name),
                            subtitle=GLib.markup_escape_text(message))
        row.set_activatable(False)
        row.add_suffix(Gtk.Image(icon_name="dialog-warning-symbolic"))
        self._ach_group.append(row)

    def _append_achievement_row(self, a):
        """Une ligne par succès : icône, nom, description/progression, état.

        Un succès `hidden` reste masqué (nom, description et icône remplacés par
        un libellé générique) tant qu'il est verrouillé ; il est révélé une fois
        débloqué. Le réglage global « Reveal hidden achievements » (réglages →
        Succès) lève le masquage même verrouillé.

        Le verdict vient de `core.tracker.is_masked`, que la barre de recherche
        utilise aussi : affichage et recherche ne peuvent donc pas diverger."""
        mask = P.is_masked(a, self._ach_show_hidden)
        row = Adw.ActionRow(title=tr("Hidden achievement") if mask
                            else GLib.markup_escape_text(a["name"]))
        row.set_activatable(False)
        lead = Gtk.Label(label="❔" if mask else (a["icon"] or _TROPHY),
                         css_classes=["accent"] if (a["done"] and not mask) else ["dim-label"])
        row.add_prefix(lead)
        if not mask:
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

    # ---------- réinitialisation / retrait ----------
    def _reset_achievements(self):
        """Demande confirmation, puis efface la progression ou retire le jeu de la liste.

        Un jeu suivi uniquement par une source TÉLÉCHARGÉE (RetroAchievements ou
        Steam) n'a pas de progression locale à effacer : le bouton le retire
        donc de la liste (purge du cache téléchargé), seule action qui ait du
        sens — l'effacer ne changerait rien et le laisserait affiché."""
        gid = self.ach_game_id
        if gid is None:
            return
        g = self.con.execute("SELECT name FROM games WHERE id=?", (gid,)).fetchone()
        name = g["name"] if g else ""
        corps_reset = tr('All progress for "{name}" will be cleared. Lines already read in '
                         'the log will not be counted again.', name=name)
        if P.tracker_paths(self.con, gid) is not None:
            retirer, corps = False, corps_reset
        elif P.linked(self.con, gid) is not None:
            retirer, corps = True, tr('"{name}" is only followed through RetroAchievements. '
                                      'It will be unlinked from the account and removed from '
                                      'this list.', name=name)
        elif P.has_steam(self.con, gid):
            retirer, corps = True, tr('"{name}" is only followed through Steam. Its '
                                      'achievements will be removed from this list.', name=name)
        else:
            retirer, corps = False, corps_reset    # ni log ni cache : rien à retirer
        action = "remove" if retirer else "reset"
        dlg = Adw.AlertDialog(heading=tr("Remove from list?") if retirer
                              else tr("Reset achievements?"), body=corps)
        dlg.add_response("cancel", tr("Cancel"))
        dlg.add_response(action, tr("Remove") if retirer else tr("Reset"))
        dlg.set_response_appearance(action, Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.set_default_response("cancel")
        dlg.set_close_response("cancel")
        dlg.connect("response", self._on_remove_response if retirer
                    else self._on_reset_response, gid)
        self._reset_dlg = dlg
        dlg.present(self)

    def _on_reset_response(self, _dlg, response, gid):
        self._reset_dlg = None
        if response != "reset":
            return
        P.reset(self.con, gid)
        self._refresh_achievements()
        self.toasts.add_toast(Adw.Toast.new(tr("Achievements reset")))

    def _on_remove_response(self, _dlg, response, gid):
        """Retire de la liste un jeu suivi uniquement par une source téléchargée."""
        self._reset_dlg = None
        if response != "remove":
            return
        P.unlink(self.con, gid)         # dissocie du compte ET purge le cache RA
        P.forget_steam(self.con, gid)   # et le cache Steam téléchargé, s'il y en a un
        if self.ach_game_id == gid:
            self.ach_game_id = None
        self._refresh_achievements()
        self.toasts.add_toast(Adw.Toast.new(tr("Game removed from the list")))

    # ---------- dialogue de configuration ----------
    def _open_tracker_dialog(self, gid=None):
        if self._tracker_dlg is not None:
            self._tracker_dlg.present(self)
            return

        games = self.con.execute(
            f"SELECT id, name FROM games {self._lib_where(trailing=True)} ORDER BY name COLLATE NOCASE"
        ).fetchall()
        dlg = Adw.Dialog(title=tr("Achievement settings"), content_width=640, content_height=480)
        self._tracker_dlg = dlg
        dlg._games = games

        # Un seul contenu : la configuration locale (YAML + log). Le compte
        # RetroAchievements se configure depuis les réglages des succès
        # (engrenage → « RetroAchievements »), pas ici.
        dlg.set_child(self._tracker_dialog_body(dlg, games, gid))

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
        # Sans ce bouton, seul Échap ferme le dialogue (pas d'en-tête, pas de
        # bouton natif sur Adw.Dialog tel qu'ici configuré).
        close = Gtk.Button(label=tr("Close"))
        grid.attach(close, 2, row[0], 1, 1)

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
        close.connect("clicked", lambda *_: dlg.close())
        dlg._close_btn = close  # référence pour les tests

        # Simple zone défilante : c'est le contenu du dialogue (Adw.Dialog
        # porte lui-même son en-tête avec le titre).
        scroll = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroll.set_child(grid)
        return scroll