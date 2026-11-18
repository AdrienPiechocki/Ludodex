#!/usr/bin/env python3
"""export.py — rétrospective exportable : Web (HTML + CSS), PNG et PDF.

Une seule collecte (`collect`) alimente les trois formats, à partir des mêmes fonctions de
`stats` que la page Rétrospective : mêmes fusions de sessions, même seuil de masquage, mêmes
chiffres. Rien n'est recalculé ici, donc rien ne peut diverger de l'écran.

  - HTML : une page autonome (sans script), claire ou sombre selon le système, imprimable ;
    le CSS est un fichier voisin (`<nom>.css`) ou intégré (`inline=True`) ;
  - PNG / PDF : dessinés avec Cairo (déjà requis par l'application), via `export_draw` ;
    le PDF est paginé en A4 sans couper un bloc en deux.

`export(con, kind, fmt, path, now=…)` : `now` permet d'exporter une période passée.
"""
import base64
import math
import datetime
import html
import mimetypes
import pathlib
import time

from i18n import get_lang, tr
from . import stats as S
from .covers import cover_file

KINDS = ("week", "month", "year")
FORMATS = ("html", "png", "pdf")
PIE_COLORS = ("#614d8f", "#4f9e6e", "#c9a227", "#c96a3d", "#4f7fc9", "#c264a1", "#8a7a5c", "#5c8a8a")
OTHER_COLOR = "#55555f"
MAX_EMBED_COVER = 3_000_000       # octets : repli sans outil de réduction ; au-delà, jaquette non embarquée
COVER_THUMB = (132, 186)          # vignette HTML (3× l'affichage 44×62 : nette sur écran HiDPI)
OTHER_COLOR = "#55555f"
_TIMELINES = {"week": S.week_timeline, "month": S.month_timeline, "year": S.year_timeline}
_TITLES = {"week": "This week", "month": "This month", "year": "This year"}


# ---------- collecte ----------

def vs_label(kind, now=None):
    """Référence des deltas : « 2025 », « Octobre », « Semaine dernière »."""
    if kind == "year":
        return str(S.previous_bounds(kind, now)[1].year)
    if kind == "month":
        return S.month_name(S.previous_bounds(kind, now)[0])
    return tr("Last week")


def delta(cur, prev, minutes=False, vs=None, invert=False):
    """(texte, sens) : sens = 'up' | 'down' | 'flat' (couleur : vert / rouge / neutre ; `invert` : plus = rouge)."""
    d = cur - prev
    if d == 0:
        return "—", "flat"
    up = d > 0
    val = S.fmt_minutes(abs(d)) if minutes else str(abs(d))
    text = f"{'▲' if up else '▼'} {'+' if up else '−'}{val}"
    if vs:
        text += f" {tr('vs {x}', x=vs)}"
    return text, ("down" if up else "up") if invert else ("up" if up else "down")


def rank_delta(g):
    if g["prev_rank"] is None:
        return f"★ {tr('New')}", "up"
    d = g["prev_rank"] - g["rank"]
    if d == 0:
        return "=", "flat"
    return f"{'▲' if d > 0 else '▼'} {abs(d)}", "up" if d > 0 else "down"


def _columns(kind, tl):
    """Colonnes de la frise, une ligne par jeu et par colonne (sessions cumulées)."""
    cols = tl["months"] if kind == "year" else tl["days"]
    out = []
    for c in cols:
        per = {}
        for e in c["entries"]:
            a = per.setdefault(e["name"], {"name": e["name"], "minutes": 0, "sessions": 0, "start": e["start"]})
            a["minutes"] += e["minutes"]
            a["sessions"] += 1
            a["start"] = min(a["start"], e["start"])
        games = sorted(per.values(), key=lambda a: (-a["minutes"], a["name"]))
        out.append({"date": c["date"], "label": S._col_label(kind, c["date"]),
                    "today": c["date"] == tl["today"] or (kind == "year" and c["date"].year == tl["today"].year
                                                          and c["date"].month == tl["today"].month),
                    "future": c["date"] > tl["today"], "games": games,
                    "total": sum(a["minutes"] for a in games)})
    return out


