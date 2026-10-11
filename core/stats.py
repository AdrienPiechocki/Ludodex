#!/usr/bin/env python3
"""stats.py — statuts, frises horaires et statistiques de jeu."""
import datetime, time

from i18n import tr
from .db import get_setting
from .sources import source_enabled, source_cond
from .steam import steam_stats, steam_log_launches, backfill_steam_logs

STATUS_LABELS = {"en_cours": "In progress", "backlog": "To play", "pause": "Paused",
                 "abandonne": "Dropped", "termine": "Finished"}  # libellés affichés (clés de i18n)
PAUSE_AFTER_DAYS = 28  # 'en_cours' sans session depuis N jours -> 'pause' (automatique)
GROUP_GAP = 300  # frise : sessions successives d'un même jeu fusionnées si séparées de ≤ 5 min
RETRO_MIN = 10    # frise : seuil historique (l'ancien réglage booléen « retro_hide_short » valait 10 min)


def hide_short_min(con):
    """Seuil (minutes, 0..15) sous lequel une session est masquée de la frise ; 0 = tout afficher.

    Reprend l'ancien réglage booléen « retro_hide_short » (10 min) s'il n'y a pas de seuil explicite."""
    raw = get_setting(con, "retro_hide_min")
    if raw == "":
        return RETRO_MIN if get_setting(con, "retro_hide_short") == "1" else 0
    try:
        return max(0, min(15, int(raw)))
    except ValueError:
        return 0


def refresh_stats(con, logs=False):
    if not source_enabled(con, "steam"):
        return
    stats = steam_stats()
    if logs:
        backfill_steam_logs(con)
    for g in con.execute("SELECT id, ext_id FROM games WHERE source='steam'").fetchall():
        if g["ext_id"] in stats:
            pt, lp = stats[g["ext_id"]]
            con.execute("UPDATE games SET playtime_min=?, last_played=MAX(last_played, ?) WHERE id=?",
                        (pt, lp, g["id"]))
            if lp:
                con.execute("INSERT OR IGNORE INTO sessions (game_id, started, ended, minutes) VALUES (?, ?, ?, 0)", (g["id"], lp, lp))
    con.commit()


def status_label(status):
    return tr(STATUS_LABELS.get(status, status))


def fmt_minutes(m):
    return f"{m // 60} h {m % 60:02d}" if m >= 60 else f"{m} min"


def _day(value):
    return datetime.date.fromisoformat(value)


def play_days(con, gid=None):
    """Jours (locaux) où une session a commencé : tous jeux confondus, ou un jeu donné."""
    sql = "SELECT DISTINCT date(started,'unixepoch','localtime') AS d FROM sessions"
    args = ()
    if gid is not None:
        sql += " WHERE game_id=?"
        args = (gid,)
    return {_day(r["d"]) for r in con.execute(sql, args)}


def _run_length(days, start):
    n, d = 0, start
    while d in days:
        n += 1
        d -= datetime.timedelta(days=1)
    return n


def streak_for_game(con, gid, today=None):
    """Jours consécutifs joués ; la série reste vivante si tu as joué hier."""
    days = play_days(con, gid)
    if not days:
        return 0
    d = today or datetime.date.today()
    if d not in days:
        d -= datetime.timedelta(days=1)
    return _run_length(days, d)


def best_streak(con, gid):
    """Plus longue suite de jours consécutifs joués (record)."""
    days = play_days(con, gid)
    return max((_run_length(days, d) for d in days), default=0)


def week_timeline(con, now=None):
    """Frise de la semaine en cours (lundi → dimanche) : sessions par jour, triées par heure.
    Les sessions successives d'un même jeu séparées de 5 min ou moins sont fusionnées
    (dans un même jour ; seule la session directement précédente est prise en compte).
    Si le seuil « retro_hide_min » est > 0, les entrées (après fusion) de moins de ce
    nombre de minutes sont masquées et exclues des totaux."""
    now = time.time() if now is None else now
    today = datetime.date.fromtimestamp(now)
    monday = today - datetime.timedelta(days=today.weekday())
    buckets = {monday + datetime.timedelta(days=i): [] for i in range(7)}
    sql = "SELECT s.started, s.ended, s.minutes, g.name FROM sessions s JOIN games g ON g.id = s.game_id"
    cond = source_cond(con, "g")
    if cond:
        sql += " WHERE " + cond
    sql += " ORDER BY s.started"
    for r in con.execute(sql):
        d = datetime.date.fromtimestamp(r["started"])
        if d not in buckets:
            continue
        end = r["ended"] or (r["started"] + r["minutes"] * 60)
        day = buckets[d]
        prev = day[-1] if day else None
        if prev and prev["name"] == r["name"] and r["started"] - prev["end"] <= GROUP_GAP:
            prev["minutes"] += r["minutes"]
            prev["end"] = max(prev["end"], end)
            continue
        day.append({"name": r["name"], "start": r["started"], "minutes": r["minutes"], "end": end})
    thr = hide_short_min(con)
    if thr > 0:
        for d in buckets:
            buckets[d] = [e for e in buckets[d] if e["minutes"] >= thr]
    games = {e["name"] for d in buckets for e in buckets[d]}
    total = sum(e["minutes"] for d in buckets for e in buckets[d])
    days = [{"date": d, "entries": [{"name": e["name"], "start": e["start"], "minutes": e["minutes"]}
                                    for e in buckets[d]]} for d in sorted(buckets)]
    return {"start": monday, "today": today, "days": days,
            "game_count": len(games), "total_minutes": total}


def week_text(con, now=None):
    week = week_timeline(con, now=now)
    lines = [tr("Week of {date} · {n} game(s) · {time}",
                date=week["start"].strftime("%d/%m"), n=week["game_count"],
                time=fmt_minutes(week["total_minutes"]))]
    for day in week["days"]:
        lines.append(f"{day['date'].strftime('%a %d/%m')}")
        if not day["entries"]:
            lines.append("  " + tr("Nothing played"))
            continue
        for e in day["entries"]:
            t = time.strftime("%H:%M", time.localtime(e["start"]))
            extra = f" ({fmt_minutes(e['minutes'])})" if e["minutes"] else ""
            lines.append(f"  {t} · {e['name']}{extra}")
    return "\n".join(lines)


def auto_tidy(con):
    """Rangement automatique : un jeu 'en_cours' inactif depuis PAUSE_AFTER_DAYS passe en 'pause'."""
    limit = int(time.time()) - PAUSE_AFTER_DAYS * 86400
    rows = con.execute("SELECT id, name FROM games WHERE status='en_cours' AND last_played > 0 AND last_played < ?",
                       (limit,)).fetchall()
    for r in rows:
        con.execute("UPDATE games SET status='pause' WHERE id=?", (r["id"],))
    con.commit()
    names = [r["name"] for r in rows]
    if names:
        print(tr("→ paused (inactive for {days} d): {names}", days=PAUSE_AFTER_DAYS, names=", ".join(names)))
    return names
