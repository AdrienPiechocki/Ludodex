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


def _timeline(con, period_start, period_end, bucket, today):
    """Entrées de la frise entre period_start et period_end (dates incluses).

    `bucket(date)` ramène une date à sa colonne (le jour lui-même pour une frise
    par jour, le 1er du mois pour une frise par mois). Les sessions d'un même jeu
    séparées de ≤ GROUP_GAP sont fusionnées dans la même colonne. Le seuil
    « retro_hide_min » (0 = tout montrer) masque les entrées trop courtes, totaux
    compris. Toutes les colonnes de la période existent, vides ou non.
    """
    buckets = {}
    sql = ("SELECT s.started, s.ended, s.minutes, g.name FROM sessions s "
           "JOIN games g ON g.id = s.game_id")
    cond = source_cond(con, "g")
    if cond:
        sql += " WHERE " + cond
    sql += " ORDER BY s.started"
    for r in con.execute(sql):
        d = datetime.date.fromtimestamp(r["started"])
        if d < period_start or d > period_end:
            continue
        key = bucket(d)
        if key < bucket(period_start) or key > bucket(period_end):
            continue  # défensif : session en dehors des colonnes de la période
        end = r["ended"] or (r["started"] + r["minutes"] * 60)
        col = buckets.setdefault(key, [])
        prev = col[-1] if col else None
        if prev and prev["name"] == r["name"] and r["started"] - prev["end"] <= GROUP_GAP:
            prev["minutes"] += r["minutes"]
            prev["end"] = max(prev["end"], end)
            continue
        col.append({"name": r["name"], "start": r["started"], "minutes": r["minutes"], "end": end})
    thr = hide_short_min(con)
    if thr > 0:
        for k in list(buckets):
            buckets[k] = [e for e in buckets[k] if e["minutes"] >= thr]
    games = {e["name"] for col in buckets.values() for e in col}
    total = sum(e["minutes"] for col in buckets.values() for e in col)
    return buckets, len(games), total


def _public(entries):
    """Entrées de frise sans la borne interne de fusion (fin de session)."""
    return [{"name": e["name"], "start": e["start"], "minutes": e["minutes"]} for e in entries]


def week_timeline(con, now=None):
    """Frise de la semaine en cours (lundi → dimanche) : sessions par jour, triées par heure.
    Les sessions successives d'un même jeu séparées de 5 min ou moins sont fusionnées
    (dans un même jour ; seule la session directement précédente est prise en compte).
    Si le seuil « retro_hide_min » est > 0, les entrées (après fusion) de moins de ce
    nombre de minutes sont masquées et exclues des totaux."""
    now = time.time() if now is None else now
    today = datetime.date.fromtimestamp(now)
    monday = today - datetime.timedelta(days=today.weekday())
    days = [monday + datetime.timedelta(days=i) for i in range(7)]
    buckets, ngames, total = _timeline(con, days[0], days[-1], lambda d: d, today)
    return {"start": monday, "today": today,
            "days": [{"date": d, "entries": _public(buckets.get(d, []))} for d in days],
            "game_count": ngames, "total_minutes": total}


def month_timeline(con, now=None):
    """Frise du mois en cours (1er → dernier jour du mois) : sessions par jour.
    Mêmes règles de fusion que week_timeline ; l'actualité (« today ») sert au rendu."""
    now = time.time() if now is None else now
    today = datetime.date.fromtimestamp(now)
    first = today.replace(day=1)
    last = (first + datetime.timedelta(days=32)).replace(day=1) - datetime.timedelta(days=1)
    days = [first + datetime.timedelta(days=i) for i in range((last - first).days + 1)]
    buckets, ngames, total = _timeline(con, first, last, lambda d: d, today)
    return {"start": first, "today": today,
            "days": [{"date": d, "entries": _public(buckets.get(d, []))} for d in days],
            "game_count": ngames, "total_minutes": total}


def year_timeline(con, now=None):
    """Frise de l'année en cours (janvier → décembre) : sessions par mois.
    Les sessions d'un même mois sont fusionnées sur les mêmes règles."""
    now = time.time() if now is None else now
    today = datetime.date.fromtimestamp(now)
    jan1 = today.replace(month=1, day=1)
    months = [datetime.date(today.year, m, 1) for m in range(1, 13)]
    buckets, ngames, total = _timeline(con, jan1, months[-1], lambda d: d.replace(day=1), today)
    return {"start": jan1, "today": today,
            "months": [{"date": m, "entries": _public(buckets.get(m, []))} for m in months],
            "game_count": ngames, "total_minutes": total}


def retro_enabled(con, kind):
    """Vrai si une frise de la rétrospective (week|month|year) est affichée ; activée par défaut."""
    return get_setting(con, f"retro_enable_{kind}") != "0"


# Noms de mois et de jours i18n : les clés de tr() sont les formes anglaises, traduites
# par lang/<code>.json — on ignore ainsi la locale système (strftime), qui fausse la langue.
_MONTHS = ["January", "February", "March", "April", "May", "June",
           "July", "August", "September", "October", "November", "December"]
_MONTHS_SHORT = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                 "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_WEEKDAYS_SHORT = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]


def month_name(d):
    """Nom du mois dans la langue de l'app : « Octobre » / « October »."""
    return tr(_MONTHS[d.month - 1])


def month_short(d):
    """Nom de mois abrégé : « oct. » / « Oct »."""
    return tr(_MONTHS_SHORT[d.month - 1])


def weekday_short(d):
    """Jour abrégé (dimanche=0) : « dim. » / « Sun »."""
    return tr(_WEEKDAYS_SHORT[(d.weekday() + 1) % 7])


def _head_date(kind, d):
    if kind == "year":
        return str(d.year)
    if kind == "month":
        return month_name(d)
    return d.strftime("%d/%m")


def _col_label(kind, d):
    if kind == "year":
        return month_short(d)
    if kind == "month":
        return d.strftime("%d/%m")
    return f"{weekday_short(d)} {d.strftime('%d/%m')}"


_timeline_text = {  # descriptifs de frise : clé i18n et unité
    "week":  ("Week of {date} · {n} game(s) · {time}", "days"),
    "month": ("Month of {date} · {n} game(s) · {time}", "days"),
    "year":  ("Year {date} · {n} game(s) · {time}", "months"),
}


def period_text(con, kind, now=None):
    """Texte d'une frise (week|month|year) : en-tête puis une ligne par colonne."""
    fns = {"week": week_timeline, "month": month_timeline, "year": year_timeline}
    tl = fns[kind](con, now=now)
    head, unit = _timeline_text[kind]
    lines = [tr(head, date=_head_date(kind, tl["start"]), n=tl["game_count"],
                time=fmt_minutes(tl["total_minutes"]))]
    for col in tl[unit]:
        lines.append(_col_label(kind, col["date"]))
        if not col["entries"]:
            lines.append("  " + tr("Nothing played"))
            continue
        for e in col["entries"]:
            if unit == "days":
                t = time.strftime("%H:%M", time.localtime(e["start"]))
                extra = f" ({fmt_minutes(e['minutes'])})" if e["minutes"] else ""
                lines.append(f"  {t} · {e['name']}{extra}")
            else:
                lines.append(f"  {e['name']}" + (f" ({fmt_minutes(e['minutes'])})" if e["minutes"] else ""))
    return "\n".join(lines)


def week_text(con, now=None):
    return period_text(con, "week", now=now)


def month_text(con, now=None):
    return period_text(con, "month", now=now)


def year_text(con, now=None):
    return period_text(con, "year", now=now)


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