def collect(con, kind, now=None, game=None, top=7):
    """Toutes les données d'une rétrospective (week | month | year) en un dictionnaire."""
    if kind not in KINDS:
        raise ValueError(f"kind: {KINDS}")
    now = time.time() if now is None else now
    game = game or None
    tl = _TIMELINES[kind](con, now=now, game=game)
    start, end = S.period_bounds(kind, now)
    head, _unit = S._timeline_text[kind]
    top_data = S.top_games(con, kind, now, limit=top, game=game)
    covers = {}
    for t in top_data["top"]:
        row = con.execute("SELECT * FROM games WHERE name=? ORDER BY id LIMIT 1", (t["name"],)).fetchone()
        f = cover_file(row) if row else None
        if f:
            covers[t["name"]] = str(f)
    return {
        "kind": kind, "now": now, "start": start, "end": end, "game": game or "",
        "title": tr(_TITLES[kind]),
        "head": tr(head, date=S._head_date(kind, tl["start"]), n=tl["game_count"],
                   time=S.fmt_minutes(tl["total_minutes"])),
        "vs": vs_label(kind, now),
        "summary": S.period_summary(con, kind, now, game),
        "top": top_data, "covers": covers,
        "hours": S.hours_histogram(con, kind, now, game)["current"],
        "weekdays": S.weekdays_histogram(con, kind, now, game)["current"],
        "regularity": S.regularity(con, kind, now, game),
        "heatmap": S.heatmap(con, kind, now, game),
        "columns": _columns(kind, tl),
        "generated": datetime.datetime.fromtimestamp(now),
    }


def weekday_labels():
    """Lun…Dim dans la langue de l'application."""
    monday = datetime.date(2024, 1, 1)
    return [S.weekday_short(monday + datetime.timedelta(days=i)) for i in range(7)]


def heat_level(minutes, maxm):
    """0 (rien) … 4 (le plus joué) ; None pour un jour futur."""
    if minutes is None:
        return None
    if minutes <= 0:
        return 0
    return 1 + min(3, int(4 * minutes / max(maxm, 1) - 1e-9))


def heat_tooltip(c):
    d = c["date"].strftime("%d/%m/%Y")
    if c["minutes"] is None:
        return d
    if not c["minutes"]:
        return f"{d} · {tr('Nothing played')}"
    return f"{d} · {S.fmt_minutes(c['minutes'])} · {c['sessions']} {tr('sessions')}"


def filename(kind, now=None):
    """Nom de fichier par défaut : ludodex-retro-week-2026-11-18."""
    d = datetime.date.fromtimestamp(time.time() if now is None else now)
    return f"ludodex-retro-{kind}-{d.isoformat()}"


# ---------- HTML + CSS ----------

