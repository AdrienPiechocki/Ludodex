#!/usr/bin/env python3
"""migrations.py — migrations SQLite versionnées (PRAGMA user_version).

Chaque migration est une fonction `m_<n>(con)` enregistrée dans MIGRATIONS ; la base
porte la version appliquée dans `PRAGMA user_version`. Au démarrage, `migrate()` joue
dans l'ordre celles qui manquent, chacune dans sa propre transaction : un échec
l'annule en entier et la base reste à la version précédente.

Règles :
  - une migration publiée ne change jamais : on en ajoute une nouvelle ;
  - elle doit rester jouable sur une base qui contient déjà une partie du changement
    (`add_column` vérifie avant d'ajouter) : la migration 1 reprend les bases créées
    avant l'introduction du versionnage, dont `user_version` vaut 0 ;
  - avant de modifier une base existante, une copie `games.db.pre-vN-<date>` est gardée
    (les trois dernières sont conservées) ;
  - une base plus récente que ce code est refusée (`DatabaseTooNew`) plutôt que
    « migrée » à l'envers.
"""
import datetime
import pathlib
import sqlite3


class DatabaseTooNew(RuntimeError):
    """La base a été écrite par une version plus récente de Ludodex."""


def statements(script):
    """Découpe un script SQL en instructions complètes (sans `executescript`, qui
    valide la transaction en cours)."""
    buf = ""
    for line in script.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            if buf.strip():
                yield buf.strip()
            buf = ""
    if buf.strip():
        yield buf.strip()


def columns(con, table):
    return {r[1] for r in con.execute(f"PRAGMA table_info({table})")}


def add_column(con, table, col, ddl):
    """Ajoute une colonne si elle manque (idempotent : les bases anciennes l'ont parfois déjà)."""
    if col not in columns(con, table):
        con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")


# ---------- migrations ----------

def m_1_baseline(con):
    """État de référence : tables d'origine + colonnes ajoutées au fil des versions
    avant le versionnage. Reprend à l'identique l'ancien code de `db()`."""
    from .db import SCHEMA
    for stmt in statements(SCHEMA):
        con.execute(stmt)
    add_column(con, "games", "playtime_min", "INTEGER NOT NULL DEFAULT 0")
    add_column(con, "games", "installed", "INTEGER NOT NULL DEFAULT 1")
    for col, ddl in (("hidden", "INTEGER NOT NULL DEFAULT 0"), ("store_tags", "TEXT NOT NULL DEFAULT ''"),
                     ("tags_fetched", "INTEGER NOT NULL DEFAULT 0"),
                     ("store_url", "TEXT NOT NULL DEFAULT ''"), ("cover_path", "TEXT NOT NULL DEFAULT ''"),
                     ("cover_fetched", "INTEGER NOT NULL DEFAULT 0"), ("sgdb_id", "INTEGER NOT NULL DEFAULT 0"),
                     ("name_locked", "INTEGER NOT NULL DEFAULT 0"), ("launch_override", "TEXT NOT NULL DEFAULT ''"),
                     ("custom_cover", "TEXT NOT NULL DEFAULT ''"), ("notes", "TEXT NOT NULL DEFAULT ''"),
                     ("cover_src", "TEXT NOT NULL DEFAULT ''")):
        add_column(con, "games", col, ddl)
    add_column(con, "collections", "kind", "TEXT NOT NULL DEFAULT 'manual'")
    add_column(con, "collections", "auto_source", "TEXT NOT NULL DEFAULT ''")
    scols = columns(con, "sessions")
    add_column(con, "sessions", "ended", "INTEGER NOT NULL DEFAULT 0")
    add_column(con, "sessions", "minutes", "INTEGER NOT NULL DEFAULT 0")
    if "ended" not in scols or "minutes" not in scols:
        con.execute("UPDATE sessions SET ended=started, minutes=0 WHERE ended=0")  # sessions héritées : clôturées
    add_column(con, "steam_achievements", "hidden", "INTEGER NOT NULL DEFAULT 0")
    add_column(con, "tracker_logs", "yaml", "TEXT NOT NULL DEFAULT ''")
    add_column(con, "achievements", "seen_patterns", "TEXT NOT NULL DEFAULT ''")


def m_2_rule_state(con):
    """État JSON des règles de succès évoluées (séquences, valeurs distinctes, sommes…)."""
    add_column(con, "achievements", "state", "TEXT NOT NULL DEFAULT ''")


def m_3_hltb(con):
    """Durées HowLongToBeat (minutes) et date de dernière tentative."""
    for col in ("hltb_id", "hltb_main", "hltb_extra", "hltb_complete", "hltb_fetched"):
        add_column(con, "games", col, "INTEGER NOT NULL DEFAULT 0")
    add_column(con, "games", "hltb_name", "TEXT NOT NULL DEFAULT ''")


def m_4_indexes(con):
    """Index des requêtes de la rétrospective (sessions par date et par jeu)."""
    con.execute("CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions (started)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_sessions_game ON sessions (game_id, started)")


MIGRATIONS = [(1, m_1_baseline), (2, m_2_rule_state), (3, m_3_hltb), (4, m_4_indexes)]
LATEST = MIGRATIONS[-1][0]


# ---------- exécution ----------

def get_version(con):
    return con.execute("PRAGMA user_version").fetchone()[0]


def _has_data(con):
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' LIMIT 1").fetchone() is not None


def _backup(con, path, version, keep=3):
    """Copie la base avant migration ; ne garde que les `keep` plus récentes."""
    path = pathlib.Path(path)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = path.with_name(f"{path.name}.pre-v{version}-{stamp}")
    out = sqlite3.connect(dest)
    try:
        con.backup(out)
    finally:
        out.close()
    old = sorted(path.parent.glob(f"{path.name}.pre-v*"), key=lambda p: p.stat().st_mtime, reverse=True)
    for f in old[keep:]:
        f.unlink(missing_ok=True)
    return dest


def pending(con):
    v = get_version(con)
    return [(n, fn) for n, fn in MIGRATIONS if n > v]


def migrate(con, path=None, backup=True):
    """Applique les migrations manquantes ; renvoie la liste des versions jouées.

    `path` (fichier de la base) active la sauvegarde préalable d'une base existante."""
    current = get_version(con)
    if current > LATEST:
        raise DatabaseTooNew(
            f"database schema v{current} is newer than this Ludodex (v{LATEST}); update Ludodex")
    todo = pending(con)
    if not todo:
        return []
    if backup and path is not None and _has_data(con):
        _backup(con, path, current)
    applied = []
    saved = con.isolation_level
    con.isolation_level = None          # transactions explicites : DDL et version vont ensemble
    try:
        for n, fn in todo:
            con.execute("BEGIN")
            try:
                fn(con)
                con.execute(f"PRAGMA user_version = {int(n)}")
            except BaseException:
                con.execute("ROLLBACK")
                raise
            con.execute("COMMIT")
            applied.append(n)
    finally:
        con.isolation_level = saved
    return applied
