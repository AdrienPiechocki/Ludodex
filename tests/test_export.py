import datetime
import sqlite3
import struct
import time

import pytest

cairo = pytest.importorskip("cairo")

from core import export, export_draw           # noqa: E402
from core.migrations import migrate             # noqa: E402

NOW = datetime.datetime(2026, 11, 18, 12, 0).timestamp()       # un mercredi
EVIL = '<script>alert("x")</script> & Co'


@pytest.fixture
def con():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    migrate(c)
    names = ["Hades", "Celeste", EVIL, "Tunic", "Dorfromantik", "Hollow Knight", "Noita", "Balatro", "Pico Park", "Extra"]
    for i, n in enumerate(names):
        c.execute("INSERT INTO games (source, ext_id, name) VALUES ('custom', ?, ?)", (f"g{i}", n))
    ids = [r[0] for r in c.execute("SELECT id FROM games ORDER BY id")]
    for day in range(0, 40):                                   # 40 jours d'historique, plusieurs jeux par jour
        base = datetime.datetime(2026, 11, 18, 20, 0) - datetime.timedelta(days=day)
        for k in range(1 + day % 3):
            start = int((base + datetime.timedelta(hours=k * 2)).timestamp())
            gid = ids[(day * 3 + k) % len(ids)]
            c.execute("INSERT OR IGNORE INTO sessions (game_id, started, ended, minutes) VALUES (?,?,?,?)",
                      (gid, start, start + (20 + 7 * day % 90) * 60, 0))
    c.commit()
    return c


@pytest.mark.parametrize("kind", export.KINDS)
def test_collect_matches_stats(con, kind):
    d = export.collect(con, kind, now=NOW)
    from core import stats
    assert d["summary"]["total_minutes"] == stats.period_summary(con, kind, NOW)["total_minutes"]
    assert d["summary"]["total_minutes"] > 0
    assert len(d["columns"]) == {"week": 7, "month": 30, "year": 12}[kind]
    assert sum(c["total"] for c in d["columns"]) == d["summary"]["total_minutes"]
    assert len(d["hours"]) == 24 and len(d["weekdays"]) == 7


def test_html_is_escaped_and_self_describing(con):
    d = export.collect(con, "month", now=NOW)
    page = export.render_html(d)
    assert "<script>" not in page and "&lt;script&gt;" in page
    assert "<style>" in page and "Ludodex" in page
    linked = export.render_html(d, css_href="x.css")
    assert 'href="x.css"' in linked and "<style>" not in linked


def test_export_html_writes_sibling_css(con, tmp_path):
    out = export.export(con, "week", "html", tmp_path / "r.html", now=NOW)
    assert [p.name for p in out] == ["r.html", "r.css"]
    assert 'href="r.css"' in out[0].read_text() and ".retro" in out[1].read_text()
    one = export.export(con, "week", "html", tmp_path / "s.html", now=NOW, inline=True)
    assert len(one) == 1 and "<style>" in one[0].read_text()


@pytest.mark.parametrize("kind", export.KINDS)
def test_png(con, tmp_path, kind):
    (p,) = export.export(con, kind, "png", tmp_path / f"{kind}.png", now=NOW)
    head = p.read_bytes()[:24]
    assert head[:8] == b"\x89PNG\r\n\x1a\n"
    w, h = struct.unpack(">II", head[16:24])
    assert w == (export_draw.CW + 2 * export_draw.PAD) * 2 and h > 1000


@pytest.mark.parametrize("kind", export.KINDS)
def test_pdf_is_paginated_a4(con, tmp_path, kind):
    d = export.collect(con, kind, now=NOW)
    p = tmp_path / f"{kind}.pdf"
    pages = export_draw.write_pdf(d, p)
    raw = p.read_bytes()
    assert raw.startswith(b"%PDF") and raw.rstrip().endswith(b"%%EOF")
    assert pages >= 1
    if kind == "month":
        assert pages >= 2                                      # 30 jours : plusieurs pages
    assert export.export(con, kind, "pdf", tmp_path / "x.pdf", now=NOW) == [tmp_path / "x.pdf"]


def test_paginate_never_splits_a_block():
    assert export_draw.paginate([300, 300, 300, 300], 700) == [[0, 1], [2, 3]]
    assert export_draw.paginate([900, 100], 700) == [[0], [1]]       # bloc trop haut : sa propre page
    assert export_draw.paginate([], 700) == []


def test_empty_database_still_exports(tmp_path):
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    migrate(c)
    for fmt in export.FORMATS:
        (p, *_) = export.export(c, "week", fmt, tmp_path / f"e.{fmt}", now=NOW)
        assert p.stat().st_size > 500


def test_game_filter_and_past_period(con, tmp_path):
    d = export.collect(con, "week", now=NOW, game="hades")
    assert {g["name"] for c in d["columns"] for g in c["games"]} <= {"Hades"}
    old = export.collect(con, "week", now=NOW - 14 * 86400)
    assert old["start"] < export.collect(con, "week", now=NOW)["start"]


def test_helpers():
    assert export.heat_level(None, 10) is None and export.heat_level(0, 10) == 0
    assert export.heat_level(10, 10) == 4 and export.heat_level(1, 10) == 1
    assert export.delta(5, 3)[1] == "up" and export.delta(3, 5)[1] == "down" and export.delta(3, 3) == ("—", "flat")
    assert export.delta(5, 3, invert=True)[1] == "down"
    assert export.filename("week", NOW) == "ludodex-retro-week-2026-11-18"


def test_html_has_pie_and_all_covers_even_when_large(con, tmp_path, monkeypatch):
    from PIL import Image
    d = export.collect(con, "month", now=NOW)
    # une grosse jaquette PNG (bruit, > 400 Ko) pour CHAQUE jeu du top
    import os
    for i, g in enumerate(d["top"]["top"]):
        p = tmp_path / f"c{i}.png"
        Image.frombytes("RGB", (600, 900), os.urandom(600 * 900 * 3)).save(p)
        assert p.stat().st_size > 400_000
        d["covers"][g["name"]] = str(p)
    page = export.render_html(d)
    assert page.count('<img src="data:image/jpeg;base64,') == len(d["top"]["top"])
    assert 'class="pie"' in page and "<svg" in page and page.count("<circle") == len(export.pie_slices(d))
    assert len(page) < 1_500_000                                 # vignettes, pas les originaux


def test_pie_slices_add_up(con):
    d = export.collect(con, "month", now=NOW)
    sl = export.pie_slices(d)
    assert sum(s["minutes"] for s in sl) == d["top"]["total_minutes"]
    assert sl[-1]["color"] == export.OTHER_COLOR


def test_png_pdf_with_pie(con, tmp_path):
    for fmt in ("png", "pdf"):
        out = tmp_path / f"r.{fmt}"
        export.export(con, "month", fmt, str(out), now=NOW)
        assert out.stat().st_size > 5000