CSS = """\
:root{--bg:#1e1e24;--card:#2a2a33;--line:#3a3a45;--text:#f2f2f5;--dim:#9a9aa8;--up:#57c785;--down:#e5645f;
--accent:#8e6fd1;--h0:#2a2a33;--h1:#3d3558;--h2:#55447f;--h3:#7358a8;--h4:#9a78d8}
@media (prefers-color-scheme:light){:root{--bg:#ffffff;--card:#f1f1f5;--line:#dcdce4;--text:#1c1c22;--dim:#6a6a78;
--up:#1f8f4d;--down:#c0392b;--accent:#614d8f;--h0:#ececf2;--h1:#d9d0ee;--h2:#bba9e0;--h3:#8e73c4;--h4:#614d8f}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:16px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
.retro{max-width:1080px;margin:0 auto;padding:32px 20px 48px}
.kicker{margin:0;color:var(--dim);font-size:.85rem;letter-spacing:.08em;text-transform:uppercase}
h1{margin:.1em 0 .1em;font-size:2rem}
.sub{margin:0 0 1.2rem;color:var(--dim)}
h2{margin:2rem 0 .8rem;font-size:1.15rem}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.card{background:var(--card);border-radius:12px;padding:12px 14px;text-align:center}
.card .label{display:block;color:var(--dim);font-size:.8rem}
.card .value{display:block;font-size:1.6rem;font-weight:700;margin:2px 0}
.card .value small{font-size:.8rem;font-weight:400;color:var(--dim)}
.card .delta{display:block;font-size:.8rem;color:var(--dim)}
.up{color:var(--up)!important}.down{color:var(--down)!important}.flat{color:var(--dim)!important}
.pie{display:flex;gap:24px;align-items:center;flex-wrap:wrap;margin-bottom:14px;background:var(--card);
border-radius:12px;padding:14px 18px}
.pie .ring{position:relative;width:200px;height:200px;flex:none}.pie svg{width:100%;height:100%}
.pie .total{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;font-size:1.15rem}
.legend{list-style:none;margin:0;padding:0;display:grid;gap:6px;flex:1;min-width:220px}
.legend li{display:grid;grid-template-columns:12px 1fr auto;gap:10px;align-items:center;font-size:.9rem}
.legend i{width:12px;height:12px;border-radius:50%}.legend span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.legend em{color:var(--dim);font-style:normal}
.top{list-style:none;margin:0;padding:0;display:grid;gap:8px}
.top li{display:grid;grid-template-columns:2rem 44px 1fr auto;gap:12px;align-items:center;background:var(--card);
border-radius:12px;padding:8px 12px}
.top .rank{color:var(--dim);font-weight:700;text-align:center}
.top img,.top .nocover{width:44px;height:62px;border-radius:6px;object-fit:cover;background:var(--line)}
.top .name{font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.top .meta{color:var(--dim);font-size:.85rem}
.top .bar{height:6px;border-radius:3px;margin-top:6px;background:var(--line);overflow:hidden}
.top .bar i{display:block;height:100%}
.top .rd{font-size:.85rem;white-space:nowrap}
.charts{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:14px}
.chart{background:var(--card);border-radius:12px;padding:12px 14px}
.chart h3{margin:0 0 .6rem;font-size:.95rem}
.bars{display:flex;align-items:flex-end;gap:3px;height:120px}
.bars div{flex:1;display:flex;flex-direction:column;justify-content:flex-end;height:100%}
.bars i{display:block;background:var(--accent);border-radius:3px 3px 0 0;min-height:1px}
.axis{display:flex;gap:3px;margin-top:4px;color:var(--dim);font-size:.7rem}
.axis span{flex:1;text-align:center;overflow:hidden}
.heat{display:grid;grid-template-rows:repeat(7,14px);grid-auto-flow:column;grid-auto-columns:14px;gap:3px;
overflow-x:auto;padding:4px 0}
.heat b{display:block;width:14px;height:14px;border-radius:3px;background:var(--h0)}
.heat .l1{background:var(--h1)}.heat .l2{background:var(--h2)}.heat .l3{background:var(--h3)}.heat .l4{background:var(--h4)}
.heat .gap{background:transparent}.heat .fut{background:transparent;outline:1px dashed var(--line)}
.timeline{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:10px}
.col{background:var(--card);border-radius:12px;padding:10px 12px}
.col.today{outline:2px solid var(--accent)}
.col h4{margin:0 0 .4rem;display:flex;justify-content:space-between;font-size:.9rem}
.col h4 span{color:var(--dim);font-weight:400}
.col ul{list-style:none;margin:0;padding:0;font-size:.85rem}
.col li{display:flex;justify-content:space-between;gap:8px;padding:2px 0}
.col li em{font-style:normal;color:var(--dim);white-space:nowrap}
.col li span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.col .none{color:var(--dim);font-size:.85rem}
footer{margin-top:2.5rem;color:var(--dim);font-size:.8rem;text-align:center}
@media print{:root{--bg:#fff;--card:#f1f1f5;--line:#dcdce4;--text:#1c1c22;--dim:#6a6a78;--up:#1f8f4d;--down:#c0392b;
--accent:#614d8f;--h0:#ececf2;--h1:#d9d0ee;--h2:#bba9e0;--h3:#8e73c4;--h4:#614d8f}
.retro{padding:0}.card,.top li,.chart,.col,.pie{break-inside:avoid}}
"""


