import json
import sqlite3

import pytest

from core import hltb
from core.migrations import migrate

HOME = '<html><script src="/_next/static/chunks/main-abc.js"></script><script src="/_next/static/chunks/app-1.js"></script></html>'
JS = 'x=1;fetch("/api/find/xyz",{method:"POST",headers:{a:1}});'


class FakeHLTB:
    """Faux site : page d'accueil, script, init, recherche ; compte les requêtes."""

    def __init__(self, results, search_status=200, init_body=None):
        self.results, self.search_status = results, search_status
        self.init_body = init_body if init_body is not None else {"token": "T", "hpKey": "k", "hpVal": "v"}
        self.log = []

    def __call__(self, method, url, headers=None, body=None):
        self.log.append((method, url))
        if url.endswith("howlongtobeat.com/"):
            return 200, HOME
        if "main-abc.js" in url:
            return 200, "nothing here"
        if "app-1.js" in url:
            return 200, JS
        if "/api/find/xyz/init" in url:
            return 200, json.dumps(self.init_body)
        if url.endswith("/api/find/xyz") and method == "POST":
            self.sent = (headers, json.loads(body))
            return self.search_status, json.dumps({"data": self.results})
        if "/game/" in url:
            return (200, "<title>How long is Hades? | HowLongToBeat</title>") if "42" in url else (404, "")
        return 404, ""


def R(id_, name, main=36000, plus=72000, c100=144000, steam=0, alias=""):
    return {"game_id": id_, "game_name": name, "game_alias": alias, "comp_main": main,
            "comp_plus": plus, "comp_100": c100, "profile_steam": steam}


@pytest.fixture
def con():
    hltb._cache["endpoint"] = None
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    migrate(c)
    return c


def _game(con, name, source="custom", ext="x"):
    con.execute("INSERT INTO games (source, ext_id, name) VALUES (?,?,?)", (source, ext, name))
    return con.execute("SELECT * FROM games WHERE ext_id=?", (ext,)).fetchone()


def test_protocol_headers_and_payload(con):
    http = FakeHLTB([R(1, "Hades")])
    hltb.search("Hades", http)
    headers, payload = http.sent
    assert headers["x-auth-token"] == "T" and headers["x-hp-key"] == "k" and headers["x-hp-val"] == "v"
    assert payload["k"] == "v" and payload["searchTerms"] == ["Hades"]


def test_fetch_stores_minutes(con):
    g = _game(con, "Hades")
    assert hltb.fetch_hltb(con, g, FakeHLTB([R(7, "Hades II"), R(1, "Hades")]))
    r = con.execute("SELECT * FROM games").fetchone()
    assert (r["hltb_id"], r["hltb_main"], r["hltb_extra"], r["hltb_complete"]) == (1, 600, 1200, 2400)
    assert hltb.summary(r) == "Story 10 h · +Extras 20 h · 100 % 40 h"


def test_steam_appid_beats_name(con):
    g = _game(con, "Totally Different Title", "steam", "620")
    assert hltb.fetch_hltb(con, g, FakeHLTB([R(5, "Portal 2", steam=620)]))
    assert con.execute("SELECT hltb_id FROM games").fetchone()[0] == 5


def test_no_wrong_game_stored(con):
    g = _game(con, "Hades")
    assert not hltb.fetch_hltb(con, g, FakeHLTB([R(7, "Hades II"), R(8, "Hades' Star")]))
    r = con.execute("SELECT hltb_id, hltb_fetched FROM games").fetchone()
    assert r["hltb_id"] == 0 and r["hltb_fetched"] > 0


def test_sequels_with_different_numbers_not_confused(con):
    assert hltb.best_match("Game 2", [R(1, "Game 3") | {"alias": "", "name": "Game 3", "id": 1, "main": 0,
                                                       "extra": 0, "complete": 0, "steam": 0}]) is None


def test_alias_and_clean_name():
    e = [hltb._entry(R(3, "The Legend of Zelda: Breath of the Wild", alias="BotW, Zelda BOTW"))]
    assert hltb.best_match("BotW", e)["id"] == 3
    assert hltb.clean_name("Cool Game™ Demo") == "Cool Game"
    assert hltb.clean_name("Doom (Game of the Year Edition)") == "Doom"


def test_rate_limit_stops_loop_and_keeps_data(con):
    _game(con, "A", ext="a")
    _game(con, "B", ext="b")
    http = FakeHLTB([], search_status=429)
    msgs = []
    assert hltb.fetch_missing(con, delay=0, progress=msgs.append, http=http) == 0
    assert any(m.startswith("!") for m in msgs)
    assert con.execute("SELECT COUNT(*) FROM games WHERE hltb_fetched=0").fetchone()[0] == 2   # rien marqué


def test_format_change_is_an_hltb_error(con):
    http = FakeHLTB([], init_body={"nope": 1})
    with pytest.raises(hltb.HltbError):
        hltb.search("x", http)


def test_missing_only_and_retry_window(con):
    a = _game(con, "Hades", ext="a")
    _game(con, "Unknown Thing", ext="b")
    http = FakeHLTB([R(1, "Hades")])
    assert hltb.fetch_missing(con, delay=0, http=http) == 1
    n = len([c for c in http.log if c[0] == "POST"])
    assert hltb.fetch_missing(con, delay=0, http=http) == 0           # déjà tenté : pas de nouvelle requête
    assert len([c for c in http.log if c[0] == "POST"]) == n
    assert hltb.fetch_missing(con, delay=0, http=http, retry=True) == 0
    assert len([c for c in http.log if c[0] == "POST"]) == n + 1      # --retry : l'introuvable est retenté


def test_link_by_id_and_pinned_refresh(con):
    g = _game(con, "Whatever the user calls it")
    assert hltb.link(con, g["id"], 42, FakeHLTB([R(42, "Hades", main=3600)]))
    g = con.execute("SELECT * FROM games").fetchone()
    assert g["hltb_id"] == 42 and g["hltb_main"] == 60
    assert hltb.fetch_hltb(con, g, FakeHLTB([R(42, "Hades", main=7200), R(1, "Whatever the user calls it")]))
    assert con.execute("SELECT hltb_id, hltb_main FROM games").fetchone()[:] == (42, 120)   # reste sur sa fiche
    assert not hltb.link(con, g["id"], 999, FakeHLTB([]))


def test_manual_and_remaining(con):
    g = _game(con, "Local")
    hltb.set_manual(con, g["id"], main=2.5, complete=10)
    g = con.execute("SELECT * FROM games").fetchone()
    assert (g["hltb_main"], g["hltb_complete"]) == (150, 600) and g["hltb_fetched"] > 0
    assert hltb.remaining_minutes(g, 100) == 50 and hltb.remaining_minutes(g, 999) == 0
    assert hltb.fmt_hours(150) == "2.5 h" and hltb.fmt_hours(40) == "40 min" and hltb.fmt_hours(120) == "2 h"
    assert hltb.remaining_minutes(_game(con, "Z", ext="z"), 5) is None
