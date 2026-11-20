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


# ---------- fenêtre du jeu : échelle, réduite, reconnaissance ----------
from overlay import windows as W


def test_scale_follows_height_and_is_bounded():
    assert W.scale_for(1080) == 1.0
    assert W.scale_for(540) == 0.5
    assert W.scale_for(2160) == 2.0
    assert W.scale_for(100) == W.SCALE_MIN and W.scale_for(99999) == W.SCALE_MAX
    assert W.scale_for(0) == 1.0 and W.scale_for(None) == 1.0       # inconnu : pas de changement
    assert W.scale_for(720, reference=720) == 1.0


def test_config_has_scale_settings():
    c = config.normalize({"reference_height": 10, "scale_with_game": "no"})
    assert c["reference_height"] == 360 and c["scale_with_game"] is True


def test_parsers():
    cr = W.parse_cyberrealm(json.dumps([{"wid": 3, "pid": 42, "title": "Game", "app_id": "g",
                                          "width": 1280, "height": 720, "hidden": True}]))
    assert cr == [W.Win(42, "Game", "g", 1280, 720, True, False)]
    hy = W.parse_hyprland(json.dumps([
        {"pid": 7, "title": "T", "class": "c", "size": [1920, 1080], "mapped": True,
         "hidden": False, "workspace": {"name": "1"}},
        {"pid": 8, "title": "S", "class": "s", "size": [800, 600], "mapped": True,
         "hidden": False, "workspace": {"name": "special:magic"}},
        {"pid": 9, "size": [1, 1], "mapped": False}]))
    assert [(w.pid, w.minimized) for w in hy] == [(7, False), (8, True)]
    kw = W.parse_kwin(json.dumps([{"pid": 1, "title": "x", "app_id": "y", "width": 10, "height": 20,
                                    "minimized": True, "active": True}]))
    assert kw[0].minimized and kw[0].active
    assert W.parse_kwin("not json") == W.parse_cyberrealm("{}") == W.parse_hyprland("") == []


def test_pick_window_prefers_pid_then_name():
    a = W.Win(100, "Launcher", "steam", 400, 300, False, False)
    b = W.Win(200, "Hollow", "hk", 1920, 1080, False, False)
    c = W.Win(200, "Hollow dialog", "hk", 300, 200, False, False)
    d = W.Win(300, "Other", "o", 800, 600, False, False)
    assert W.pick_window([a, b, c, d], {200}, "Hollow Knight") is b          # même PID : la plus grande
    assert W.pick_window([a, d], {999}, "Launcher stuff") is None            # rien ne correspond
    assert W.pick_window([a, b], set(), "abc") is None                       # nom trop court : pas de devinette
    named = W.Win(5, "Hollow Knight", "x", 10, 10, False, False)
    assert W.pick_window([a, named], set(), "Hollow Knight") is named


def test_descendants_and_detect():
    tree = {1: [2, 3], 2: [4], 4: [5], 9: [10]}
    assert W.descendants(1, tree) == {1, 2, 3, 4, 5}
    assert W.descendants(7, tree) == {7}
    assert W.detect({"WAYLAND_DISPLAY": "cyberrealm-0"}) == "cyberrealm"
    assert W.detect({"HYPRLAND_INSTANCE_SIGNATURE": "x"}) == "hyprland"
    assert W.detect({"XDG_CURRENT_DESKTOP": "KDE"}) == "kwin"
    assert W.detect({"XDG_CURRENT_DESKTOP": "sway"}) is None


def test_start_passes_pid(monkeypatch):
    con = P.db()
    calls = []
    monkeypatch.setattr(core_overlay, "_spawn", lambda args: calls.append(args) or True)
    P.set_setting(con, "overlay_enabled", "1")
    core_overlay.start(con, 3, 4242)
    assert calls == [["--game", "3", "--pid", "4242"]]
