#!/usr/bin/env python3
"""export_draw.py — rétrospective dessinée avec Cairo (PNG et PDF).

La mise en page est une pile de BLOCS (en-tête, cartes, top, graphiques, calendrier, frise) :
chacun se dessine à une largeur logique fixe (`CW`) et renvoie sa hauteur. Le PNG les empile sur
une seule image ; le PDF les répartit sur des pages A4 sans jamais couper un bloc en deux.

Le texte passe par PangoCairo quand PyGObject est là (c'est le cas de l'application : accents,
CJK, emoji, points de suspension propres) ; sans lui — un script nu, la CI — Cairo « toy text »
prend le relais : même mise en page, polices plus simples.
"""
import math

import cairo

from i18n import tr
from . import stats as S
from .export import PIE_COLORS, pie_slices, delta, heat_level, rank_delta, weekday_labels

try:
    import gi
    gi.require_version("Pango", "1.0")
    gi.require_version("PangoCairo", "1.0")
    from gi.repository import Pango, PangoCairo
    HAVE_PANGO = True
except (ImportError, ValueError):
    HAVE_PANGO = False

try:
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf
    HAVE_PIXBUF = True
except (NameError, ImportError, ValueError):
    HAVE_PIXBUF = False

CW = 920                      # largeur utile (unités logiques) ; la page ajoute PAD de chaque côté
PAD = 40
GAP = 14
FONT = "Sans"
THEMES = {
    "dark": {"bg": "#1e1e24", "card": "#2a2a33", "line": "#3a3a45", "text": "#f2f2f5", "dim": "#9a9aa8",
             "up": "#57c785", "down": "#e5645f", "accent": "#8e6fd1",
             "heat": ["#2a2a33", "#3d3558", "#55447f", "#7358a8", "#9a78d8"]},
    "light": {"bg": "#ffffff", "card": "#f1f1f5", "line": "#dcdce4", "text": "#1c1c22", "dim": "#6a6a78",
              "up": "#1f8f4d", "down": "#c0392b", "accent": "#614d8f",
              "heat": ["#ececf2", "#d9d0ee", "#bba9e0", "#8e73c4", "#614d8f"]},
}
A4 = (595.2756, 841.8898)
A4_MARGIN = 28.0
MAX_LINES = 6                 # jeux listés par colonne de la frise (les autres : « +N »)


def rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


# ---------- primitives ----------

def text(cr, s, x, y, size, color, bold=False, width=None, align="left"):
    """Une ligne de texte posée en (x, y) = coin haut-gauche ; renvoie sa hauteur.

    `width` borne la ligne (points de suspension) ; `align` : left | right | center DANS cette largeur."""
    cr.set_source_rgb(*rgb(color))
    if HAVE_PANGO:
        layout = PangoCairo.create_layout(cr)
        fd = Pango.FontDescription()
        fd.set_family(FONT)
        fd.set_weight(Pango.Weight.BOLD if bold else Pango.Weight.NORMAL)
        fd.set_absolute_size(size * Pango.SCALE)
        layout.set_font_description(fd)
        layout.set_text(s, -1)
        if width:
            layout.set_width(int(width * Pango.SCALE))
            layout.set_ellipsize(Pango.EllipsizeMode.END)
            layout.set_alignment({"left": Pango.Alignment.LEFT, "right": Pango.Alignment.RIGHT,
                                  "center": Pango.Alignment.CENTER}[align])
        cr.move_to(x, y)
        PangoCairo.show_layout(cr, layout)
        return layout.get_pixel_size()[1]
    cr.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD if bold else cairo.FONT_WEIGHT_NORMAL)
    cr.set_font_size(size)
    if width:
        while s and cr.text_extents(s).x_advance > width:
            s = s[:-2] + "…" if len(s) > 1 else ""
    ext = cr.text_extents(s)
    dx = {"left": 0, "right": (width or ext.x_advance) - ext.x_advance,
          "center": ((width or ext.x_advance) - ext.x_advance) / 2}[align]
    fe = cr.font_extents()
    cr.move_to(x + dx, y + fe[0])
    cr.show_text(s)
    return size * 1.25


