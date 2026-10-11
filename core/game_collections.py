#!/usr/bin/env python3
"""game_collections.py — collections manuelles et automatiques (par plateforme).

Le nom évite `collections.py`, qui masquerait le module standard du même nom.
"""
import time

from i18n import tr
from .db import get_setting, set_setting, AUTO_SOURCES, ensure_auto_collections  # noqa: F401 (surface publique)
from .sources import source_cond, source_enabled

# ---------- collections ----------
def _collection_row(con, cid):
    return con.execute("SELECT * FROM collections WHERE id=?", (cid,)).fetchone()

def _reject_auto(con, cid, msg):
    r = _collection_row(con, cid)
    if r and r["kind"] == "auto":
        raise ValueError(tr(msg))

def collection_count(con, c, include_hidden=False, include_not_installed=True):
    """Nombre de jeux d'une collection ; masqués/non-installés ne comptent que s'ils sont affichés."""
    ni = "" if include_not_installed else " AND installed=1"
    if c["kind"] == "auto":
        where = "source=?" + ("" if include_hidden else " AND hidden=0") + ni
        cond = source_cond(con)
        if cond:
            where += f" AND {cond}"
        return con.execute(f"SELECT COUNT(*) FROM games WHERE {where}", (c["auto_source"],)).fetchone()[0]
    where = "cg.collection_id=?" + ("" if include_hidden else " AND g.hidden=0") \
            + ("" if include_not_installed else " AND g.installed=1")
    cond = source_cond(con, "g")  # plateformes désactivées : leurs jeux n'apparaissent pas non plus
    if cond:
        where += f" AND {cond}"
    return con.execute(f"SELECT COUNT(*) FROM collection_games cg JOIN games g ON g.id = cg.game_id "
                       f"WHERE {where}", (c["id"],)).fetchone()[0]

def list_collections(con, include_disabled=False, include_hidden=False, include_not_installed=True):
    """[dict] : manuelles (triées par nom) puis automatiques (ordre fixe), avec leur nombre de jeux."""
    manual, auto = [], []
    for c in con.execute("SELECT * FROM collections"):
        if c["kind"] == "auto":
            if include_disabled or auto_collection_enabled(con, c["auto_source"]):
                auto.append(c)
        else:
            manual.append(c)
    order = {src: i for i, (src, _) in enumerate(AUTO_SOURCES)}
    manual.sort(key=lambda c: c["name"].casefold())
    auto.sort(key=lambda c: order[c["auto_source"]])
    return [{"id": c["id"], "name": c["name"],
             "n": collection_count(con, c, include_hidden, include_not_installed),
             "kind": c["kind"], "auto_source": c["auto_source"]} for c in manual + auto]

def auto_collection_enabled(con, source):
    if not source_enabled(con, source):  # plateforme désactivée → sa collection auto disparaît aussi
        return False
    return get_setting(con, f"auto_off_{source}") != "1"

def set_auto_collection_enabled(con, source, on):
    set_setting(con, f"auto_off_{source}", "0" if on else "1")

def find_collection(con, name):
    key = name.strip().casefold()
    rows = [r for r in con.execute("SELECT * FROM collections") if r["name"].strip().casefold() == key]
    if len(rows) != 1:
        raise SystemExit(tr("{n} collections for '{name}': ", n=len(rows), name=name) + ", ".join(r["name"] for r in rows))
    return rows[0]

def _collection_name_taken(con, name, exclude_id=None):
    key = name.strip().casefold()
    for r in con.execute("SELECT id, name FROM collections"):
        if r["id"] != exclude_id and r["name"].strip().casefold() == key:
            return True
    return False

def add_collection(con, name):
    name = name.strip()
    if not name:
        raise ValueError(tr("empty name"))
    if _collection_name_taken(con, name):
        raise ValueError(tr("a collection with this name already exists"))
    cur = con.execute("INSERT INTO collections (name, created) VALUES (?, ?)", (name, int(time.time())))
    con.commit()
    return cur.lastrowid

def rename_collection(con, cid, name):
    _reject_auto(con, cid, "automatic collections can't be renamed")
    name = name.strip()
    if not name:
        raise ValueError(tr("empty name"))
    if _collection_name_taken(con, name, exclude_id=cid):
        raise ValueError(tr("a collection with this name already exists"))
    con.execute("UPDATE collections SET name=? WHERE id=?", (name, cid))
    con.commit()

def delete_collection(con, cid):
    _reject_auto(con, cid, "automatic collections can't be deleted")
    con.execute("DELETE FROM collections WHERE id=?", (cid,))
    con.commit()

def collections_of_game(con, gid):
    """ids des collections (manuelles + auto) contenant ce jeu."""
    manual = {r["collection_id"] for r in con.execute("SELECT collection_id FROM collection_games WHERE game_id=?", (gid,))}
    src = con.execute("SELECT source FROM games WHERE id=?", (gid,)).fetchone()
    auto = {r["id"] for r in con.execute("SELECT id FROM collections WHERE kind='auto' AND auto_source=?",
                                         (src["source"],))} if src else set()
    return manual | auto

def collection_game_ids(con, cid):
    """ids des jeux d'une collection (dynamique pour les collections automatiques)."""
    c = _collection_row(con, cid)
    if c and c["kind"] == "auto":
        sql = "SELECT id FROM games WHERE source=?"
        cond = source_cond(con)
        if cond:
            sql += " AND " + cond
        return {r["id"] for r in con.execute(sql, (c["auto_source"],))}
    return {r["game_id"] for r in con.execute("SELECT game_id FROM collection_games WHERE collection_id=?", (cid,))}

def set_game_collections(con, gid, cids):
    for cid in cids:
        _reject_auto(con, cid, "automatic collections can't be edited")
    con.execute("DELETE FROM collection_games WHERE game_id=?", (gid,))
    con.executemany("INSERT OR IGNORE INTO collection_games (collection_id, game_id) VALUES (?, ?)",
                    [(cid, gid) for cid in cids])
    con.commit()

def add_game_to_collection(con, cid, gid):
    _reject_auto(con, cid, "automatic collections can't be edited")
    con.execute("INSERT OR IGNORE INTO collection_games (collection_id, game_id) VALUES (?, ?)", (cid, gid))
    con.commit()

def remove_game_from_collection(con, cid, gid):
    _reject_auto(con, cid, "automatic collections can't be edited")
    con.execute("DELETE FROM collection_games WHERE collection_id=? AND game_id=?", (cid, gid))
    con.commit()
