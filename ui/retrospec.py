"""retrospec.py — page « Rétrospective » : frise de la semaine et statistiques."""
import datetime, time

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, GObject, Gtk, Pango

import picker as P
from i18n import available, get_lang, tr


class RetroMixin:

    # ---------- page « Rétrospective » : frise de la semaine ----------
    def _build_retro_page(self):
        self.retro_page = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
                                             vscrollbar_policy=Gtk.PolicyType.AUTOMATIC)
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                        margin_top=18, margin_bottom=18, margin_start=18, margin_end=18)
        inner.append(Gtk.Label(label=tr("This week"), xalign=0, css_classes=["title-3"]))
        self.retro_head_desc = Gtk.Label(xalign=0, css_classes=["dim-label"])
        inner.append(self.retro_head_desc)
        self._retro_cols_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8,
                                       homogeneous=True, valign=Gtk.Align.START)
        inner.append(self._retro_cols_box)
        self.retro_page.set_child(inner)
        self._retro_columns = []
        self._refresh_retro()
        return self.retro_page

    def _day_label(self, d, today):
        if d == today:
            return tr("Today")
        if d == today - datetime.timedelta(days=1):
            return tr("Yesterday")
        label = d.strftime("%a %d %b")
        return label[:1].upper() + label[1:]

    def _refresh_retro(self):
        while (c := self._retro_cols_box.get_first_child()) is not None:
            self._retro_cols_box.remove(c)
        self._retro_columns = []
        week = P.week_timeline(self.con)
        self.retro_head_desc.set_text(tr("Week of {date} · {n} game(s) · {time}",
                                         date=week["start"].strftime("%d/%m"), n=week["game_count"],
                                         time=P.fmt_minutes(week["total_minutes"])))
        for day in week["days"]:
            future = day["date"] > week["today"]
            is_today = day["date"] == week["today"]
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0,
                          width_request=120, valign=Gtk.Align.START, css_classes=["retro-col"])
            header = Gtk.Label(label=self._day_label(day["date"], week["today"]),
                               css_classes=["heading"])
            col.append(header)
            entries = []
            if future:
                col.append(Gtk.Label(label=tr("Upcoming"), css_classes=["dim-label"]))
            elif not day["entries"]:
                col.append(Gtk.Label(label=tr("Nothing played"), css_classes=["dim-label"]))
            else:
                for e in day["entries"]:
                    cell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["retro-cell"])
                    t = Gtk.Label(label=time.strftime("%H:%M", time.localtime(e["start"])),
                                  xalign=0, css_classes=["caption", "dim-label"])
                    name = Gtk.Label(label=GLib.markup_escape_text(e["name"]), xalign=0,
                                     ellipsize=Pango.EllipsizeMode.END, tooltip_text=e["name"])
                    cell.append(t)
                    cell.append(name)
                    col.append(cell)
                    entries.append({"time": t, "name": name, "minutes": e["minutes"]})
            if is_today:
                col.add_css_class("today")
            self._retro_cols_box.append(col)
            self._retro_columns.append({"date": day["date"], "today": is_today,
                                        "box": col, "header": header, "entries": entries})

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