def _e(s):
    return html.escape(str(s), quote=True)


def _thumb_jpeg(p):
    """Vignette JPEG de la jaquette (GdkPixbuf, sinon Pillow), ou None si aucun des deux."""
    try:
        import gi
        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import GdkPixbuf
        pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(p), *COVER_THUMB, True)
        if pb.get_has_alpha():
            pb = pb.composite_color_simple(pb.get_width(), pb.get_height(),
                                           GdkPixbuf.InterpType.BILINEAR, 255, 1, 0x2a2a33, 0x2a2a33)
        return pb.save_to_bufferv("jpeg", ["quality"], ["85"])[1]
    except Exception:
        pass
    try:
        import io
        from PIL import Image
        im = Image.open(p)
        im.thumbnail(COVER_THUMB)
        out = io.BytesIO()
        im.convert("RGB").save(out, "JPEG", quality=85)
        return out.getvalue()
    except Exception:
        return None


def _cover_uri(path):
    """Jaquette en data-URI. Réduite en vignette : un PNG de plusieurs Mo ne gonfle plus la page
    et ne disparaît plus ; sans outil de réduction, le fichier d'origine est embarqué tel quel."""
    p = pathlib.Path(path)
    try:
        data = _thumb_jpeg(p)
        if data:
            return "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")
        if p.stat().st_size > MAX_EMBED_COVER:
            return ""
        mime = mimetypes.guess_type(p.name)[0] or "image/jpeg"
        return f"data:{mime};base64," + base64.b64encode(p.read_bytes()).decode("ascii")
    except OSError:
        return ""


def pie_slices(d):
    """Parts du camembert : top + « Autres » (gris), comme dans l'application."""
    top, total = d["top"], d["top"]["total_minutes"]
    out = [{"name": g["name"], "minutes": g["minutes"], "share": g["share"],
            "color": PIE_COLORS[i % len(PIE_COLORS)]} for i, g in enumerate(top["top"])]
    o = top["others"]
    if o["minutes"]:
        out.append({"name": tr("Other ({n} games)", n=o["games"]), "minutes": o["minutes"],
                    "share": round(o["minutes"] * 100 / total) if total else 0, "color": OTHER_COLOR})
    return out


def _pie(d):
    """Anneau SVG (sans JS) + total au centre + légende."""
    slices, total = pie_slices(d), d["top"]["total_minutes"]
    if not slices or not total:
        return ""
    r, circ, off, arcs = 37.5, 2 * math.pi * 37.5, 0.0, []
    for s in slices:
        ln = circ * s["minutes"] / total
        arcs.append(f'<circle r="{r}" cx="50" cy="50" fill="none" stroke="{s["color"]}" stroke-width="25" '
                    f'stroke-dasharray="{ln:.3f} {circ - ln:.3f}" stroke-dashoffset="{-off:.3f}">'
                    f'<title>{_e(s["name"])} · {_e(S.fmt_minutes(s["minutes"]))} · {s["share"]} %</title></circle>')
        off += ln
    legend = "".join(f'<li><i style="background:{s["color"]}"></i><span>{_e(s["name"])}</span>'
                     f'<em>{_e(S.fmt_minutes(s["minutes"]))} · {s["share"]} %</em></li>' for s in slices)
    return (f'<div class="pie"><div class="ring"><svg viewBox="0 0 100 100" role="img" '
            f'aria-label="{_e(tr("Time per game"))}"><g transform="rotate(-90 50 50)">{"".join(arcs)}</g></svg>'
            f'<div class="total"><b>{_e(S.fmt_minutes(total))}</b></div></div><ul class="legend">{legend}</ul></div>')


