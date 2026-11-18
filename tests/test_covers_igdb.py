import sqlite3

import pytest

from core import covers
from core.migrations import migrate


@pytest.fixture
def con(monkeypatch):
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    migrate(c)
    monkeypatch.setattr(covers, "igdb_credentials", lambda: ("id", "secret"))
    monkeypatch.setattr(covers, "sgdb_key", lambda: "")
    covers.IGDB_STATE["error"] = ""
    return c


def _game(con, source, ext, name):
    con.execute("INSERT INTO games (source, ext_id, name) VALUES (?,?,?)", (source, ext, name))
    return con.execute("SELECT * FROM games WHERE ext_id=?", (ext,)).fetchone()


def _fetch(urls_ok):
    seen = []

    def f(url):
        seen.append(url)
        return b"IMG" if any(u in url for u in urls_ok) else None
    f.seen = seen
    return f


def _post(rows):
    calls = []

    def p(path, body):
        calls.append(body)
        return rows
    p.calls = calls
    return p


def test_igdb_used_when_steam_has_no_art(con, monkeypatch):
    g = _game(con, "steam", "111", "Obscure Game")
    monkeypatch.setattr(covers, "igdb_post", _post([{"name": "Obscure Game", "cover": {"image_id": "co1abc"}}]))
    f = _fetch(["co1abc"])
    assert covers.fetch_cover(con, g, f)
    row = con.execute("SELECT cover_src, cover_path FROM games WHERE id=?", (g["id"],)).fetchone()
    assert row["cover_src"] == "igdb" and row["cover_path"].endswith(".jpg")
    assert any("t_cover_big_2x/co1abc.jpg" in u for u in f.seen)


def test_igdb_not_called_when_steam_cdn_has_art(con, monkeypatch):
    g = _game(con, "steam", "222", "Known Game")
    p = _post([])
    monkeypatch.setattr(covers, "igdb_post", p)
    assert covers.fetch_cover(con, g, _fetch(["steamstatic"]))
    assert not p.calls
    assert con.execute("SELECT cover_src FROM games").fetchone()[0] == "steam"


def test_non_exact_name_gives_no_cover(con, monkeypatch):
    g = _game(con, "custom", "c1", "Hades")
    monkeypatch.setattr(covers, "igdb_post", _post([{"name": "Hades II", "cover": {"image_id": "zz"}}]))
    assert not covers.fetch_cover(con, g, _fetch(["zz"]))
    assert con.execute("SELECT cover_path FROM games").fetchone()[0] == ""


def test_demo_falls_back_to_full_game(con, monkeypatch):
    g = _game(con, "custom", "c2", "Cool Game Demo")
    p = _post([{"name": "Cool Game", "cover": {"image_id": "full1"}}])
    monkeypatch.setattr(covers, "igdb_post", p)
    assert covers.fetch_cover(con, g, _fetch(["full1"]))


def test_auth_failure_does_not_raise_and_stops_igdb(con, monkeypatch):
    g = _game(con, "custom", "c3", "Anything")

    def boom(path, body):
        raise covers.IgdbAuthError("nope")
    monkeypatch.setattr(covers, "igdb_post", boom)
    assert covers.fetch_cover(con, g, _fetch([])) is False
    assert covers.IGDB_STATE["error"] == "nope"
    p = _post([])
    monkeypatch.setattr(covers, "igdb_post", p)
    covers.fetch_cover(con, _game(con, "custom", "c4", "Other"), _fetch([]))
    assert not p.calls                                    # coupé pour le reste de la passe


def test_no_credentials_means_no_igdb(con, monkeypatch):
    monkeypatch.setattr(covers, "igdb_credentials", lambda: ("", ""))
    p = _post([])
    monkeypatch.setattr(covers, "igdb_post", p)
    covers.fetch_cover(con, _game(con, "custom", "c5", "X"), _fetch([]))
    assert not p.calls


def test_existing_igdb_cover_survives_failed_retry(con, monkeypatch):
    g = _game(con, "custom", "c6", "Keeper")
    monkeypatch.setattr(covers, "igdb_post", _post([{"name": "Keeper", "cover": {"image_id": "k1"}}]))
    covers.fetch_cover(con, g, _fetch(["k1"]))
    g = con.execute("SELECT * FROM games WHERE ext_id='c6'").fetchone()
    p = _post([])
    monkeypatch.setattr(covers, "igdb_post", p)
    assert covers.fetch_cover(con, g, _fetch([]))         # l'ancienne couverture est gardée
    assert not p.calls
    assert con.execute("SELECT cover_src FROM games WHERE ext_id='c6'").fetchone()[0] == "igdb"


