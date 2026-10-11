"""retrospec.py — page « Rétrospective » : frises semaine / mois / année et statistiques."""
import datetime, time

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, GObject, Gtk, Pango

import picker as P
from i18n import available, get_lang, tr


class RetroMixin:

    # ---------- page « Rétrospective » : frise au choix (semaine, mois, année) ----------
    _RETRO_PERIODS = (("week", "This week", "Week of {date} · {n} game(s) · {time}"),
                      ("month", "This month", "Month of {date} · {n} game(s) · {time}"),
                      ("year", "This year", "Year {date} · {n} game(s) · {time}"))

    def _build_retro_page(self):
        # En-tête fixe (boutons, titre, description) + zone défilante pour la frise :
        # ainsi les boutons restent centrés à la fenêtre, quelle que soit la largeur de la frise.
        self.retro_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                         margin_top=18, margin_start=18, margin_end=18)
        self._retro_switcher = Gtk.Box(spacing=4, css_classes=["linked"], halign=Gtk.Align.CENTER)
        header.append(self._retro_switcher)
        self.retro_head_title = Gtk.Label(xalign=0, css_classes=["title-3"])
        header.append(self.retro_head_title)
        self.retro_head_desc = Gtk.Label(xalign=0, css_classes=["dim-label"])
        header.append(self.retro_head_desc)
        self.retro_page.append(header)
        self._retro_scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
                                                vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
                                                hexpand=True, vexpand=True)
        self._retro_cols_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8,
                                       homogeneous=True, valign=Gtk.Align.START,
                                       margin_bottom=18, margin_start=18, margin_end=18)
        self._retro_scroll.set_child(self._retro_cols_box)
        self.retro_page.append(self._retro_scroll)
        self._retro_columns = []
        self._retro_buttons = {}
        self._retro_kind = "week"
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
                "year": P.year_timeline}[kind](self.con)

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

    def _refresh_retro(self):
        self._rebuild_retro_switcher()
        while (c := self._retro_cols_box.get_first_child()) is not None:
            self._retro_cols_box.remove(c)
        self._retro_columns = []
        kind = self._retro_kind
        tl = self._retro_timeline(kind)
        self.retro_head_title.set_text(tr(dict((k, t) for k, t, _ in self._RETRO_PERIODS)[kind]))
        self.retro_head_desc.set_text(self._retro_head_text(kind, tl))
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
                    t_label = time.strftime("%H:%M", time.localtime(e["start"]))
                    if e["minutes"]:
                        t_label += f" · {P.fmt_minutes(e['minutes'])}"
                    t = Gtk.Label(label=t_label, xalign=0,
                                  css_classes=["caption", "dim-label"], tooltip_text=t_label)
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