def _cards(d):
    s, prev, vs = d["summary"], d["summary"]["prev"], d["vs"]
    r = d["regularity"]
    pct = round(s["days_played"] * 100 / s["period_days"]) if s["period_days"] else 0
    items = [
        (tr("Total time"), _e(S.fmt_minutes(s["total_minutes"])), "",
         delta(s["total_minutes"], prev["total_minutes"], minutes=True, vs=vs)),
        (tr("Sessions"), str(s["sessions"]), "", delta(s["sessions"], prev["sessions"], vs=vs)),
        (tr("Games played"), str(s["games"]), "", delta(s["games"], prev["games"], vs=vs)),
        (tr("Days played"), str(s["days_played"]), f" /{s['period_days']}",
         delta(s["days_played"], prev["days_played"], vs=vs) if s["days_played"] != prev["days_played"]
         else (tr("{pct} % of the period", pct=pct), "flat")),
        (tr("Avg. per session"), _e(S.fmt_minutes(s["avg_minutes"])), "",
         delta(s["avg_minutes"], prev["avg_minutes"], minutes=True)),
        (tr("Current streak"), f"{r['streak']} {tr('d')}", "", (f"{vs} : {r['prev']['streak']} {tr('d')}", "flat")),
        (tr("Longest streak"), f"{r['longest_streak']} {tr('d')}", "",
         delta(r["longest_streak"], r["prev"]["longest_streak"])),
        (tr("Longest break"), f"{r['longest_gap']} {tr('d')}", "",
         delta(r["longest_gap"], r["prev"]["longest_gap"], invert=True)),
    ]
    return "".join(
        f'<div class="card"><span class="label">{_e(label)}</span>'
        f'<span class="value">{value}<small>{_e(small)}</small></span>'
        f'<span class="delta {cls}">{_e(text)}</span></div>' for label, value, small, (text, cls) in items)


def _top(d):
    top = d["top"]["top"]
    if not top:
        return f'<p class="sub">{_e(tr("Nothing played"))}</p>'
    out = ['<ol class="top">']
    for i, g in enumerate(top):
        color = PIE_COLORS[i % len(PIE_COLORS)]
        uri = _cover_uri(d["covers"][g["name"]]) if g["name"] in d["covers"] else ""
        img = f'<img src="{uri}" alt="">' if uri else '<div class="nocover"></div>'
        rd, cls = rank_delta(g)
        out.append(
            f'<li><span class="rank">{g["rank"]}</span>{img}'
            f'<div><div class="name" title="{_e(g["name"])}">{_e(g["name"])}</div>'
            f'<div class="meta">{_e(S.fmt_minutes(g["minutes"]))} · {g["sessions"]} {_e(tr("sessions"))}'
            f' · {g["share"]} %</div>'
            f'<div class="bar"><i style="width:{max(2, g["share"])}%;background:{color}"></i></div></div>'
            f'<span class="rd {cls}">{_e(rd)}</span></li>')
    o = d["top"]["others"]
    if o["games"]:
        out.append(f'<li><span class="rank">…</span><div class="nocover"></div><div><div class="name">'
                   f'{_e(tr("Other ({n} games)", n=o["games"]))}</div><div class="meta">'
                   f'{_e(S.fmt_minutes(o["minutes"]))} · {o["sessions"]} {_e(tr("sessions"))}</div></div><span></span></li>')
    out.append("</ol>")
    return "".join(out)


def _bars(data, labels, title, every=1):
    peak = max([b["minutes"] for b in data] + [1])
    bars = "".join(f'<div title="{_e(lab)} · {_e(S.fmt_minutes(b["minutes"]))} · {b["sessions"]} {_e(tr("sessions"))}">'
                   f'<i style="height:{round(100 * b["minutes"] / peak)}%"></i></div>'
                   for b, lab in zip(data, labels))
    axis = "".join(f"<span>{_e(lab) if i % every == 0 else ''}</span>" for i, lab in enumerate(labels))
    return f'<div class="chart"><h3>{_e(title)}</h3><div class="bars">{bars}</div><div class="axis">{axis}</div></div>'


