#!/usr/bin/env python3
"""retrospec.py — page « Rétrospective » : résumé, graphiques (quoi/quand/régularité) et frises."""
import datetime, math, time

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, GObject, Gtk, Pango

import picker as P
from i18n import available, get_lang, tr

# Camembert : palette fixe par rang (6 max), gris pour « Autres ».
PIE_COLORS = ("#614d8f", "#4f9e6e", "#c9a227", "#c96a3d", "#4f7fc9", "#c264a1")
OTHER_COLOR = "#55555f"


def dot_class(color):
    """Classe CSS de la pastille d'une part du camembert, déduite de SA COULEUR.

    La couleur décide de la classe — jamais l'index de la ligne : la part « Autres »
    (grise) tombe en 6ᵉ position à la suite des 5 rangs nommés et ne doit surtout pas
    hériter de la pastille rose du rang 6."""
    return f"retro-dot-{PIE_COLORS.index(color)}" if color in PIE_COLORS else "retro-dot-other"


def _hex_rgb(color):
    """'#614d8f' → (0.38, 0.30, 0.56) : composantes 0..1 pour Cairo."""
    color = color.lstrip("#")
    return tuple(int(color[i:i + 2], 16) / 255 for i in (0, 2, 4))


class RetroMixin:

    # ---------- page « Rétrospective » : frise au choix (semaine, mois, année) ----------
    _RETRO_PERIODS = (("week", "This week", "Week of {date} · {n} game(s) · {time}"),
                      ("month", "This month", "Month of {date} · {n} game(s) · {time}"),
                      ("year", "This year", "Year {date} · {n} game(s) · {time}"))
    _RETRO_SECTIONS = (("quoi", "What — time breakdown"),
                       ("quand", "When — hours & days"),
                       ("reg", "Regularity — streaks & calendar"))

    def _build_retro_page(self):
        # En-tête fixe (boutons, titre, description) + zone défilante verticale pour tout le reste :
        # les boutons restent centrés à la fenêtre quelle que soit la largeur des graphiques.
        self.retro_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                         margin_top=18, margin_start=18, margin_end=18)
        self._retro_switcher = Gtk.Box(spacing=4, css_classes=["linked"], halign=Gtk.Align.CENTER)
        header.append(self._retro_switcher)
        # Recherche par jeu : filtre TOUTE la rétrospective (stats + frise) sur les jeux
        # dont le nom contient la saisie (casse ignorée, correspondances cumulées).
        self._retro_game = ""
        self._retro_search = Gtk.SearchEntry(placeholder_text=tr("Search a game"),
                                             halign=Gtk.Align.CENTER, width_request=280)
        self._retro_search.connect("search-changed", self._on_retro_search)
        header.append(self._retro_search)
        self.retro_head_title = Gtk.Label(xalign=0, css_classes=["title-3"])
        header.append(self.retro_head_title)
        self.retro_head_desc = Gtk.Label(xalign=0, css_classes=["dim-label"])
        header.append(self.retro_head_desc)
        self.retro_page.append(header)
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                    vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
                                    hexpand=True, vexpand=True)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                          margin_start=18, margin_end=18, margin_bottom=18)
        scroll.set_child(content)
        self.retro_page.append(scroll)

        # Résumé : 5 cartes chiffrées avec comparaison à la période précédente.
        self._retro_summary_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                                          css_classes=["retro-section"])
        content.append(self._retro_summary_box)

        # Sections fixes (titre + contenu), comme le Résumé : rien à replier.
        self._retro_section_boxes = {}
        for key, title in self._RETRO_SECTIONS:
            section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                              css_classes=["retro-section"])
            section.append(Gtk.Label(label=tr(title), css_classes=["heading"], xalign=0))
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            section.append(box)
            content.append(section)
            self._retro_section_boxes[key] = box

        # Frise existante en bas de page : son propre défilement horizontal (colonnes min 120 px).
        frise_scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
                                          vscrollbar_policy=Gtk.PolicyType.NEVER)
        self._retro_cols_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8,
                                       homogeneous=True, valign=Gtk.Align.START)
        frise_scroll.set_child(self._retro_cols_box)
        content.append(frise_scroll)
        self._retro_frise_scroll = frise_scroll     # pour centrer le défilement sur aujourd'hui

        self._retro_columns = []
        self._retro_frise_centrage = []
        self._retro_buttons = {}
        self._retro_kind = "week"
        self._pie_da, self._pie_slices, self._pie_total, self._pie_hover = None, [], 0, None
        self._top_rows, self._game_rows = [], {}
        self._refresh_retro()
        return self.retro_page

    def _retro_kinds(self):
        """Périodes affichées, dans l'ordre : celles qui sont activées (toutes par défaut)."""
        return [k for k, _, _ in self._RETRO_PERIODS if P.retro_enabled(self.con, k)]

    def _rebuild_retro_switcher(self):
        while (c := self._retro_switcher.get_first_child()) is not None:
            self._retro_switcher.remove(c)
        self._retro_buttons = {}
        kinds = self._retro_kinds()
        self._retro_switcher.set_visible(len(kinds) > 1)  # une seule frise → pas de choix à proposer
        if self._retro_kind not in kinds:
            self._retro_kind = kinds[0] if kinds else "week"
        for kind in kinds:
            title = dict((k, t) for k, t, _ in self._RETRO_PERIODS)[kind]
            btn = Gtk.ToggleButton(label=tr(title))
            btn.set_active(kind == self._retro_kind)
            btn.connect("toggled", self._on_retro_select, kind)
            self._retro_switcher.append(btn)
            self._retro_buttons[kind] = btn

    def _on_retro_select(self, btn, kind):
        if not btn.get_active():
            if kind == self._retro_kind:  # impossible de tout désélectionner
                btn.set_active(True)
            return
        for k, b in self._retro_buttons.items():
            if k != kind and b.get_active():
                b.set_active(False)
        if self._retro_kind != kind:
            self._retro_kind = kind
            self._refresh_retro()

    def _retro_timeline(self, kind):
        return {"week": P.week_timeline, "month": P.month_timeline,
                "year": P.year_timeline}[kind](self.con, game=self._retro_game or None)

    def _on_retro_search(self, entry):
        """Filtre la rétrospective sur la saisie (vide = vue complète)."""
        q = entry.get_text().strip()
        if q != self._retro_game:
            self._retro_game = q
            self._refresh_retro()

    def _retro_head_text(self, kind, tl):
        fmt = dict((k, f) for k, _, f in self._RETRO_PERIODS)[kind]
        if kind == "year":
            date = str(tl["start"].year)
        elif kind == "month":
            date = P.month_name(tl["start"])  # « Octobre » / « October » (langue de l'app)
        else:
            date = tl["start"].strftime("%d/%m")
        return tr(fmt, date=date, n=tl["game_count"], time=P.fmt_minutes(tl["total_minutes"]))

    def _day_label(self, d):
        label = P.weekday_short(d)
        label = f"{label[:1].upper() + label[1:]} {d.day} {P.month_short(d)}"
        return label

    def _month_label(self, d):
        label = P.month_short(d)
        return label[:1].upper() + label[1:]

    @staticmethod
    def _clear(box):
        while (c := box.get_first_child()) is not None:
            box.remove(c)

    # ---------- comparaison à la période précédente ----------
    def _vs_label(self, kind):
        """Référence affichée dans les deltas : « vs 2025 », « vs Octobre », « vs Semaine dernière »."""
        if kind == "year":
            return str(P.previous_bounds(kind)[1].year)
        if kind == "month":
            return P.month_name(P.previous_bounds(kind)[0])
        return tr("Last week")

    @staticmethod
    def _delta(cur, prev, minutes=False, vs=None, invert=False):
        """(texte, classe CSS) du delta vs période précédente : ▲ +6 h 20 / ▼ −12 ; invert = « plus = rouge »."""
        d = cur - prev
        if d == 0:
            return "—", ["dim-label"]
        up = d > 0
        if invert:
            css = ["retro-delta-down"] if up else ["retro-delta-up"]
        else:
            css = ["retro-delta-up"] if up else ["retro-delta-down"]
        val = P.fmt_minutes(abs(d)) if minutes else str(abs(d))
        text = f"{'▲' if up else '▼'} {'+' if up else '−'}{val}"
        return (f"{text} {tr('vs {x}', x=vs)}" if vs else text), css

    # ---------- section « Résumé » : 5 cartes ----------
    def _stat_card(self, label, parts, sub, sub_css, tooltip=""):
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                       css_classes=["retro-card"], halign=Gtk.Align.FILL, vexpand=False)
        card.append(Gtk.Label(label=label, css_classes=["caption", "dim-label"]))
        vrow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0,
                       halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        for text, css in parts:
            vrow.append(Gtk.Label(label=text, css_classes=css))
        card.append(vrow)
        card.append(Gtk.Label(label=sub, css_classes=["caption"] + list(sub_css)))
        if tooltip:
            card.set_tooltip_text(tooltip)
        return card

    def _refresh_retro_summary(self, kind):
        box = self._retro_summary_box
        self._clear(box)
        box.append(Gtk.Label(label=tr("Summary"), css_classes=["heading"], xalign=0))
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, homogeneous=True, spacing=8)
        s = P.period_summary(self.con, kind, game=self._retro_game or None)
        prev, vs = s["prev"], self._vs_label(kind)
        tip = ""
        if s["longest"]:
            d = datetime.datetime.fromtimestamp(s["longest"]["start"])
            tip = tr("Longest session: {game} — {time}, {date}",
                     game=s["longest"]["name"], time=P.fmt_minutes(s["longest"]["minutes"]),
                     date=d.strftime("%d/%m %H:%M"))
        row.append(self._stat_card(tr("Total time"),
                                   [(P.fmt_minutes(s["total_minutes"]), ["title-2"])],
                                   *self._delta(s["total_minutes"], prev["total_minutes"],
                                                minutes=True, vs=vs), tooltip=tip))
        row.append(self._stat_card(tr("Sessions"), [(str(s["sessions"]), ["title-2"])],
                                   *self._delta(s["sessions"], prev["sessions"], vs=vs)))
        row.append(self._stat_card(tr("Games played"), [(str(s["games"]), ["title-2"])],
                                   *self._delta(s["games"], prev["games"], vs=vs)))
        # Jours joués : « 96 /365 » + % de la période ; le % remplace le delta si ±0.
        dsub, dcss = self._delta(s["days_played"], prev["days_played"], vs=vs)
        if s["days_played"] == prev["days_played"]:
            pct = round(s["days_played"] * 100 / s["period_days"]) if s["period_days"] else 0
            dsub, dcss = tr("{pct} % of the period", pct=pct), ["dim-label"]
        row.append(self._stat_card(tr("Days played"),
                                   [(str(s["days_played"]), ["title-2"]),
                                    (f" /{s['period_days']}", ["caption", "dim-label"])],
                                   dsub, dcss))
        row.append(self._stat_card(tr("Avg. per session"),
                                   [(P.fmt_minutes(s["avg_minutes"]), ["title-2"])],
                                   *self._delta(s["avg_minutes"], prev["avg_minutes"], minutes=True)))
        box.append(row)

    # ---------- section « Quoi » : camembert anneau + top jeux ----------
    def _refresh_retro_quoi(self, kind):
        box = self._retro_section_boxes["quoi"]
        self._clear(box)
        top = P.top_games(self.con, kind, limit=5, game=self._retro_game or None)
        vs = self._vs_label(kind)
        self._game_rows = {}
        slices = []
        for i, g in enumerate(top["top"]):
            delta_text, delta_css, delta_plain = self._rank_delta(g, vs)
            slices.append({"name": g["name"], "minutes": g["minutes"], "sessions": g["sessions"],
                           "share": g["share"], "color": PIE_COLORS[i % len(PIE_COLORS)],
                           "delta_text": delta_text, "delta_css": delta_css,
                           "delta_plain": delta_plain})
        others = top["others"]
        if others["minutes"]:
            slices.append({"name": tr("Other ({n} games)", n=others["games"]),
                           "minutes": others["minutes"], "sessions": others["sessions"],
                           "share": round(others["minutes"] * 100 / top["total_minutes"]),
                           "color": OTHER_COLOR, "delta_text": "—", "delta_css": ["dim-label"],
                           "delta_plain": tr("outside top 5")})
        self._pie_slices, self._pie_total, self._pie_hover = slices, top["total_minutes"], None
        self._top_rows = []

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16, valign=Gtk.Align.START)
        # Camembert : DrawingArea Cairo + total au centre (Overlay, non cliquable → survol inchangé).
        pie_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, halign=Gtk.Align.CENTER)
        self._pie_da = Gtk.DrawingArea(content_width=260, content_height=260)
        self._pie_da.set_draw_func(self._draw_pie)
        self._pie_da.set_has_tooltip(True)
        self._pie_da.connect("query-tooltip", self._on_pie_tooltip)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_pie_motion)
        motion.connect("leave", lambda *_: self._set_pie_hover(None))
        self._pie_da.add_controller(motion)
        overlay = Gtk.Overlay(child=self._pie_da)
        center = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, halign=Gtk.Align.CENTER,
                         valign=Gtk.Align.CENTER)
        center.set_can_target(False)  # le pointeur passe au camembert sous le total
        center.append(Gtk.Label(label=P.fmt_minutes(top["total_minutes"]), css_classes=["title-2"]))
        center.append(Gtk.Label(label=tr("Total time"), css_classes=["caption", "dim-label"]))
        overlay.add_overlay(center)
        pie_col.append(overlay)
        pie_col.append(Gtk.Label(label=tr("Time per game — hover for details"),
                                 css_classes=["caption", "dim-label"]))
        row.append(pie_col)

        # Top jeux (rang, couverture, part, évolution) — survol croisé avec le camembert.
        rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0, hexpand=True)
        if not slices:
            rows.append(Gtk.Label(label=tr("Nothing played"), css_classes=["dim-label"]))
        for i, s in enumerate(slices):
            rows.append(self._top_row(i, s))
        row.append(rows)
        box.append(row)

    @staticmethod
    def _rank_delta(g, vs):
        """Libellé d'évolution d'un rang : « ★ nouveau », « ▲ 2 », « = » ; version sans couleur pour les infobulles."""
        if g["prev_rank"] is None:
            return f"★ {tr('New')}", ["retro-delta-up"], f"★ {tr('New')} {tr('vs previous period')}"
        d = g["prev_rank"] - g["rank"]
        if d == 0:
            return "=", ["dim-label"], tr("same rank")
        text = f"{'▲' if d > 0 else '▼'} {abs(d)}"
        return text, (["retro-delta-up"] if d > 0 else ["retro-delta-down"]), \
            f"{text} {tr('vs previous period')}"

    def _top_game_row_data(self, name):
        """Ligne de jeu (pour la couverture) — mise en cache le temps d'un rafraîchissement."""
        if name not in self._game_rows:
            self._game_rows[name] = self.con.execute(
                "SELECT * FROM games WHERE name=? ORDER BY id LIMIT 1", (name,)).fetchone()
        return self._game_rows[name]

    def _top_row(self, idx, s):
        sub = f"{P.fmt_minutes(s['minutes'])} · {s['sessions']} {tr('sessions')} · {s['share']} %"
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8,
                      css_classes=["retro-row"], valign=Gtk.Align.CENTER,
                      tooltip_text=f"{s['name']}\n{sub}\n{s['delta_plain']}")
        dot = Gtk.Box(width_request=10, height_request=10, valign=Gtk.Align.CENTER,
                      css_classes=[dot_class(s["color"])])
        row.append(dot)
        g = self._top_game_row_data(s["name"])
        tex = self._cover_texture(g) if g else None
        if tex:
            row.append(Gtk.Picture(paintable=tex, content_fit=Gtk.ContentFit.COVER,
                                   can_shrink=True, width_request=34, height_request=46,
                                   valign=Gtk.Align.CENTER))
        else:
            ph = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, width_request=34, height_request=46,
                         valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER,
                         css_classes=["game-placeholder", f"ph-{idx % 6}"])
            ph.append(Gtk.Label(label=s["name"][:5], css_classes=["caption"]))
            row.append(ph)
        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0, hexpand=True)
        name = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, tooltip_text=s["name"])
        name.set_markup(GLib.markup_escape_text(s["name"]))
        labels.append(name)
        labels.append(Gtk.Label(label=sub, xalign=0, css_classes=["caption", "dim-label"]))
        row.append(labels)
        row.append(Gtk.Label(label=s["delta_text"], css_classes=["caption"] + s["delta_css"],
                             valign=Gtk.Align.CENTER))
        motion = Gtk.EventControllerMotion()
        motion.connect("enter", lambda *_: self._set_pie_hover(idx))
        motion.connect("leave", lambda *_: self._set_pie_hover(None))
        row.add_controller(motion)
        self._top_rows.append(row)
        return row

    # --- camembert : rendu Cairo, survol et infobulle ---
    def _pie_index_at(self, x, y, w, h):
        """Part survolée par le pointeur (angle + rayon), ou None (trou central / hors disque)."""
        if not self._pie_slices or not self._pie_total:
            return None
        r_out, r_in = min(w, h) / 2 - 2, (min(w, h) / 2 - 2) * 0.5
        dx, dy = x - w / 2, y - h / 2
        r = math.hypot(dx, dy)
        if not (r_in <= r <= r_out):
            return None
        ang = (math.atan2(dy, dx) + math.pi / 2) % (2 * math.pi)
        pos = ang / (2 * math.pi) * self._pie_total
        acc = 0
        for i, s in enumerate(self._pie_slices):
            acc += s["minutes"]
            if pos < acc:
                return i
        return len(self._pie_slices) - 1

    def _draw_pie(self, _area, cr, w, h):
        cx, cy = w / 2, h / 2
        r_out = min(w, h) / 2 - 2
        r_in = r_out * 0.5
        if not self._pie_slices or not self._pie_total:  # anneau vide
            cr.set_source_rgba(0.5, 0.5, 0.5, 0.15)
            cr.arc(cx, cy, (r_out + r_in) / 2, 0, 2 * math.pi)
            cr.set_line_width(r_out - r_in)
            cr.stroke()
            return
        ang, hover = -math.pi / 2, self._pie_hover
        for i, s in enumerate(self._pie_slices):
            span = 2 * math.pi * s["minutes"] / self._pie_total
            r_, g_, b_ = _hex_rgb(s["color"])
            alpha = 1.0 if (hover is None or hover == i) else 0.45
            cr.set_source_rgba(r_, g_, b_, alpha)
            cr.new_sub_path()
            cr.arc(cx, cy, r_out, ang, ang + span)
            cr.arc_negative(cx, cy, r_in, ang + span, ang)
            cr.close_path()
            cr.fill()
            ang += span

    def _on_pie_motion(self, _ctrl, x, y):
        self._set_pie_hover(self._pie_index_at(x, y, self._pie_da.get_width(),
                                               self._pie_da.get_height()))

    def _on_pie_tooltip(self, _da, x, y, _keyboard, tooltip):
        i = self._pie_index_at(x, y, self._pie_da.get_width(), self._pie_da.get_height())
        if i is None:
            return False
        s = self._pie_slices[i]
        tooltip.set_markup(f"<b>{GLib.markup_escape_text(s['name'])}</b>\n"
                           f"{P.fmt_minutes(s['minutes'])} · {s['share']} %\n"
                           f"{s['sessions']} {tr('sessions')}\n{s['delta_plain']}")
        return True

    def _set_pie_hover(self, idx):
        """Survol croisé : la part du camembert et la ligne du top surlignées ensemble."""
        if self._pie_hover == idx:
            return
        self._pie_hover = idx
        if self._pie_da:
            self._pie_da.queue_draw()
        for i, row in enumerate(self._top_rows):
            if i == idx:
                row.add_css_class("hot")
            else:
                row.remove_css_class("hot")

    # ---------- section « Quand » : histogrammes heures / jours ----------
    def _refresh_retro_quand(self, kind):
        box = self._retro_section_boxes["quand"]
        self._clear(box)
        hours = P.hours_histogram(self.con, kind, game=self._retro_game or None)
        days = P.weekdays_histogram(self.con, kind, game=self._retro_game or None)
        monday = datetime.date(2024, 1, 1)  # lundi générique (ancre fixe pour les libellés)
        letters = [P.weekday_short(monday + datetime.timedelta(days=i))[:1].upper() for i in range(7)]
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, homogeneous=True, spacing=14,
                      valign=Gtk.Align.START)
        hour_labels = [f"{h}h" if h in (0, 6, 12, 18, 23) else "" for h in range(24)]
        row.append(self._histogram(hours, tr("Hours of the day"), hour_labels,
                                   [f"{h} h" for h in range(24)]))
        row.append(self._histogram(days, tr("Weekdays"), letters,
                                   [P.weekday_name(monday + datetime.timedelta(days=i))
                                    for i in range(7)], gap=5))
        box.append(row)
        # Pic : heure et jour les plus actifs (en minutes jouées).
        if any(b["minutes"] for b in hours["current"]) and any(b["minutes"] for b in days["current"]):
            peak_h = max(range(24), key=lambda i: hours["current"][i]["minutes"])
            peak_d = max(range(7), key=lambda i: days["current"][i]["minutes"])
            box.append(Gtk.Label(
                label=tr("Peak: {hour} on {day}", hour=f"{peak_h} h",
                         day=P.weekday_name(monday + datetime.timedelta(days=peak_d))),
                xalign=0, css_classes=["caption", "dim-label"]))

    def _histogram(self, data, title, axis_labels, names, gap=2):
        """Histogramme à barres (hauteur ∝ minutes jouées) ; l'axe porte 1 libellé par barre."""
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        col.append(Gtk.Label(label=title, xalign=0, css_classes=["caption", "dim-label"]))
        cur, prev = data["current"], data["previous"]
        maxm = max([b["minutes"] for b in cur] + [1])
        bars = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, homogeneous=True, spacing=gap,
                       height_request=90, valign=Gtk.Align.END)
        for i, b in enumerate(cur):
            h = max(2, round(90 * b["minutes"] / maxm)) if b["minutes"] else 2
            bar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, height_request=h, hexpand=True,
                          valign=Gtk.Align.END, css_classes=["retro-bar"],
                          tooltip_text=f"{names[i]} · {b['minutes'] and P.fmt_minutes(b['minutes']) or '0 min'} "
                                       f"· {b['sessions']} {tr('sessions')}\n"
                                       f"{tr('vs prev.: {n} sessions', n=prev[i]['sessions'])}")
            if b["minutes"] and b["minutes"] == maxm:
                bar.add_css_class("peak")
            motion = Gtk.EventControllerMotion()
            # Arity-proof : « enter » porte (x, y) dans GTK 4.24 → 3 args en Python ;
            # le lambda à 2 params plantait à chaque survol (jamais de « hot »).
            motion.connect("enter", lambda ctrl, *_: ctrl.get_widget().add_css_class("hot"))
            motion.connect("leave", lambda ctrl, *_: ctrl.get_widget().remove_css_class("hot"))
            bar.add_controller(motion)
            bars.append(bar)
        col.append(bars)
        axis = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, homogeneous=True, spacing=gap)
        for lab in axis_labels:
            axis.append(Gtk.Label(label=lab, css_classes=["caption", "dim-label"]))
        col.append(axis)
        return col

    # ---------- section « Régularité » : séries + calendrier ----------
    def _refresh_retro_regularity(self, kind):
        box = self._retro_section_boxes["reg"]
        self._clear(box)
        r = P.regularity(self.con, kind, game=self._retro_game or None)
        prev, vs = r["prev"], self._vs_label(kind)
        cards = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, homogeneous=True, spacing=8)
        dsub, dcss = self._delta(r["days_played"], prev["days_played"], vs=vs)
        if r["days_played"] == prev["days_played"]:
            pct = round(r["days_played"] * 100 / r["period_days"]) if r["period_days"] else 0
            dsub, dcss = tr("{pct} % of the period", pct=pct), ["dim-label"]
        cards.append(self._stat_card(tr("Days played"),
                                     [(str(r["days_played"]), ["title-3"]),
                                      (f" /{r['period_days']}", ["caption", "dim-label"])],
                                     dsub, dcss))
        cards.append(self._stat_card(tr("Current streak"),
                                     [(f"{r['streak']} {tr('d')}", ["title-3"])],
                                     f"{vs} : {prev['streak']} {tr('d')}", ["dim-label"]))
        cards.append(self._stat_card(tr("Longest streak"),
                                     [(f"{r['longest_streak']} {tr('d')}", ["title-3"])],
                                     *self._delta(r["longest_streak"], prev["longest_streak"])))
        # Plus long arrêt : une augmentation est mauvaise → delta à l'envers (rouge si +).
        cards.append(self._stat_card(tr("Longest break"),
                                     [(f"{r['longest_gap']} {tr('d')}", ["title-3"])],
                                     *self._delta(r["longest_gap"], prev["longest_gap"], invert=True)))
        box.append(cards)

        cells = P.heatmap(self.con, kind, game=self._retro_game or None)
        maxm = max([(c["minutes"] or 0) for c in cells] + [1])
        start, end = P.period_bounds(kind)
        if kind == "year":
            x = str(start.year)
        elif kind == "month":
            x = P.month_name(start)
        else:
            x = f"{start.day}–{end.day} {P.month_short(end)}"
        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        head.append(Gtk.Label(label=tr("Calendar {x}", x=x), xalign=0,
                              css_classes=["caption", "dim-label"], hexpand=True))
        legend = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4,
                         valign=Gtk.Align.CENTER)
        legend.append(Gtk.Label(label=tr("Less"), css_classes=["caption", "dim-label"]))
        for lvl in range(5):
            sw = Gtk.Box(width_request=10, height_request=10, css_classes=[f"retro-hm-{lvl}"])
            legend.append(sw)
        legend.append(Gtk.Label(label=tr("More"), css_classes=["caption", "dim-label"]))
        head.append(legend)
        box.append(head)
        if kind == "week":
            # Modèle annuel : carrés 32 px, grille centrée, jour de la semaine
            # attaché SOUS chaque carré (dans la grille → alignement exact).
            grid = Gtk.Grid(column_spacing=2, row_spacing=2, column_homogeneous=True)
            for i, c in enumerate(cells):
                grid.attach(self._heat_cell(c, maxm, 32), i, 0, 1, 1)
                grid.attach(Gtk.Label(label=P.weekday_short(c["date"]), xalign=0.5,
                                      css_classes=["caption", "dim-label"]), i, 1, 1, 1)
            grid.set_halign(Gtk.Align.CENTER)
            box.append(grid)
        elif kind == "month":
            grid = Gtk.Grid(column_spacing=2, row_spacing=2, column_homogeneous=True)
            first = start
            for c in cells:
                d = c["date"]
                grid.attach(self._heat_cell(c, maxm, 32), d.weekday(),
                            (first.weekday() + d.day - 1) // 7, 1, 1)
            # Jours de la semaine sous LEUR colonne, comme les mois sous la grille
            # annuelle : attachés DANS la grille — une liste séparée se décalerait.
            bas = max((first.weekday() + c["date"].day - 1) // 7 for c in cells) + 1
            for j in range(7):
                ex = next(c["date"] for c in cells if c["date"].weekday() == j)
                grid.attach(Gtk.Label(label=P.weekday_short(ex), xalign=0.5,
                                      css_classes=["caption", "dim-label"]), j, bas, 1, 1)
            grid.set_halign(Gtk.Align.CENTER)
            box.append(grid)
        else:
            outer = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
                                       vscrollbar_policy=Gtk.PolicyType.NEVER)
            # Marge basse DANS le ScrolledWindow : la barre flottante (overlay)
            # se pose sur le bas de la vue — sans marge elle recouvrait les mois.
            vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4,
                           margin_bottom=16)
            jan1 = start
            auj = datetime.date.today()
            cell_auj = None
            grid = Gtk.Grid(column_spacing=2, row_spacing=2)
            for c in cells:
                d = c["date"]
                col = (d.timetuple().tm_yday - 1 + jan1.weekday()) // 7
                # Même taille de carré que les heatmaps semaine/mois (32 px) :
                # la grille dépasse alors la section, le ScrolledWindow défile.
                celule = self._heat_cell(c, maxm, 32)
                grid.attach(celule, col, d.weekday(), 1, 1)
                if d == auj:
                    cell_auj = celule
            # Les mois sont ATTACHÉS à la grille (ligne sous les cellules), à la
            # colonne du 1er jour du mois et jusqu'à celle du mois suivant : une
            # liste séparée, étalée en 12 parts égales sur toute la largeur, ne
            # tombait jamais sur les vraies positions (jusqu'à 354 px d'écart en
            # fin d'année). Texte à gauche : le libellé démarre pile sur le mois.
            dernier = (datetime.date(jan1.year, 12, 31).timetuple().tm_yday - 1
                       + jan1.weekday()) // 7
            for m in range(1, 13):
                d = datetime.date(jan1.year, m, 1)
                col = (d.timetuple().tm_yday - 1 + jan1.weekday()) // 7
                suiv = ((datetime.date(jan1.year, m + 1, 1).timetuple().tm_yday - 1
                         + jan1.weekday()) // 7 if m < 12 else dernier + 1)
                lab = Gtk.Label(label=P.month_short(d), xalign=0,
                                css_classes=["caption", "dim-label"])
                grid.attach(lab, col, 7, suiv - col, 1)
            # Centrée quand elle tient dans la section ; sous la largeur, le
            # ScrolledWindow prend le relais.
            grid.set_halign(Gtk.Align.CENTER)
            vbox.append(grid)
            outer.set_child(vbox)
            box.append(outer)
            # Défilement initial centré sur aujourd'hui (si la grille déborde).
            if cell_auj is not None:
                self._centrer_scroll_h(outer, lambda: (cell_auj.get_allocation().x,
                                                       cell_auj.get_allocation().width))

    @staticmethod
    def _heat_tooltip(c):
        """Infobulle d'une case : date, total, sessions et jeux joués ; None si jour futur."""
        if c["minutes"] is None:
            return None
        date_s = f"{c['date'].day} {P.month_name(c['date'])} {c['date'].year}"
        if not c["minutes"]:
            return f"{date_s}\n{tr('Nothing played')}"
        lines = [f"{date_s} · {P.fmt_minutes(c['minutes'])}",
                 f"{c['sessions']} {tr('sessions')}"]
        lines += [f"{g['name']} — {P.fmt_minutes(g['minutes'])}" for g in c["games"]]
        return "\n".join(lines)

    @staticmethod
    def _heat_cell(c, maxm, size):
        """Cellule du calendrier : carré `size`×`size` ; niveau d'intensité relatif au
        max ; jour futur = case neutre."""
        m = c["minutes"]
        cell = Gtk.Box(width_request=size, height_request=size, valign=Gtk.Align.CENTER)
        if m is None:
            cell.add_css_class("retro-future")
            return cell
        lvl = max(1, math.ceil(4 * m / maxm)) if m else 0
        cell.add_css_class(f"retro-hm-{lvl}")
        cell.set_tooltip_text(RetroMixin._heat_tooltip(c))
        return cell

    @staticmethod
    def _centrer_scroll_h(sw, cible):
        """Défilement horizontal initial : place l'objet rendu par `cible()` — callable
        (x, largeur) — au milieu de la vue. On ne touche à l'ajustement que depuis un
        signal : une tentative synchrone verrait l'allocation FANTÔME que GTK donne aux
        enfants fraîchement joints (x=-6, w=12) et l'`upper` périmé du contenu précédent.
        `page-size`/`upper` sont émis APRÈS l'allocation — soit au premier affichage de
        la page de la pile (encore invisible à la construction), soit au premier frame
        suivant un rebuild. La REGULATION elle-même est repoussée en idle : régler la
        valeur pendant le layout est avalé (la viewport ne retranslate jamais — GTK 4).
        Contenu plus étroit que la vue : la valeur reste bornée à 0. Un rafraîchissement
        qui ne change pas la largeur n'émet rien : le défilement en cours est préservé."""
        adj = sw.get_hadjustment()

        def centrer():
            page, upper = adj.get_page_size(), adj.get_upper()
            x, w = cible()
            if page > 0 and upper > 0 and w > 0:
                adj.set_value(max(adj.get_lower(),
                                  min(x + w / 2 - page / 2, upper - page)))
            return False

        def appliquer(*_):
            page, upper = adj.get_page_size(), adj.get_upper()
            x, w = cible()
            if page <= 0 or upper <= 0 or w <= 0:
                return False                 # allocation encore fantôme : on attend
            GLib.idle_add(centrer)           # hors du layout, sinon c'est avalé
            for cid in ids:
                if GObject.signal_handler_is_connected(adj, cid):
                    adj.disconnect(cid)
            return False

        ids = [adj.connect("notify::page-size", appliquer),
               adj.connect("notify::upper", appliquer)]
        return ids

    # ---------- frise existante ----------
    def _refresh_retro_frise(self, kind, tl):
        while (c := self._retro_cols_box.get_first_child()) is not None:
            self._retro_cols_box.remove(c)
        self._retro_columns = []
        columns = tl["months"] if kind == "year" else tl["days"]
        for col in columns:
            future = col["date"] > tl["today"]
            if kind == "year":
                # colonnes = 1er du mois : le mois en cours est le « jour » d'aujourd'hui
                label = self._month_label(col["date"])
                is_today = col["date"].month == tl["today"].month and col["date"].year == tl["today"].year
            elif kind == "month":
                label = str(col["date"].day)  # un jour du mois porte son numéro
                is_today = col["date"] == tl["today"]
            else:
                label = self._day_label(col["date"])
                is_today = col["date"] == tl["today"]
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0,
                          width_request=120, valign=Gtk.Align.START, css_classes=["retro-col"])
            header = Gtk.Label(label=label, css_classes=["heading"])
            box.append(header)
            entries = []
            if future:
                box.append(Gtk.Label(label=tr("Upcoming"), css_classes=["dim-label"]))
            elif not col["entries"]:
                box.append(Gtk.Label(label=tr("Nothing played"), css_classes=["dim-label"]))
            else:
                for e in col["entries"]:
                    cell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["retro-cell"])
                    start_dt = datetime.datetime.fromtimestamp(e["start"])
                    end_dt = datetime.datetime.fromtimestamp(e.get("end", e["start"] + e["minutes"] * 60))
                    if kind == "year":
                        t_label = f"{start_dt.strftime('%d/%m %H:%M')} → {end_dt.strftime('%H:%M')}"
                    else:
                        t_label = f"{start_dt.strftime('%H:%M')} → {end_dt.strftime('%H:%M')}"
                    t = Gtk.Label(label=t_label, xalign=0, css_classes=["retro-time", "dim-label"],
                                  tooltip_text=t_label)
                    name = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END,
                                     tooltip_text=e["name"])
                    name.set_markup(GLib.markup_escape_text(e["name"]))
                    cell.append(t)
                    cell.append(name)
                    box.append(cell)
                    entries.append({"time": t, "name": name, "minutes": e["minutes"]})
            if is_today:
                box.add_css_class("today")
            self._retro_cols_box.append(box)
            self._retro_columns.append({"date": col["date"], "today": is_today,
                                        "box": box, "header": header, "entries": entries})
        # Défilement initial centré sur aujourd'hui : les rappels d'attente des
        # rafraîchissements successifs sont débranchés, seul le dernier compte.
        for cid in self._retro_frise_centrage:
            if GObject.signal_handler_is_connected(self._retro_frise_scroll.get_hadjustment(), cid):
                self._retro_frise_scroll.get_hadjustment().disconnect(cid)
        self._retro_frise_centrage = []
        col_auj = next((c["box"] for c in self._retro_columns if c["today"]), None)
        if col_auj is not None:
            self._retro_frise_centrage = self._centrer_scroll_h(
                self._retro_frise_scroll,
                lambda: (col_auj.get_allocation().x, col_auj.get_allocation().width))

    # ---------- rafraîchissement global ----------
    def _refresh_retro(self):
        self._rebuild_retro_switcher()
        kind = self._retro_kind
        tl = self._retro_timeline(kind)
        self.retro_head_title.set_text(tr(dict((k, t) for k, t, _ in self._RETRO_PERIODS)[kind]))
        self.retro_head_desc.set_text(self._retro_head_text(kind, tl))
        self._refresh_retro_summary(kind)
        self._refresh_retro_quoi(kind)
        self._refresh_retro_quand(kind)
        self._refresh_retro_regularity(kind)
        self._refresh_retro_frise(kind, tl)

    def _hide_short_label(self, minutes):
        """Titre du réglage selon le seuil (0 = on montre tout)."""
        minutes = int(minutes)
        return tr("Show every session") if minutes <= 0 else tr("Hide sessions under {n} min", n=minutes)

    def _on_hide_short(self, minutes):
        self.hide_short_min = max(0, min(15, int(minutes)))
        P.set_setting(self.con, "retro_hide_min", str(self.hide_short_min))
        self._refresh_retro()

    def _refresh_stats(self):
        P.refresh_stats(self.con)
        if self.stack.get_visible_child_name() == "retro":
            self._refresh_retro()
