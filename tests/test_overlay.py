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


def test_parsers():
    cr = W.parse_cyberrealm(json.dumps([{"wid": 3, "pid": 42, "title": "Game", "app_id": "g",
                                          "width": 1280, "height": 720, "hidden": True}]))
    assert cr == [W.Win(42, "Game", "g", 1280, 720, True, False, wid=3)]
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


def test_hyprland_and_kwin_report_the_active_window():
    hy = W.parse_hyprland(json.dumps([
        {"pid": 1, "size": [1, 1], "mapped": True, "workspace": {"name": "1"}, "focusHistoryID": 0},
        {"pid": 2, "size": [1, 1], "mapped": True, "workspace": {"name": "1"}, "focusHistoryID": 1}]))
    assert [w.active for w in hy] == [True, False]      # sert à départager deux fenêtres du même jeu


def test_pick_window_falls_back_to_processes_born_after_the_session():
    old = W.Win(10, "Ludodex", "io.github.Ludodex", 1920, 1088, False, False)
    konsole = W.Win(11, "Konsole", "konsole", 1920, 1088, False, True)
    game = W.Win(500, "OpenMW", "openmw", 1920, 1080, False, False)
    born = {10: 1000.0, 11: 1500.0, 500: 2005.0}
    assert W.pick_window([old, konsole, game], set(), "The Elder Scrolls III: Morrowind",
                         since=2000.0, started=born.get) is game           # ni PID ni titre : né après le début
    assert W.pick_window([old, konsole], set(), "Morrowind", since=2000.0, started=born.get) is None
    assert W.pick_window([old, game], set(), "Morrowind", since=None, started=born.get) is None
    assert W.pick_window([old, game], {10}, "x", since=2000.0, started=born.get) is old   # le PID prime


def test_confine_fenetre_et_plein_ecran():
    mons = [(0, 0, 1920, 1080), (1920, 0, 2560, 1440)]
    win = W.Win(1, "g", "g", 800, 600, False, False, 100, 50)
    assert W.confine(win, mons) == (0, (100, 50, 1020, 430))
    full = W.Win(1, "g", "g", 1920, 1080, False, False, 0, 0)
    assert W.confine(full, mons) == (0, (0, 0, 0, 0))
    second = W.Win(1, "g", "g", 1000, 700, False, False, 2000, 100)    # centre sur la 2e sortie
    assert W.confine(second, mons) == (1, (80, 100, 1480, 640))
    assert W.confine(W.Win(1, "g", "g", 800, 600, False, False), mons) is None     # position inconnue
    assert W.confine(None, mons) is None


def test_positions_poussees_par_les_compositeurs():
    hy = W.parse_hyprland(json.dumps([{"pid": 1, "size": [800, 600], "at": [10, 20], "mapped": True}]))
    assert (hy[0].x, hy[0].y) == (10, 20)
    kw = W.parse_kwin(json.dumps([{"pid": 1, "width": 10, "height": 20, "x": 5, "y": 6}]))
    assert (kw[0].x, kw[0].y) == (5, 6)


def test_cyberrealm_position_en_mode_focus():
    cr = W.parse_cyberrealm(json.dumps([{"pid": 1, "width": 800, "height": 600, "x": 560, "y": 240},
                                        {"pid": 2, "width": 5, "height": 5}]))
    assert (cr[0].x, cr[0].y) == (560, 240) and (cr[1].x, cr[1].y) == (None, None)


def test_kwin_fenetre_recouverte():
    kw = W.parse_kwin(json.dumps([{"pid": 1, "covered": True}, {"pid": 2}]))
    assert kw[0].covered is True and kw[1].covered is False


def _hypr(clients, monitors):
    return json.dumps(clients) + W.HYPR_SEP + json.dumps(monitors)


