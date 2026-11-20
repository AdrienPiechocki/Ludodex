"""Overlay : configuration, formats, placement, lectures de la base (sans GTK)."""
import datetime, json, time

import picker as P
from core import overlay as core_overlay
from overlay import config, data


def test_defaults_when_file_missing_or_broken(tmp_path):
    assert config.load(tmp_path / "nope.json") == config.normalize(None)
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert config.load(bad) == config.normalize(None)


def test_normalize_clamps_and_rejects_wrong_types():
    cfg = config.normalize({"dim": 9, "widgets": {
        "clock": {"opacity": 0, "x": -3, "mode": "bogus", "hour24": "yes", "size": 1e9},
        "notes": {"width": "big"}, "unknown": {"x": 1}}})
    c = cfg["widgets"]["clock"]
    assert cfg["dim"] == 0.8
    assert c["opacity"] == 0.2 and c["x"] == 0.0 and c["size"] == 120
    assert c["mode"] == "clock" and c["hour24"] is True
    assert cfg["widgets"]["notes"]["width"] == config.DEFAULTS["widgets"]["notes"]["width"]
    assert "unknown" not in cfg["widgets"]


def test_save_load_roundtrip(tmp_path):
    cfg = config.normalize(None)
    cfg["widgets"]["clock"]["mode"] = "stopwatch"
    cfg["widgets"]["notes"]["x"] = 0.25
    path = tmp_path / "sub" / "overlay.json"
    assert config.save(cfg, path)
    assert config.load(path) == cfg
    assert json.loads(path.read_text())["widgets"]["clock"]["mode"] == "stopwatch"


def test_clock_and_duration_formats():
    t = datetime.datetime(2026, 1, 2, 14, 5, 9)
    assert config.clock_text(t) == "14:05"
    assert config.clock_text(t, seconds=True) == "14:05:09"
    assert config.clock_text(t, hour24=False) == "2:05 PM"
    assert config.clock_text(t.replace(hour=0), hour24=False, seconds=True) == "12:05:09 AM"
    assert config.duration_text(754) == "12:34"
    assert config.duration_text(3725) == "1:02:05"
    assert config.duration_text(3725, seconds=False) == "1:02"
    assert config.duration_text(-5) == "00:00"


def test_place_keeps_widget_on_screen_and_inverts():
    assert config.place(0, 0, 1920, 1080, 200, 100) == (0, 0)
    assert config.place(1, 1, 1920, 1080, 200, 100) == (1720, 980)
    assert config.place(5, -2, 1920, 1080, 200, 100) == (1720, 0)       # bornée
    assert config.place(1, 1, 100, 100, 300, 300) == (0, 0)             # plus grand que l'écran
    fx, fy = config.fraction(860, 490, 1920, 1080, 200, 100)
    assert config.place(fx, fy, 1920, 1080, 200, 100) == (860, 490)
    assert config.fraction(-50, 5000, 1920, 1080, 200, 100) == (0.0, 1.0)


def _game(con):
    con.execute("INSERT INTO games (name, source, ext_id) VALUES ('G','custom','custom:g')")
    con.commit()
    return con.execute("SELECT id FROM games WHERE name='G'").fetchone()[0]


def test_session_and_unlock_reading():
    con = P.db()
    gid = _game(con)
    assert data.open_session(con, gid) is None
    con.execute("INSERT INTO sessions (game_id, started, ended, minutes) VALUES (?,?,0,0)", (gid, 1000))
    con.commit()
    sid, started = data.open_session(con, gid)
    assert started == 1000 and data.session_is_open(con, sid)
    P.close_session(con, sid, now=2000)
    assert not data.session_is_open(con, sid) and data.open_session(con, gid) is None

    before = data.unlocked(con, gid)
    assert before == {}
    con.execute("INSERT INTO achievements (game_id, ach_id, count, unlocked_at) VALUES (?,?,1,?)",
                (gid, "boss", int(time.time())))
    con.execute("INSERT INTO steam_achievements (game_id, ach_id, title, earned_at) VALUES (?,?,?,?)",
                (gid, "FIRST", "Premier sang", 5))
    con.execute("INSERT INTO steam_achievements (game_id, ach_id, title, earned_at) VALUES (?,?,?,0)",
                (gid, "LOCKED", "Pas encore"))
    con.commit()
    now = data.unlocked(con, gid)
    assert {a["title"] for a in data.new_unlocks(before, now)} == {"boss", "Premier sang"}
    assert data.new_unlocks(now, now) == []


def test_start_respects_setting_and_env(monkeypatch):
    con = P.db()
    calls = []
    monkeypatch.setattr(core_overlay, "_spawn", lambda args: calls.append(args) or True)
    assert core_overlay.start(con, 7) is True and calls == [["--game", "7"]]
    P.set_setting(con, "overlay_enabled", "0")
    assert core_overlay.start(con, 7) is False and len(calls) == 1


def test_spawn_is_blocked_by_env_guard(monkeypatch):
    # conftest pose LUDODEX_NO_OVERLAY : aucun test ne doit lancer un vrai overlay
    monkeypatch.setattr(core_overlay.subprocess, "Popen", lambda *a, **k: 1 / 0)
    assert core_overlay._spawn(["--toggle"]) is False
