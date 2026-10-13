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


def _timeline(con, period_start, period_end, bucket, today, game=None):
    """Entrées de la frise entre period_start et period_end (dates incluses).

    `bucket(date)` ramène une date à sa colonne (le jour lui-même pour une frise
    par jour, le 1er du mois pour une frise par mois). Les sessions d'un même jeu
    séparées de ≤ GROUP_GAP sont fusionnées dans la même colonne. Le seuil
    « retro_hide_min » (0 = tout montrer) masque les entrées trop courtes, totaux
    compris. Toutes les colonnes de la période existent, vides ou non.
    `game` (optionnel) filtre sur une sous-chaîne du nom, insensible à la casse :
    plusieurs jeux correspondants sont cumulés — appliqué AVANT fusion et seuil.
    """
    buckets = {}
    needle = game.strip().lower() if game else ""
    sql = ("SELECT s.started, s.ended, s.minutes, g.name FROM sessions s "
           "JOIN games g ON g.id = s.game_id")
    cond = source_cond(con, "g")
    if cond:
        sql += " WHERE " + cond
    sql += " ORDER BY s.started"
    for r in con.execute(sql):
        if needle and needle not in r["name"].lower():
            continue
        d = datetime.date.fromtimestamp(r["started"])
        if d < period_start or d > period_end:
            continue
        key = bucket(d)
        if key < bucket(period_start) or key > bucket(period_end):
            continue  # défensif : session en dehors des colonnes de la période
        if r["ended"]:
            end = r["ended"]
            mins = r["minutes"]
        else:
            end = int(time.time())
            mins = max(0, (end - r["started"]) // 60)
        col = buckets.setdefault(key, [])
        prev = col[-1] if col else None
        if prev and prev["name"] == r["name"] and r["started"] - prev["end"] <= GROUP_GAP:
            prev["minutes"] += mins
            prev["end"] = max(prev["end"], end)
            continue
        col.append({"name": r["name"], "start": r["started"], "minutes": mins, "end": end})
    thr = hide_short_min(con)
    if thr > 0:
        for k in list(buckets):
            buckets[k] = [e for e in buckets[k] if e["minutes"] >= thr]
    games = {e["name"] for col in buckets.values() for e in col}
    total = sum(e["minutes"] for col in buckets.values() for e in col)
    return buckets, len(games), total


def _public(entries):
    """Entrées de frise sans la borne interne de fusion (fin de session)."""
    return [{"name": e["name"], "start": e["start"], "minutes": e["minutes"], "end": e.get("end", e["start"] + e["minutes"] * 60)} for e in entries]


def week_timeline(con, now=None, game=None):
    """Frise de la semaine en cours (lundi → dimanche) : sessions par jour, triées par heure.
    Les sessions successives d'un même jeu séparées de 5 min ou moins sont fusionnées
    (dans un même jour ; seule la session directement précédente est prise en compte).
    Si le seuil « retro_hide_min » est > 0, les entrées (après fusion) de moins de ce
    nombre de minutes sont masquées et exclues des totaux. `game` filtre sur le nom."""
    now = time.time() if now is None else now
    today = datetime.date.fromtimestamp(now)
    monday = today - datetime.timedelta(days=today.weekday())
    days = [monday + datetime.timedelta(days=i) for i in range(7)]
    buckets, ngames, total = _timeline(con, days[0], days[-1], lambda d: d, today, game)
    return {"start": monday, "today": today,
            "days": [{"date": d, "entries": _public(buckets.get(d, []))} for d in days],
            "game_count": ngames, "total_minutes": total}


def month_timeline(con, now=None, game=None):
    """Frise du mois en cours (1er → dernier jour du mois) : sessions par jour.
    Mêmes règles de fusion que week_timeline ; l'actualité (« today ») sert au rendu."""
    now = time.time() if now is None else now
    today = datetime.date.fromtimestamp(now)
    first = today.replace(day=1)
    last = (first + datetime.timedelta(days=32)).replace(day=1) - datetime.timedelta(days=1)
    days = [first + datetime.timedelta(days=i) for i in range((last - first).days + 1)]
    buckets, ngames, total = _timeline(con, first, last, lambda d: d, today, game)
    return {"start": first, "today": today,
            "days": [{"date": d, "entries": _public(buckets.get(d, []))} for d in days],
            "game_count": ngames, "total_minutes": total}


def year_timeline(con, now=None, game=None):
    """Frise de l'année en cours (janvier → décembre) : sessions par mois.
    Les sessions d'un même mois sont fusionnées sur les mêmes règles."""
    now = time.time() if now is None else now
    today = datetime.date.fromtimestamp(now)
    jan1 = today.replace(month=1, day=1)
    months = [datetime.date(today.year, m, 1) for m in range(1, 13)]
    buckets, ngames, total = _timeline(con, jan1, months[-1], lambda d: d.replace(day=1), today, game)
    return {"start": jan1, "today": today,
            "months": [{"date": m, "entries": _public(buckets.get(m, []))} for m in months],
            "game_count": ngames, "total_minutes": total}


# ---------- statistiques de période (résumé, top, histogrammes, régularité, heatmap) ----------
# Toutes ces fonctions rejouent les règles de la frise via _timeline : sessions fusionnées
# (écart ≤ GROUP_GAP) puis seuil « retro_hide_min » appliqué — une seule source de vérité.

def period_bounds(kind, now=None):
    """Bornes (dates incluses) de la période courante (week|month|year)."""
    today = datetime.date.fromtimestamp(time.time() if now is None else now)
    if kind == "week":
        monday = today - datetime.timedelta(days=today.weekday())
        return monday, monday + datetime.timedelta(days=6)
    if kind == "month":
        first = today.replace(day=1)
        last = (first + datetime.timedelta(days=32)).replace(day=1) - datetime.timedelta(days=1)
        return first, last
    return today.replace(month=1, day=1), today.replace(month=12, day=31)


def previous_bounds(kind, now=None):
    """Bornes de la période précédente de même nature (semaine / mois / année civiles)."""
    start, end = period_bounds(kind, now)
    if kind == "week":
        return start - datetime.timedelta(days=7), end - datetime.timedelta(days=7)
    if kind == "month":
        prev_last = start - datetime.timedelta(days=1)
        return prev_last.replace(day=1), prev_last
    return start.replace(year=start.year - 1), end.replace(year=end.year - 1)


def _entries(con, start, end, game=None):
    """Entrées fusionnées + seuil appliqué sur [start, end], colonnes d'un jour."""
    buckets, _, _ = _timeline(con, start, end, lambda d: d, end, game)
    return [e for col in buckets.values() for e in col]


def period_summary(con, kind, now=None, game=None):
    """Totaux de la période : minutes, sessions, jeux, jours joués, moyenne, plus longue session.

    « prev » reprend les mêmes clés pour la période précédente (comparaison).
    `game` filtre sur le nom (sous-chaîne, casse ignorée), comme la frise."""

    def _agg(entries, days):
        total = sum(e["minutes"] for e in entries)
        longest = max(entries, key=lambda e: e["minutes"], default=None)
        return {"total_minutes": total,
                "sessions": len(entries),
                "games": len({e["name"] for e in entries}),
                "days_played": len({datetime.date.fromtimestamp(e["start"]) for e in entries}),
                "period_days": days,
                "avg_minutes": total // len(entries) if entries else 0,
                "longest": ({"name": longest["name"], "minutes": longest["minutes"],
                             "start": longest["start"]} if longest else None)}

    start, end = period_bounds(kind, now)
    pstart, pend = previous_bounds(kind, now)
    return {**_agg(_entries(con, start, end, game), (end - start).days + 1),
            "prev": _agg(_entries(con, pstart, pend, game), (pend - pstart).days + 1)}


def top_games(con, kind, now=None, limit=5, game=None):
    """Top « limit » jeux de la période par minutes, avec rang, part et évolution.

    Chaque ligne porte « prev_rank » : rang complet du même jeu sur la période précédente
    (None = ★ nouveau). « others » agrège le reste du classement. `game` filtre sur le nom."""
    start, end = period_bounds(kind, now)
    entries = _entries(con, start, end, game)
    total = sum(e["minutes"] for e in entries)
    agg = {}
    for e in entries:
        a = agg.setdefault(e["name"], {"name": e["name"], "minutes": 0, "sessions": 0})
        a["minutes"] += e["minutes"]
        a["sessions"] += 1
    ranked = sorted(agg.values(), key=lambda a: -a["minutes"])
    pstart, pend = previous_bounds(kind, now)
    prev_minutes = {}
    for e in _entries(con, pstart, pend, game):
        prev_minutes[e["name"]] = prev_minutes.get(e["name"], 0) + e["minutes"]
    prev_rank = {name: i + 1 for i, name in enumerate(
        sorted(prev_minutes, key=lambda n: -prev_minutes[n]))}
    top = []
    for i, a in enumerate(ranked[:limit]):
        a["rank"] = i + 1
        a["share"] = round(a["minutes"] * 100 / total) if total else 0
        a["prev_rank"] = prev_rank.get(a["name"])
        top.append(a)
    rest = ranked[limit:]
    return {"top": top, "total_minutes": total,
            "others": {"games": len(rest), "minutes": sum(a["minutes"] for a in rest),
                       "sessions": sum(a["sessions"] for a in rest)}}


def _time_buckets(con, kind, now, nbuckets, key, game=None):
    """Agrège les entrées de la période courante et de la précédente par clé (heure ou jour)."""

    def _buckets(entries):
        out = [{"sessions": 0, "minutes": 0} for _ in range(nbuckets)]
        for e in entries:
            b = out[key(e)]
            b["sessions"] += 1
            b["minutes"] += e["minutes"]
        return out

    start, end = period_bounds(kind, now)
    pstart, pend = previous_bounds(kind, now)
    return {"current": _buckets(_entries(con, start, end, game)),
            "previous": _buckets(_entries(con, pstart, pend, game))}


def hours_histogram(con, kind, now=None, game=None):
    """24 créneaux horaires (heure locale de début) : sessions et minutes, période courante
    et précédente (« previous ») pour la comparaison."""
    return _time_buckets(con, kind, now, 24,
                         lambda e: datetime.datetime.fromtimestamp(e["start"]).hour, game)


def weekdays_histogram(con, kind, now=None, game=None):
    """7 jours de la semaine (lundi d'abord) : sessions et minutes, période courante et précédente."""
    return _time_buckets(con, kind, now, 7,
                         lambda e: datetime.datetime.fromtimestamp(e["start"]).weekday(), game)


def _played_days(con, until, game=None):
    """Jours (≤ until) comptant au moins une session retenue par le seuil de la frise."""
    buckets, _, _ = _timeline(con, datetime.date(1970, 1, 1), until, lambda d: d, until, game)
    return {d for d, col in buckets.items() if col}


def _streak_ending(played, cutoff):
    """Longueur de la série de jours consécutifs se terminant sur cutoff (0 si non joué)."""
    n, d = 0, cutoff
    while d in played:
        n += 1
        d -= datetime.timedelta(days=1)
    return n


def _longest_streak(played, until):
    """Plus longue série de jours consécutifs joués parmi les jours ≤ until."""
    best = run = 0
    prev = None
    for d in sorted(x for x in played if x <= until):
        run = run + 1 if prev is not None and (d - prev).days == 1 else 1
        best = max(best, run)
        prev = d
    return best


def _longest_gap(played, start, end):
    """Plus long arrêt (jours non joués) entre deux jours joués, dans [start, end]."""
    days = sorted(d for d in played if start <= d <= end)
    return max(((b - a).days - 1 for a, b in zip(days, days[1:])), default=0)


def regularity(con, kind, now=None, game=None):
    """Régularité : jours joués, série en cours, plus longue série et plus long arrêt.

    Les séries se mesurent sur toute l'histoire des jours joués jusqu'à la fin de la période
    (bornée à aujourd'hui si la période est en cours) ; « prev » reprend les mêmes mesures
    arrêtées à la fin de la période précédente. Le « plus long arrêt » ne compte qu'à
    l'intérieur de la période, entre deux jours joués. `game` filtre sur le nom."""
    now = time.time() if now is None else now
    today = datetime.date.fromtimestamp(now)
    start, end = period_bounds(kind, now)
    pstart, pend = previous_bounds(kind, now)
    played = _played_days(con, min(end, today), game)

    def _agg(cutoff, s, e):
        return {"days_played": len([d for d in played if s <= d <= e]),
                "period_days": (e - s).days + 1,
                "streak": _streak_ending(played, cutoff),
                "longest_streak": _longest_streak(played, cutoff),
                "longest_gap": _longest_gap(played, s, cutoff)}

    return {**_agg(min(end, today), start, end), "prev": _agg(pend, pstart, pend)}


def heatmap(con, kind, now=None, game=None):
    """Jours de la période (week|month|year) : détail par jour pour l'infobulle.

    Chaque cellule porte « minutes » (total), « sessions » (nombre) et « games » (liste
    {name, minutes} triée par temps décroissant), le tout après fusion et seuil de frise.
    Les jours futurs (après aujourd'hui) ont « minutes » = None ; les jours passés ou le jour
    en cours sans session retenue valent 0 (sessions 0, games vide). `game` filtre sur le nom."""
    now = time.time() if now is None else now
    today = datetime.date.fromtimestamp(now)
    start, end = period_bounds(kind, now)
    cutoff = min(end, today)
    buckets, _, _ = _timeline(con, start, cutoff, lambda d: d, cutoff, game)
    cells, d = [], start
    while d <= end:
        entries = buckets.get(d, []) if d <= cutoff else []
        per_game = {}
        for e in entries:
            per_game[e["name"]] = per_game.get(e["name"], 0) + e["minutes"]
        cells.append({"date": d,
                      "minutes": sum(per_game.values()) if d <= cutoff else None,
                      "sessions": len(entries),
                      "games": [{"name": n, "minutes": m}
                                for n, m in sorted(per_game.items(), key=lambda kv: -kv[1])]})
        d += datetime.timedelta(days=1)
    return cells


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
_WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def month_name(d):
    """Nom du mois dans la langue de l'app : « Octobre » / « October »."""
    return tr(_MONTHS[d.month - 1])


def month_short(d):
    """Nom de mois abrégé : « oct. » / « Oct »."""
    return tr(_MONTHS_SHORT[d.month - 1])


def weekday_short(d):
    """Jour abrégé (dimanche=0) : « dim. » / « Sun »."""
    return tr(_WEEKDAYS_SHORT[(d.weekday() + 1) % 7])


def weekday_name(d):
    """Nom complet du jour (lundi=0) : « samedi » / « Saturday »."""
    return tr(_WEEKDAYS[d.weekday()])


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
                t = time.strftime("%d/%m %H:%M", time.localtime(e["start"]))
                extra = f" ({fmt_minutes(e['minutes'])})" if e["minutes"] else ""
                lines.append(f"  {t} · {e['name']}{extra}")
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
