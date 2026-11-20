"""data.py — lectures de la base Ludodex utiles à l'overlay (sans GTK, testables).

L'overlay ne scanne rien lui-même : l'application principale relit déjà les logs
toutes les 5 s et écrit les succès en base. L'overlay observe seulement ces tables."""
from core import tracker

TROPHY = "🏆"


def game(con, gid):
    return con.execute("SELECT id, name FROM games WHERE id=?", (gid,)).fetchone()


def open_session(con, gid):
    """(id, début) de la session ouverte du jeu, ou None."""
    r = con.execute("SELECT id, started FROM sessions WHERE game_id=? AND ended=0 "
                    "ORDER BY started DESC, id DESC LIMIT 1", (gid,)).fetchone()
    return (r["id"], r["started"]) if r else None


def session_is_open(con, sid):
    return con.execute("SELECT 1 FROM sessions WHERE id=? AND ended=0", (sid,)).fetchone() is not None


def unlocked(con, gid):
    """{(source, id): {title, description, icon}} des succès acquis, trois sources
    confondues (suivi YAML, RetroAchievements, Steam)."""
    out = {}
    defs = {}
    cfg = tracker.tracker_paths(con, gid)
    if cfg:
        defs = {d["id"]: d for d in (tracker._cached_defs(cfg[0]) or [])}
    for r in con.execute("SELECT ach_id FROM achievements WHERE game_id=? AND unlocked_at>0", (gid,)):
        d = defs.get(r["ach_id"], {})
        out[("log", r["ach_id"])] = {"title": d.get("name") or r["ach_id"],
                                     "description": d.get("description") or "",
                                     "icon": d.get("icon") or TROPHY}
    for src, table, col in (("ra", "ra_achievements", "earned_at"),
                            ("steam", "steam_achievements", "earned_at")):
        for r in con.execute(f"SELECT ach_id, title, description FROM {table} "
                             f"WHERE game_id=? AND {col}>0", (gid,)):
            out[(src, r["ach_id"])] = {"title": r["title"] or r["ach_id"],
                                       "description": r["description"] or "", "icon": TROPHY}
    return out


def new_unlocks(known, current):
    """Succès présents dans `current` et absents de `known`, dans un ordre stable."""
    return [current[k] for k in sorted(current) if k not in known]