def rrect(cr, x, y, w, h, r, color):
    r = min(r, w / 2, h / 2)
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()
    cr.set_source_rgb(*rgb(color))
    cr.fill()


def cover_surface(path, w, h):
    """Jaquette réduite en surface Cairo (PNG direct ; JPEG & co via GdkPixbuf), ou None."""
    try:
        if str(path).lower().endswith(".png"):
            src = cairo.ImageSurface.create_from_png(str(path))
            out = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
            c = cairo.Context(out)
            c.scale(w / src.get_width(), h / src.get_height())
            c.set_source_surface(src, 0, 0)
            c.get_source().set_filter(cairo.FILTER_BEST)
            c.paint()
            return out
        if HAVE_PIXBUF:
            pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(path), w, h, False)
            return _pixbuf_surface(pb)
        from PIL import Image                       # repli sans PyGObject : JPEG, WebP…
        im = Image.open(path).convert("RGBA").resize((w, h), Image.LANCZOS)
        r, g, b, a = im.split()
        data = bytearray(Image.merge("RGBA", (b, g, r, a)).tobytes())
        for i in range(0, len(data), 4):            # Cairo attend de l'alpha prémultiplié
            al = data[i + 3]
            if al != 255:
                data[i], data[i + 1], data[i + 2] = (data[i] * al // 255, data[i + 1] * al // 255,
                                                     data[i + 2] * al // 255)
        return cairo.ImageSurface.create_for_data(data, cairo.FORMAT_ARGB32, w, h, w * 4)
    except Exception:
        return None
    return None


def _pixbuf_surface(pb):
    w, h, stride, n = pb.get_width(), pb.get_height(), pb.get_rowstride(), pb.get_n_channels()
    px, alpha = pb.get_pixels(), pb.get_has_alpha()
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    buf = surf.get_data()
    for y in range(h):
        for x in range(w):
            i = y * stride + x * n
            r, g, b = px[i], px[i + 1], px[i + 2]
            a = px[i + 3] if alpha else 255
            o = y * surf.get_stride() + x * 4
            buf[o:o + 4] = bytes((b * a // 255, g * a // 255, r * a // 255, a))   # ARGB32 prémultiplié, petit-boutiste
    surf.mark_dirty()
    return surf


# ---------- blocs ----------
# Un bloc : f(cr, th, d, y) → hauteur. Dessiné à x = 0 ; l'appelant place le contexte.

def b_header(cr, th, d, y):
    h = text(cr, "LUDODEX · " + tr("Retrospective").upper(), 0, y, 13, th["dim"])
    h += text(cr, d["title"], 0, y + h + 2, 34, th["text"], bold=True)
    sub = d["head"] + (f" · {tr('Game')}: {d['game']}" if d["game"] else "")
    h += text(cr, sub, 0, y + h + 4, 15, th["dim"], width=CW)
    return h + 14


def _heading(cr, th, label, y):
    return text(cr, label, 0, y, 19, th["text"], bold=True) + 10


def _cards_row(cr, th, items, y):
    """Rangée de cartes (label, valeur, petit, (delta, sens)) en colonnes égales."""
    n = len(items)
    w = (CW - GAP * (n - 1)) / n
    for i, (label, value, small, (dtxt, cls)) in enumerate(items):
        x = i * (w + GAP)
        rrect(cr, x, y, w, 96, 12, th["card"])
        text(cr, label, x, y + 10, 12, th["dim"], width=w, align="center")
        vw = text(cr, value + (" " + small if small else ""), x, y + 30, 25, th["text"], bold=True,
                  width=w, align="center")
        text(cr, dtxt, x + 6, y + 30 + vw + 6, 11, th[{"up": "up", "down": "down"}.get(cls, "dim")],
             width=w - 12, align="center")
    return 96 + GAP


def b_summary(cr, th, d, y):
    s, prev, vs = d["summary"], d["summary"]["prev"], d["vs"]
    pct = round(s["days_played"] * 100 / s["period_days"]) if s["period_days"] else 0
    h = _heading(cr, th, tr("Summary"), y)
    items = [
        (tr("Total time"), S.fmt_minutes(s["total_minutes"]), "", delta(s["total_minutes"], prev["total_minutes"], True, vs)),
        (tr("Sessions"), str(s["sessions"]), "", delta(s["sessions"], prev["sessions"], vs=vs)),
        (tr("Games played"), str(s["games"]), "", delta(s["games"], prev["games"], vs=vs)),
        (tr("Days played"), str(s["days_played"]), f"/{s['period_days']}",
         delta(s["days_played"], prev["days_played"], vs=vs) if s["days_played"] != prev["days_played"]
         else (tr("{pct} % of the period", pct=pct), "flat")),
        (tr("Avg. per session"), S.fmt_minutes(s["avg_minutes"]), "", delta(s["avg_minutes"], prev["avg_minutes"], True)),
    ]
    return h + _cards_row(cr, th, items, y + h)


def _pie_row(cr, th, d, y):
    """Carte « camembert » : anneau (total au centre) à gauche, légende à droite. Renvoie sa hauteur
    + l'espacement ; 0 s'il n'y a rien à tracer."""
    slices, total = pie_slices(d), d["top"]["total_minutes"]
    if not slices or not total:
        return 0
    hgt = max(220, 28 * len(slices) + 24)
    rrect(cr, 0, y, CW, hgt, 12, th["card"])
    cx, cy, r_out = 24 + 90, y + hgt / 2, 90
    r_in = r_out * 0.5
    ang = -math.pi / 2
    for s_ in slices:
        span = 2 * math.pi * s_["minutes"] / total
        cr.new_sub_path()
        cr.arc(cx, cy, r_out, ang, ang + span)
        cr.arc_negative(cx, cy, r_in, ang + span, ang)
        cr.close_path()
        cr.set_source_rgb(*rgb(s_["color"]))
        cr.fill()
        ang += span
    text(cr, S.fmt_minutes(total), cx - r_in, cy - 10, 18, th["text"], bold=True, width=2 * r_in, align="center")
    lx, step = 24 + 180 + 36, 28
    top_y = y + (hgt - step * len(slices)) / 2
    for i, s_ in enumerate(slices):
        ly = top_y + i * step
        cr.arc(lx + 6, ly + 10, 6, 0, 2 * math.pi)
        cr.set_source_rgb(*rgb(s_["color"]))
        cr.fill()
        text(cr, s_["name"], lx + 22, ly + 2, 14, th["text"], width=CW - lx - 240)
        text(cr, f"{S.fmt_minutes(s_['minutes'])} · {s_['share']} %", CW - 210, ly + 3, 13, th["dim"],
             width=196, align="right")
    return hgt + GAP


def b_top(cr, th, d, y):
    h = _heading(cr, th, tr("What — time breakdown"), y)
    top = d["top"]["top"]
    if not top:
        return h + text(cr, tr("Nothing played"), 0, y + h, 14, th["dim"]) + GAP
    ph = _pie_row(cr, th, d, y + h)
    h += ph
    row_h = 74
    for i, g in enumerate(top):
        ry = y + h + i * (row_h + 8)
        rrect(cr, 0, ry, CW, row_h, 12, th["card"])
        text(cr, str(g["rank"]), 0, ry + 24, 18, th["dim"], bold=True, width=44, align="center")
        surf = cover_surface(d["covers"][g["name"]], 44, 62) if g["name"] in d["covers"] else None
        if surf is not None:
            cr.set_source_surface(surf, 52, ry + 6)
            cr.paint()
        else:
            rrect(cr, 52, ry + 6, 44, 62, 6, th["line"])
        x = 110
        rd, cls = rank_delta(g)
        text(cr, rd, CW - 130, ry + 12, 13, th[{"up": "up", "down": "down"}.get(cls, "dim")],
             width=118, align="right")
        text(cr, g["name"], x, ry + 10, 17, th["text"], bold=True, width=CW - x - 150)
        text(cr, f"{S.fmt_minutes(g['minutes'])} · {g['sessions']} {tr('sessions')} · {g['share']} %",
             x, ry + 34, 13, th["dim"], width=CW - x - 150)
        bw = CW - x - 24
        rrect(cr, x, ry + 58, bw, 6, 3, th["line"])
        rrect(cr, x, ry + 58, max(8, bw * g["share"] / 100), 6, 3, PIE_COLORS[i % len(PIE_COLORS)])
    n = len(top)
    o = d["top"]["others"]
    extra = 0
    if o["games"]:
        text(cr, f"{tr('Other ({n} games)', n=o['games'])} · {S.fmt_minutes(o['minutes'])}",
             0, y + h + n * (row_h + 8) + 2, 13, th["dim"])
        extra = 24
    return h + n * (row_h + 8) + extra + GAP


def _bar_chart(cr, th, x, y, w, data, labels, title, every):
    hgt = 190
    rrect(cr, x, y, w, hgt, 12, th["card"])
    text(cr, title, x + 14, y + 10, 14, th["text"], bold=True)
    peak = max([b["minutes"] for b in data] + [1])
    area_x, area_y, area_w, area_h = x + 14, y + 40, w - 28, 108
    slot = area_w / len(data)
    for i, b in enumerate(data):
        bh = max(1.5, area_h * b["minutes"] / peak) if b["minutes"] else 1.5
        rrect(cr, area_x + i * slot + slot * 0.12, area_y + area_h - bh, slot * 0.76, bh, 2,
              th["accent"] if b["minutes"] else th["line"])
        if i % every == 0:
            text(cr, labels[i], area_x + i * slot - slot * 0.5, area_y + area_h + 6, 10, th["dim"],
                 width=slot * 2, align="center")
    return hgt


def b_when(cr, th, d, y):
    h = _heading(cr, th, tr("When — hours & days"), y)
    w1 = CW * 0.58 - GAP / 2
    w2 = CW - w1 - GAP
    hours = [f"{i:02d}h" for i in range(24)]
    hh = _bar_chart(cr, th, 0, y + h, w1, d["hours"], hours, tr("Hours"), 3)
    _bar_chart(cr, th, w1 + GAP, y + h, w2, d["weekdays"], weekday_labels(), tr("Weekdays"), 1)
    return h + hh + GAP


def b_regularity(cr, th, d, y):
    r = d["regularity"]
    vs = d["vs"]
    h = _heading(cr, th, tr("Regularity — streaks & calendar"), y)
    items = [
        (tr("Days played"), str(r["days_played"]), f"/{r['period_days']}",
         delta(r["days_played"], r["prev"]["days_played"], vs=vs)),
        (tr("Current streak"), f"{r['streak']} {tr('d')}", "", (f"{vs} : {r['prev']['streak']} {tr('d')}", "flat")),
        (tr("Longest streak"), f"{r['longest_streak']} {tr('d')}", "",
         delta(r["longest_streak"], r["prev"]["longest_streak"])),
        (tr("Longest break"), f"{r['longest_gap']} {tr('d')}", "",
         delta(r["longest_gap"], r["prev"]["longest_gap"], invert=True)),
    ]
    h += _cards_row(cr, th, items, y + h)
    cells = d["heatmap"]
    maxm = max([(c["minutes"] or 0) for c in cells] + [1])
    if d["kind"] == "week":      # 7 jours : une rangée étiquetée plutôt qu'une colonne maigre
        w = (CW - GAP * 6) / 7
        for i, c in enumerate(cells):
            lvl = heat_level(c["minutes"], maxm)
            cx = i * (w + GAP)
            if lvl is None:
                cr.set_source_rgb(*rgb(th["line"]))
                cr.set_line_width(1)
                cr.rectangle(cx + .5, y + h + .5, w - 1, 51)
                cr.stroke()
            else:
                rrect(cr, cx, y + h, w, 52, 8, th["heat"][lvl])
            text(cr, weekday_labels()[i], cx, y + h + 8, 12, th["text"] if lvl else th["dim"], width=w, align="center")
            if c["minutes"]:
                text(cr, S.fmt_minutes(c["minutes"]), cx, y + h + 28, 12, th["text"], width=w, align="center")
        return h + 52 + GAP
    offset = cells[0]["date"].weekday()
    cols = (offset + len(cells) + 6) // 7
    cell = min(26.0, (CW - 6 * (cols - 1)) / cols)
    cell = max(cell, 10.0)
    step = cell + 4 if cols <= 20 else cell + 2
    total_w = cols * step
    x0 = max(0.0, (CW - total_w) / 2) if cols <= 20 else 0.0
    for i, c in enumerate(cells):
        col, row = (i + offset) // 7, (i + offset) % 7
        lvl = heat_level(c["minutes"], maxm)
        cx, cy = x0 + col * step, y + h + row * step
        if lvl is None:
            cr.set_source_rgb(*rgb(th["line"]))
            cr.set_line_width(1)
            cr.rectangle(cx + .5, cy + .5, cell - 1, cell - 1)
            cr.stroke()
        else:
            rrect(cr, cx, cy, cell, cell, 3, th["heat"][lvl])
    return h + 7 * step + GAP


def _timeline_cell(cr, th, c, x, y, w):
    """Cellule de la frise (un jour ou un mois) ; renvoie sa hauteur."""
    rows = [] if c["future"] or not c["games"] else c["games"]
    shown = rows[:MAX_LINES]
    extra = len(rows) - len(shown)
    lines = max(1, len(shown) + (1 if extra else 0))
    hgt = 34 + lines * 20
    rrect(cr, x, y, w, hgt, 10, th["card"])
    if c["today"]:
        cr.set_source_rgb(*rgb(th["accent"]))
        cr.set_line_width(2)
        cr.rectangle(x + 1, y + 1, w - 2, hgt - 2)
        cr.stroke()
    text(cr, c["label"], x + 12, y + 8, 14, th["text"], bold=True, width=w - 100)
    if c["total"]:
        text(cr, S.fmt_minutes(c["total"]), x + w - 92, y + 9, 12, th["dim"], width=80, align="right")
    ly = y + 32
    if c["future"]:
        text(cr, tr("Upcoming"), x + 12, ly, 12, th["dim"])
    elif not rows:
        text(cr, tr("Nothing played"), x + 12, ly, 12, th["dim"])
    for g in shown:
        text(cr, g["name"], x + 12, ly, 12.5, th["text"], width=w - 90)
        text(cr, S.fmt_minutes(g["minutes"]), x + w - 74, ly, 12, th["dim"], width=62, align="right")
        ly += 20
    if extra:
        text(cr, f"+{extra}", x + 12, ly, 12, th["dim"])
    return hgt


def timeline_rows(d):
    """Lignes de la frise : listes de colonnes, `n` cellules par ligne (jour : 2 ; mois : 3)."""
    n = {"week": 2, "month": 2, "year": 3}[d["kind"]]
    cols = d["columns"]
    return [cols[i:i + n] for i in range(0, len(cols), n)], n


def timeline_blocks(d):
    """Une rangée de la frise = un bloc (insécable à la pagination) ; le titre « Frise » est dans le
    premier, pour ne jamais rester seul en bas d'une page."""
    rows, n = timeline_rows(d)
    w = (CW - GAP * (n - 1)) / n
    blocks = []
    for k, row in enumerate(rows):
        def draw(cr, th, d_, y, row=row, w=w, first=(k == 0)):
            top = _heading(cr, th, tr("Timeline"), y) if first else 0
            hgt = 0
            for i, c in enumerate(row):
                hgt = max(hgt, _timeline_cell(cr, th, c, i * (w + GAP), y + top, w))
            return top + hgt + 10
        blocks.append(draw)
    return blocks


def b_footer(cr, th, d, y):
    return text(cr, tr("Generated by Ludodex on {date}", date=d["generated"].strftime("%d/%m/%Y %H:%M")),
                0, y + 8, 11, th["dim"], width=CW, align="center") + 8


def blocks_for(d):
    return [b_header, b_summary, b_top, b_when, b_regularity, *timeline_blocks(d), b_footer]


def _measure(blocks, th, d):
    scratch = cairo.Context(cairo.RecordingSurface(cairo.CONTENT_COLOR_ALPHA, None))
    return [b(scratch, th, d, 0) for b in blocks]


# ---------- sorties ----------

def write_png(d, path, theme="dark", scale=2.0):
    """PNG d'un seul tenant (largeur logique CW + 2·PAD, rendue à `scale` ×)."""
    th = THEMES[theme]
    blocks = blocks_for(d)
    heights = _measure(blocks, th, d)
    total_h = PAD * 2 + sum(heights)
    w_px, h_px = int((CW + 2 * PAD) * scale), int(total_h * scale)
    if h_px > 32000:                       # limite des images Cairo
        scale = 32000 / total_h
        w_px, h_px = int((CW + 2 * PAD) * scale), int(total_h * scale)
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w_px, h_px)
    cr = cairo.Context(surf)
    cr.scale(scale, scale)
    cr.set_source_rgb(*rgb(th["bg"]))
    cr.paint()
    y = float(PAD)
    for b, hgt in zip(blocks, heights):
        cr.save()
        cr.translate(PAD, 0)
        b(cr, th, d, y)
        cr.restore()
        y += hgt
    surf.write_to_png(str(path))


def paginate(heights, page_h):
    """Répartit des blocs sur des pages de hauteur `page_h` : [[index, …], …] ; jamais de bloc coupé
    (un bloc plus haut qu'une page occupe sa propre page)."""
    pages, cur, used = [], [], 0.0
    for i, h in enumerate(heights):
        if cur and used + h > page_h:
            pages.append(cur)
            cur, used = [], 0.0
        cur.append(i)
        used += h
    if cur:
        pages.append(cur)
    return pages


def write_pdf(d, path, theme="light"):
    """PDF A4 portrait, blocs insécables répartis sur autant de pages que nécessaire ; renvoie le nombre de pages."""
    th = THEMES[theme]
    blocks = blocks_for(d)
    heights = _measure(blocks, th, d)
    pw, ph = A4
    s = (pw - 2 * A4_MARGIN) / CW
    page_h = (ph - 2 * A4_MARGIN) / s
    surf = cairo.PDFSurface(str(path), pw, ph)
    surf.set_metadata(cairo.PDF_METADATA_TITLE, f"Ludodex — {d['head']}")
    surf.set_metadata(cairo.PDF_METADATA_CREATOR, "Ludodex")
    cr = cairo.Context(surf)
    pages = paginate(heights, page_h)
    for n, idx in enumerate(pages):
        if n:
            cr.show_page()
        cr.set_source_rgb(*rgb(th["bg"]))
        cr.paint()
        cr.save()
        cr.translate(A4_MARGIN, A4_MARGIN)
        cr.scale(s, s)
        y = 0.0
        for i in idx:
            blocks[i](cr, th, d, y)
            y += heights[i]
        cr.restore()
    surf.finish()
    return len(pages)
