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
        self.ach_game_sort = "name"       # tri de la liste de gauche (menu dédié)
        self.ach_game_source = "all"      # filtre de source : toutes / RA / Steam / YAML
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
        # Filtre de source et tri : menus d'icônes COMPACTS dans l'en-tête, à
        # gauche du « + » — deux listes déroulantes pleine largeur déborderaient
        # du volet de 260 px. Les libellés complets (dont « Personnalisés »)
        # vivent dans le menu, avec une coche sur le choix courant, et
        # l'infobulle répète le choix en cours.
        self.ach_source_btn = self._ach_menu_button("view-filter-symbolic",
                                                    tr("Filter by source"), "source")
        self.ach_sort_btn = self._ach_menu_button("view-sort-ascending-symbolic",
                                                  tr("Sort"), "sort")
        head.append(self.ach_source_btn)
        head.append(self.ach_sort_btn)
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
        self.ach_empty = Adw.StatusPage(icon_name="starred-symbolic", vexpand=True,
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

    def _ach_bar(self, row, done, total):
        """Barre de progression pleine largeur, posée SOUS le nom du jeu.

        Pas de pourcentage à droite du nom : la barre occupe la largeur de la ligne,
        sous le titre — le pourcentage se lit dans l'infobulle. `done` est une SOMME
        DE FRACTIONS (un succès à 3/5 vaut 0,6 succès) : la barre est donc plus fine
        que le compteur « 12/30 » de la fiche, qui ne compte que les succès entièrement
        débloqués. Borné à [0, 1] par sécurité — une base héritée d'un YAML réécrit ne
        peut pas faire déborder la barre. Aucune barre si le jeu n'a aucun succès.

        Où l'accrocher : la boîte interne de l'ActionRow est HORIZONTALE (préfixe,
        titre, suffixes côte à côte) — y ajouter un enfant le place à CÔTÉ du titre,
        où il est écrasé jusqu'à être invisible. C'est la boîte TITRE (verticale,
        libellé puis sous-titre) qu'il faut viser : la barre y devient le dernier
        enfant, donc une ligne posée sous le nom, pleine largeur. `add_suffix` reste
        inutilisé : il ne placerait qu'à droite du nom."""
        if not total:
            return  # jeu sans aucun succès connu : la ligne reste nue
        tbox = self._ach_title_box(row)
        if tbox is None:
            return  # structure interne inattendue : on n'abîme pas la ligne
        frac = max(0.0, min(1.0, done / total))
        bar = Gtk.ProgressBar(
            fraction=frac, hexpand=True,
            # Pas de « 12/30 » ici : `done` est une somme de fractions (1,6 pour un
            # 1.0 + un 3/5), et l'afficher en déblocage entier mentirait.
            tooltip_text=tr("{n} % unlocked", n=round(frac * 100)))
        # `add_css_class` ET NON `css_classes=[...]` au constructeur : en GTK4 cette
        # propriété REMPLACE la liste entière, ce qui efface la classe `horizontal`
        # que GtkProgressBar s'attribue. Sans elle, Breeze n'applique plus
        # `progressbar.horizontal trough/progress` et la barre se réduit à un
        # rectangle transparent — elle existe alors dans l'arbre sans se peindre.
        bar.add_css_class("ach-bar")
        tbox.append(bar)

    @staticmethod
    def _ach_title_box(row):
        """La boîte VERTICALE interne qui porte le titre d'une ActionRow, ou None.

        ActionRow construit sa boîte interne elle-même : on la repère parmi ses
        enfants par celle qui contient un Gtk.Label portant la classe CSS `title`.
        On NE compare pas le texte du libellé au titre de la ligne : le titre est
        stocké ÉCHAPPÉ en markup (`Baldur&apos;s Gate`) alors que `Label.get_text()`
        renvoie le texte rendu (`Baldur's Gate`) — un jeu avec un esperluette dans
        le nom rendrait la comparaison impossible et supprimerait sa barre. On ne
        touche jamais à la structure si elle diffère de l'attendu."""
        inner = row.get_child()
        if inner is None:
            return None
        child = inner.get_first_child()
        while child is not None:
            sub = child.get_first_child()
            while sub is not None:
                if isinstance(sub, Gtk.Label) and "title" in sub.get_css_classes():
                    return child
                sub = sub.get_next_sibling()
            child = child.get_next_sibling()
        return None

    def _fill_ach_list(self):
        """Remplit la liste de gauche ; la sélection courante est conservée si possible."""
        self._ach_filling = True
        try:
            while (child := self.ach_list.get_first_child()) is not None:
                self.ach_list.remove(child)
            self._ach_row_by_gid = {}
            tous = P.achievement_games(self.con)  # suivi YAML, lien RA et/ou cache Steam
            # Une seule passe pour la complétion de TOUS les jeux (aucune requête par jeu).
            compl = P.completions(self.con)
            # Filtre insensible à la casse sur le nom du jeu, puis filtre de
            # source et tri — les deux menus du volet gauche.
            q = self.ach_game_query.strip().casefold()
            cand = [g for g in tous if not q or q in g[1].casefold()]
            games = P.filter_sort_game_rows(cand, compl, self.ach_game_sort,
                                            self.ach_game_source)
            for gid, name, _has_tracker, _has_ra, _has_steam in games:
                row = Adw.ActionRow(title=GLib.markup_escape_text(name))
                row._gid = gid
                self._ach_bar(row, *compl.get(gid, (0.0, 0)))   # barre SOUS le nom
                self.ach_list.append(row)
                self._ach_row_by_gid[gid] = row
            if self.ach_game_id not in self._ach_row_by_gid:
                # Un jeu suivi que la recherche OU le filtre de source MASQUE ne
                # fait pas changer de jeu : le volet droit reste sur la partie en
                # cours, seule la liste reflète les réglages. C'est la perte du
                # suivi (le jeu disparaît de la liste complète) qui bascule sur
                # le premier jeu, comme auparavant.
                if not any(g[0] == self.ach_game_id for g in tous):
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

    # ---------- menus de tri et de filtre de la liste de gauche ----------
    def _ach_menu_button(self, icon, tooltip, kind):
        """MenuButton d'icône qui déroule une liste de choix (coche sur le courant)."""
        btn = Gtk.MenuButton(icon_name=icon, tooltip_text=tooltip,
                             valign=Gtk.Align.CENTER, css_classes=["flat"])
        btn._kind = kind
        pop = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                      margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
        pop.set_child(box)
        btn.set_popover(pop)
        btn._choices = box
        # Gtk.MenuButton n'émet PAS « clicked » (ce n'est pas un GtkButton) : le
        # menu est rafraîchi à la construction et après chaque choix — il est
        # donc toujours juste au moment de l'ouverture.
        self._refill_ach_menu(btn)
        return btn

    @staticmethod
    def _ach_menu_labels(kind):
        """Libellés complets des choix — vivent DANS le menu, pas dans l'en-tête."""
        if kind == "source":
            return [tr("All sources"), tr("RetroAchievements"), tr("Steam"),
                    tr("Custom achievements")]
        return [tr("Name A→Z"), tr("Name Z→A"), tr("Completion")]  # l'ordre suit ACH_GAME_SORTS

    def _refill_ach_menu(self, btn):
        """Reconstruit les lignes du menu au moment de l'ouvrir : la coche suit
        le choix courant, et le libellé est repris dans la langue du moment."""
        box = btn._choices
        while box.get_first_child() is not None:
            box.remove(box.get_first_child())
        cur = (P.ACH_GAME_SOURCES.index(self.ach_game_source) if btn._kind == "source"
               else P.ACH_GAME_SORTS.index(self.ach_game_sort))
        pick = self._pick_ach_source if btn._kind == "source" else self._pick_ach_sort
        for i, label in enumerate(self._ach_menu_labels(btn._kind)):
            # Ligne PLEINE largeur : sans halign=START, le bouton occupe toute
            # la boîte du menu (comme un vrai menu), texte aligné à gauche.
            row = Gtk.Button(css_classes=["flat"], hexpand=True)
            row.set_child(Gtk.Label(label=("✓  " if i == cur else "      ") + label, xalign=0))
            row.connect("clicked",
                        lambda _b, idx=i, b=btn: (b.get_popover().popdown(), pick(idx)))
            box.append(row)

    def _pick_ach_source(self, idx):
        """Filtre de source choisi : on refait la liste de gauche, l'infobulle suit.

        Comme la recherche, le réglage ne change JAMAIS le jeu affiché : seul le
        volet gauche suit. Aucun re-scan ni appel réseau — les sources sont déjà
        en base."""
        self.ach_game_source = P.ACH_GAME_SOURCES[idx]
        self.ach_source_btn.set_tooltip_text(
            f"{tr('Filter by source')} — {self._ach_menu_labels('source')[idx]}")
        self._refill_ach_menu(self.ach_source_btn)     # la coche suit le choix
        self._fill_ach_list()

    def _pick_ach_sort(self, idx):
        """Tri choisi : on refait la liste de gauche, l'infobulle suit."""
        self.ach_game_sort = P.ACH_GAME_SORTS[idx]
        self.ach_sort_btn.set_tooltip_text(
            f"{tr('Sort')} — {self._ach_menu_labels('sort')[idx]}")
        self._refill_ach_menu(self.ach_sort_btn)       # la coche suit le choix
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

        tracks = P.tracker_paths(self.con, gid)
        # Le bouton d'engrenage ouvre la configuration du SUIVI YAML (fichier de
        # succès + log). Un jeu suivi par RetroAchievements ou Steam n'y a rien à
        # régler — ces sources se configurent depuis les réglages des succès — et
        # l'ajout d'un suivi reste possible par le « + » de la liste de gauche. Le
        # bouton n'est donc montré que lorsqu'il sert.
        self.ach_gear.set_visible(tracks is not None)
        if tracks is not None:
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
            self.ach_sort, show_hidden=self._ach_show_hidden)
        ra_rows = P.sort_achievements(
            P.filter_achievements(self._ra_state, self.ach_filter, self.ach_query,
                                  show_hidden=self._ach_show_hidden),
            self.ach_sort, show_hidden=self._ach_show_hidden)
        steam_rows = P.sort_achievements(
            P.filter_achievements(self._steam_state, self.ach_filter, self.ach_query,
                                  show_hidden=self._ach_show_hidden),
            self.ach_sort, show_hidden=self._ach_show_hidden)
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
                # Barre lisse (pas de segments — arbitrage utilisateur) AVANT l'étiquette :
                # l'ordre des add_suffix suit l'ordre d'appel. Le libellé 3/5 reste, la
                # barre ne fait que le rendre visible. Les succès binaires (target == 1),
                # RA et Steam n'ont pas de target : rien à mesurer, ils n'entrent pas ici.
                pbar = Gtk.ProgressBar(
                    fraction=max(0.0, min(1.0, a["progress"] / a["target"])),
                    valign=Gtk.Align.CENTER, width_request=110,
                    tooltip_text=tr("{n} % unlocked", n=round(a["progress"] / a["target"] * 100)))
                # add_css_class et non css_classes=[...] : voir _ach_bar — la propriété
                # css_classes REMPLACE la liste et effacerait la classe `horizontal`,
                # sans laquelle Breeze ne peint ni trough ni progress.
                pbar.add_css_class("ach-bar")
                row.add_suffix(pbar)
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

        # --- actions : empilées verticalement, chacune pleine largeur ---
        # Côte à côte sur une ligne, les trois boutons se partageaient la grille en
        # colonnes de 152 / 230 / 182 px alors que la fenêtre en fait 640 : la cible
        # cliquable d'« Enregistrer » — l'action principale — se réduisait à un tiers
        # de la largeur disponible. Empilés et pleine largeur, l'ordre visuel
        # (principal → destructif → fermeture) devient aussi l'ordre de lecture.
        save = Gtk.Button(label=tr("Save"), css_classes=["suggested-action"])
        # Sans ce bouton, seul Échap ferme le dialogue (pas d'en-tête, pas de
        # bouton natif sur Adw.Dialog tel qu'ici configuré).
        close = Gtk.Button(label=tr("Close"))
        actions = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                          margin_top=8, hexpand=True)
        actions.append(save)
        # « Retirer le suivi » ne sert QUE s'il existe un suivi à retirer. Ouvert
        # par le « + » ou par « Configurer un suivi », le dialogue n'en a aucun, et
        # le bouton n'y faisait rien d'autre que fermer la fenêtre (`gid` vaut
        # None → aucun retrait). Il n'existe donc qu'en mode configuration d'un
        # suivi déjà en place, c'est-à-dire depuis l'engrenage.
        remove = None
        if gid is not None and P.tracker_paths(self.con, gid) is not None:
            remove = Gtk.Button(label=tr("Remove tracker"))
            actions.append(remove)
        actions.append(close)
        grid.attach(actions, 0, row[0], 3, 1)   # les 3 colonnes = toute la grille
        # Références pour les tests de largeur/position (comme `dlg._close_btn`).
        dlg._save_btn, dlg._remove_btn = save, remove

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
            # Pas de garde sur `gid` : ce bouton n'existe que si un suivi est déjà
            # en place, donc `gid` y est toujours renseigné. Une garde masquerait
            # que la condition d'existence du bouton a été rompue.
            P.clear_tracker(self.con, gid)
            dlg.close()
            self._refresh_achievements()

        save.connect("clicked", on_save)
        if remove is not None:
            remove.connect("clicked", on_remove)
        close.connect("clicked", lambda *_: dlg.close())
        dlg._close_btn = close  # référence pour les tests

        # Simple zone défilante : c'est le contenu du dialogue (Adw.Dialog
        # porte lui-même son en-tête avec le titre).
        scroll = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroll.set_child(grid)
        # La fenêtre se taille au contenu. `content_height` fixe la hauteur AFFICHÉE
        # du dialogue : aux 480 px déclarés, la grille était étirée et laissait
        # 304 px de vide sous les boutons. On prend la hauteur NATURELLE de la
        # grille, plus ses marges — recalculée à chaque ouverture, elle suit la
        # langue, la police et les fichiers choisis.
        #
        # La largeur doit être FOURNIE à `measure()` : à -1 (non contraint) le texte
        # des chemins ne se retourne pas et la hauteur ressort trop petite — mesuré,
        # 302 px avec aucun chemin renseigné, mais 384 annoncés pour 438 réels dès
        # qu'un YAML est chargé. La grille est allouée sur `content_width` moins ses
        # marges : c'est là qu'il faut mesurer.
        largeur = dlg.get_content_width() - grid.get_margin_start() - grid.get_margin_end()
        dlg.set_content_height(grid.measure(Gtk.Orientation.VERTICAL, largeur)[1]
                               + grid.get_margin_top() + grid.get_margin_bottom())
        return scroll