def test_hyprland_fenetre_recouverte():
    mons = [{"activeWorkspace": {"id": 1}}]
    game = {"pid": 1, "class": "g", "title": "g", "at": [100, 100], "size": [800, 600], "mapped": True,
            "workspace": {"id": 1, "name": "1"}, "focusHistoryID": 1}
    # une autre fenêtre du même workspace, focalisée plus récemment, qui chevauche le jeu
    above = dict(game, pid=2, at=[700, 100], size=[600, 400], focusHistoryID=0)
    assert [w.covered for w in W.parse_hyprland(_hypr([game, above], mons))] == [True, False]
    assert W.parse_hyprland(_hypr([game], mons))[0].covered is False
    # même workspace mais l'autre fenêtre est derrière (focus plus ancien) : le jeu reste visible
    behind = dict(above, focusHistoryID=5)
    assert W.parse_hyprland(_hypr([game, behind], mons))[0].covered is False
    # aucun chevauchement
    apart = dict(above, at=[1000, 800], size=[200, 100])
    assert W.parse_hyprland(_hypr([game, apart], mons))[0].covered is False
    # même processus (dialogue du jeu) : ne recouvre pas
    assert W.parse_hyprland(_hypr([game, dict(above, pid=1)], mons))[0].covered is False
    # workspace non affiché : recouverte
    other_ws = dict(game, workspace={"id": 2, "name": "2"})
    assert W.parse_hyprland(_hypr([other_ws], mons))[0].covered is True
    # sans la liste des écrans : on ne sait pas, donc visible
    assert W.parse_hyprland(json.dumps([other_ws]))[0].covered is False


def test_cyberrealm_covered():
    cr = W.parse_cyberrealm(json.dumps([{"pid": 1, "covered": True}, {"pid": 2}]))
    assert [w.covered for w in cr] == [True, False]


def test_cyberrealm_wid_pour_lier_l_overlay():
    cr = W.parse_cyberrealm(json.dumps([{"pid": 1, "wid": 17}, {"pid": 2}]))
    assert (cr[0].wid, cr[1].wid) == (17, None)


def test_pick_window_cyberrealm_pid_inconnu():
    old = W.Win(5, "Konsole", "konsole", 1920, 1080, False, False, wid=16)
    game = W.Win(-1, "Weird Title", "", 1280, 720, False, False, wid=40)       # X11 : pid introuvable
    dialog = W.Win(-1, "dlg", "", 300, 200, False, False, wid=41)
    pre = W.Win(-1, "avant", "", 1920, 1080, False, False, wid=12)             # présente avant le suivi
    assert W.pick_window([old, pre, dialog, game], {999}, "Other", wid_floor=16) == game
    assert W.pick_window([old, pre], {999}, "Other", wid_floor=16) is None
    assert W.pick_window([old, game], {999}, "Other") is None                   # sans plancher : inchangé


def test_spawn_flatpak_sous_cyberrealm_part_de_l_hote(monkeypatch):
    """Dans Flatpak, avec CyberRealm sur l'hôte : `cyberrealm-launch flatpak run …` via flatpak-spawn."""
    import subprocess
    from core import platform as plat
    seen = []
    monkeypatch.setenv("FLATPAK_ID", "io.example.Ludodex")
    monkeypatch.delenv("LUDODEX_NO_OVERLAY", raising=False)
    monkeypatch.setattr(core_overlay, "command", lambda: ["/app/bin/ludodex-overlay"])
    monkeypatch.setattr(core_overlay, "_cyberrealm_on_host", lambda: True)
    monkeypatch.setattr(plat, "cyberrealm_wrapper", lambda: ["/home/u/.local/bin/cyberrealm-launch"])
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **kw: seen.append(argv))
    assert core_overlay._spawn(["--game", "3", "--pid", "9"]) is True
    argv = seen[0]
    assert argv[:3] == ["flatpak-spawn", "--host", "sh"]
    assert "--env=LUDODEX_OVERLAY_TRACKER=cyberrealm" in argv and argv[-4:] == ["--game", "3", "--pid", "9"]
    assert argv[argv.index("flatpak", 3) - 1].endswith("cyberrealm-launch")