def _heat(d):
    cells = d["heatmap"]
    maxm = max([(c["minutes"] or 0) for c in cells] + [1])
    out = ['<div class="heat">']
    out += ['<b class="gap"></b>'] * cells[0]["date"].weekday()          # le 1er jour tombe sur sa ligne
    for c in cells:
        lvl = heat_level(c["minutes"], maxm)
        cls = "fut" if lvl is None else f"l{lvl}"
        out.append(f'<b class="{cls}" title="{_e(heat_tooltip(c))}"></b>')
    out.append("</div>")
    return "".join(out)


def _timeline(d):
    out = ['<div class="timeline">']
    for c in d["columns"]:
        if c["future"]:
            body = f'<p class="none">{_e(tr("Upcoming"))}</p>'
        elif not c["games"]:
            body = f'<p class="none">{_e(tr("Nothing played"))}</p>'
        else:
            body = "<ul>" + "".join(
                f'<li><span title="{_e(g["name"])}">{_e(g["name"])}</span>'
                f'<em>{_e(S.fmt_minutes(g["minutes"]))}</em></li>' for g in c["games"]) + "</ul>"
        total = f"<span>{_e(S.fmt_minutes(c['total']))}</span>" if c["total"] else ""
        out.append(f'<div class="col{" today" if c["today"] else ""}"><h4>{_e(c["label"])}{total}</h4>{body}</div>')
    out.append("</div>")
    return "".join(out)


def render_html(d, css_href=None):
    """Page HTML complète ; `css_href` = lien vers la feuille de style, sinon le CSS est intégré."""
    style = (f'<link rel="stylesheet" href="{_e(css_href)}">' if css_href else f"<style>\n{CSS}</style>")
    hours = [f"{h:02d}h" for h in range(24)]
    sub = d["head"] + (f" · {tr('Game')}: {d['game']}" if d["game"] else "")
    return f"""<!doctype html>
<html lang="{_e(get_lang())}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Ludodex — {_e(tr('Retrospective'))} — {_e(d['head'])}</title>
{style}
</head>
<body>
<main class="retro">
<header>
<p class="kicker">Ludodex · {_e(tr('Retrospective'))}</p>
<h1>{_e(d['title'])}</h1>
<p class="sub">{_e(sub)}</p>
</header>
<section><h2>{_e(tr('Summary'))}</h2><div class="cards">{_cards(d)}</div></section>
<section><h2>{_e(tr('What — time breakdown'))}</h2>{_pie(d)}{_top(d)}</section>
<section><h2>{_e(tr('When — hours & days'))}</h2><div class="charts">
{_bars(d['hours'], hours, tr('Hours'), 3)}
{_bars(d['weekdays'], weekday_labels(), tr('Weekdays'))}
</div></section>
<section><h2>{_e(tr('Regularity — streaks & calendar'))}</h2>{_heat(d)}</section>
<section><h2>{_e(tr('Timeline'))}</h2>{_timeline(d)}</section>
<footer>{_e(tr('Generated by Ludodex on {date}', date=d['generated'].strftime('%d/%m/%Y %H:%M')))}</footer>
</main>
</body>
</html>
"""


# ---------- écriture ----------

def export(con, kind, fmt, path, now=None, game=None, inline=False, theme=None):
    """Écrit la rétrospective dans `path` ; renvoie la liste des fichiers créés.

    fmt : 'html' (+ `<nom>.css` voisin, sauf `inline=True`), 'png' ou 'pdf'.
    `theme` ('dark' | 'light') ne concerne que PNG et PDF (sombre / clair par défaut)."""
    if fmt not in FORMATS:
        raise ValueError(f"fmt: {FORMATS}")
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    d = collect(con, kind, now=now, game=game)
    if fmt == "html":
        if inline:
            path.write_text(render_html(d), encoding="utf-8")
            return [path]
        css = path.with_suffix(".css")
        path.write_text(render_html(d, css_href=css.name), encoding="utf-8")
        css.write_text(CSS, encoding="utf-8")
        return [path, css]
    from . import export_draw            # Cairo : chargé seulement pour PNG / PDF
    if fmt == "png":
        export_draw.write_png(d, path, theme=theme or "dark")
    else:
        export_draw.write_pdf(d, path, theme=theme or "light")
    return [path]