def test_credentials_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(covers, "DB_PATH", tmp_path / "games.db")
    monkeypatch.delenv("IGDB_CLIENT_ID", raising=False)
    monkeypatch.delenv("IGDB_CLIENT_SECRET", raising=False)
    covers.save_igdb_credentials("abc", "def")
    assert covers.igdb_credentials() == ("abc", "def")
    assert oct(covers.igdb_file().stat().st_mode & 0o777) == "0o600"
    covers.save_igdb_credentials("", "")
    assert covers.igdb_credentials() == ("", "")


def test_query_escapes_quotes(con, monkeypatch):
    p = _post([])
    monkeypatch.setattr(covers, "igdb_post", p)
    covers.igdb_find_image('The "Quoted" Game')
    assert 'search "The \\"Quoted\\" Game"' in p.calls[0]


def test_trademark_symbols_ignored_in_cover_search(monkeypatch):
    from core import covers
    assert covers.steamgrid_name("Tomb Raider™ Demo") == "Tomb Raider"
    assert covers.steamgrid_name("DOOM® Eternal™") == "DOOM Eternal"
    assert covers._norm("Tomb Raider™") == covers._norm("Tomb Raider")
    seen = []
    monkeypatch.setattr(covers, "sgdb_get", lambda path, params=None, timeout=15:
                        seen.append(path) or [{"id": 7, "name": "Hades"}])
    assert covers.sgdb_find_game("Hades™") == 7
    assert "™" not in seen[0] and "%E2%84%A2" not in seen[0]


def test_numbered_titles_accept_a_subtitle(monkeypatch):
    from core import covers
    res = [{"id": 1, "name": "Ultima Underworld II: Labyrinth of Worlds"},
           {"id": 2, "name": "Ultima Underworld: The Stygian Abyss"}]
    monkeypatch.setattr(covers, "sgdb_get", lambda path, params=None, timeout=15: res)
    assert covers.sgdb_find_game("Ultima™ Underworld I") == 2           # le 1 perd son numéro, pas le II
    assert covers.sgdb_find_game("Ultima™ Underworld II") == 1
    monkeypatch.setattr(covers, "sgdb_get", lambda *a, **k: [{"id": 9, "name": "Hades: Battle Out of Hell"}])
    assert covers.sgdb_find_game("Hades") is None                        # sans numéro : égalité stricte
    monkeypatch.setattr(covers, "sgdb_get", lambda *a, **k: [{"id": 5, "name": "Ultima Underworld II: Labyrinth of Worlds"}])
    assert covers.sgdb_find_game("Ultima Underworld I") is None          # « II » n'est pas le jeu 1


def test_igdb_uses_same_subtitle_rule():
    from core import covers
    rows = [{"name": "Ultima Underworld: The Stygian Abyss", "cover": {"image_id": "co5xjp"}}]
    assert covers.igdb_find_image("Ultima™ Underworld I", post=lambda *a: rows) == "co5xjp"


def test_base_title_is_searched_too(monkeypatch):
    from core import covers
    seen = []

    def fake(path, params=None, timeout=15):
        seen.append(path)
        return [{"id": 2, "name": "Ultima Underworld: The Stygian Abyss"}] if path.endswith("Underworld") else []
    monkeypatch.setattr(covers, "sgdb_get", fake)
    assert covers.sgdb_find_game("Ultima™ Underworld I") == 2 and len(seen) == 2


def test_fetch_missing_covers_and_hltb_target_every_game(monkeypatch):
    import sqlite3
    from core import covers, hltb
    from core.migrations import migrate
    c = sqlite3.connect(":memory:"); c.row_factory = sqlite3.Row; migrate(c)
    rows = [("a", 1, 0), ("b", 0, 0), ("c", 1, 1), ("d", 0, 1)]       # (nom, installé, masqué)
    for n, inst, hid in rows:
        c.execute("INSERT INTO games (source, ext_id, name, installed, hidden) VALUES ('custom', ?, ?, ?, ?)", (n, n, inst, hid))
    seen = []
    monkeypatch.setattr(covers, "fetch_cover", lambda con, g, fetch=None: seen.append(g["name"]) or True)
    monkeypatch.setattr(covers, "FETCH_COVERS", True)
    covers.fetch_missing_covers(c, delay=0)
    assert sorted(seen) == ["a", "b", "c", "d"]
    seen.clear()
    monkeypatch.setattr(hltb, "fetch_hltb", lambda con, g, http=None: seen.append(g["name"]) or True)
    hltb.fetch_missing(c, delay=0)
    assert sorted(seen) == ["a", "b", "c", "d"]
