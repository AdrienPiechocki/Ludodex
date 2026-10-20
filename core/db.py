#!/usr/bin/env python3
"""db.py — base SQLite locale : schéma, constantes de découverte et accès de base."""
import datetime, pathlib, re, sqlite3, time

from i18n import available, set_lang, tr
from .platform import data_dir, migrate_data_dir, lutris_dbs, itch_dbs, flatpak_dirs, retro_dirs

DB_PATH = data_dir() / "games.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,            -- steam | custom
    ext_id TEXT NOT NULL,            -- appid Steam ou commande
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'backlog',
    tags TEXT NOT NULL DEFAULT '',   -- ex: calme,solo,court
    session_length INTEGER NOT NULL DEFAULT 30,  -- minutes nécessaires pour une session utile
    last_played INTEGER NOT NULL DEFAULT 0,   -- epoch
    UNIQUE (source, ext_id)
);
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY,
    game_id INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    started INTEGER NOT NULL,
    ended INTEGER NOT NULL DEFAULT 0,       -- 0 = session encore ouverte
    minutes INTEGER NOT NULL DEFAULT 0,     -- durée affichée en minutes (écart des horaires, 0 = session d'import)
    UNIQUE (game_id, started)
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS collections (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    created INTEGER NOT NULL,
    kind TEXT NOT NULL DEFAULT 'manual',      -- manual | auto
    auto_source TEXT NOT NULL DEFAULT ''      -- source regroupée si kind='auto' (steam, itch...)
);
CREATE TABLE IF NOT EXISTS collection_games (
    collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    game_id INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    PRIMARY KEY (collection_id, game_id)
);
CREATE TABLE IF NOT EXISTS tracker_logs (
    game_id INTEGER NOT NULL,
    log_path TEXT NOT NULL,
    offset INTEGER NOT NULL DEFAULT 0,
    size INTEGER NOT NULL DEFAULT 0,
    prefix TEXT NOT NULL DEFAULT '',
    yaml TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (game_id, log_path)
);
CREATE TABLE IF NOT EXISTS achievements (
    game_id INTEGER NOT NULL,
    ach_id TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    unlocked_at INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (game_id, ach_id)
);
CREATE TABLE IF NOT EXISTS ra_achievements (
    game_id INTEGER NOT NULL,
    ach_id TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    points INTEGER NOT NULL DEFAULT 0,
    badge TEXT NOT NULL DEFAULT '',
    earned_at INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (game_id, ach_id)
);
CREATE TABLE IF NOT EXISTS steam_achievements (
    game_id INTEGER NOT NULL,
    ach_id TEXT NOT NULL,            -- apiname Steam (ex. FIRST_BLOOD)
    title TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    icon TEXT NOT NULL DEFAULT '',
    hidden INTEGER NOT NULL DEFAULT 0,   -- drapeau spoiler de Steam (1 = caché)
    earned_at INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (game_id, ach_id)
);
"""

# Base des autres sources (Lutris / itch.io / Flatpak / RetroArch).
LUTRIS_DBS = lutris_dbs()
ITCH_DBS = itch_dbs()
FLATPAK_DIRS = flatpak_dirs()
RETRO_DIRS = retro_dirs()
FLATPAK_IGNORE = {"com.valvesoftware.Steam", "net.lutris.Lutris", "org.libretro.RetroArch", "io.itch.itch",
                  "com.heroicgameslauncher.hgl", "com.usebottles.bottles", "net.davidotek.pupgui2"}

# AUTO_SOURCES / ensure_auto_collections vivent ici (db) pour éviter un cycle db -> collections.
AUTO_SOURCES = [("steam", "Steam"), ("itch", "Itch"), ("flatpak", "Flatpak"),
                ("lutris", "Lutris"), ("retroarch", "RetroArch"),
                ("heroic", "Heroic"), ("custom", "Imported")]


def db():
    migrate_data_dir()  # reprend une fois les données de l'ancien nom (gamepicker)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    cols = {r["name"] for r in con.execute("PRAGMA table_info(games)")}
    if "playtime_min" not in cols:
        con.execute("ALTER TABLE games ADD COLUMN playtime_min INTEGER NOT NULL DEFAULT 0")
    if "installed" not in cols:
        con.execute("ALTER TABLE games ADD COLUMN installed INTEGER NOT NULL DEFAULT 1")
    for col, ddl in (("hidden", "INTEGER NOT NULL DEFAULT 0"), ("store_tags", "TEXT NOT NULL DEFAULT ''"),
                     ("tags_fetched", "INTEGER NOT NULL DEFAULT 0"),
                     ("store_url", "TEXT NOT NULL DEFAULT ''"), ("cover_path", "TEXT NOT NULL DEFAULT ''"),
                     ("cover_fetched", "INTEGER NOT NULL DEFAULT 0"), ("sgdb_id", "INTEGER NOT NULL DEFAULT 0"),
                     ("name_locked", "INTEGER NOT NULL DEFAULT 0"), ("launch_override", "TEXT NOT NULL DEFAULT ''"),
                     ("custom_cover", "TEXT NOT NULL DEFAULT ''"), ("notes", "TEXT NOT NULL DEFAULT ''"),
                     ("cover_src", "TEXT NOT NULL DEFAULT ''")):
        if col not in cols:
            con.execute(f"ALTER TABLE games ADD COLUMN {col} {ddl}")
    ccols = {r["name"] for r in con.execute("PRAGMA table_info(collections)")}
    if "kind" not in ccols:
        con.execute("ALTER TABLE collections ADD COLUMN kind TEXT NOT NULL DEFAULT 'manual'")
    if "auto_source" not in ccols:
        con.execute("ALTER TABLE collections ADD COLUMN auto_source TEXT NOT NULL DEFAULT ''")
    scols = {r["name"] for r in con.execute("PRAGMA table_info(sessions)")}
    if "ended" not in scols or "minutes" not in scols:
        if "ended" not in scols:
            con.execute("ALTER TABLE sessions ADD COLUMN ended INTEGER NOT NULL DEFAULT 0")
        if "minutes" not in scols:
            con.execute("ALTER TABLE sessions ADD COLUMN minutes INTEGER NOT NULL DEFAULT 0")
        con.execute("UPDATE sessions SET ended=started, minutes=0 WHERE ended=0")  # sessions héritées : clôturées
    hcols = {r["name"] for r in con.execute("PRAGMA table_info(steam_achievements)")}
    if "hidden" not in hcols:  # table créée sans le drapeau spoiler de Steam
        con.execute("ALTER TABLE steam_achievements ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0")
    tcols = {r["name"] for r in con.execute("PRAGMA table_info(tracker_logs)")}
    if "yaml" not in tcols:  # la position de lecture vaut pour le YAML qui l'a produite
        con.execute("ALTER TABLE tracker_logs ADD COLUMN yaml TEXT NOT NULL DEFAULT ''")
    ensure_auto_collections(con)
    con.execute("PRAGMA foreign_keys=ON")
    load_lang(con)  # langue choisie dans les réglages (sinon celle du système)
    return con


def ensure_auto_collections(con):
    """Crée les collections automatiques manquantes (une par plateforme) ; jamais dupliquées."""
    have = {r["auto_source"] for r in con.execute("SELECT auto_source FROM collections WHERE kind='auto'")}
    for src, label in AUTO_SOURCES:
        if src not in have:
            con.execute("INSERT INTO collections (name, created, kind, auto_source) VALUES (?, ?, 'auto', ?)",
                        (label, int(time.time()), src))
    con.commit()


def get_setting(con, key, default=""):
    r = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r[0] if r else default


def set_setting(con, key, value):
    con.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value))
    con.commit()


def get_note(con, gid):
    """Note libre attachée à un jeu ('' si aucune)."""
    r = con.execute("SELECT notes FROM games WHERE id=?", (gid,)).fetchone()
    return r["notes"] if r else ""


def set_note(con, gid, text):
    """Enregistre la note d'un jeu (chaîne vide = note effacée)."""
    con.execute("UPDATE games SET notes=? WHERE id=?", (text or "", gid))
    con.commit()


def shows_not_installed(con):
    """Vrai si les jeux possédés non installés sont affichés (donc proposables au tirage)."""
    return get_setting(con, "show_not_installed") == "1"


def pick_owned(con):
    """Vrai si le tirage prend en compte les jeux possédés non installés."""
    return get_setting(con, "pick_owned") == "1"


def module_enabled(con, name):
    """Vrai si un module d'interface (pick|retro) est affiché ; activé par défaut."""
    return get_setting(con, f"module_{name}") != "0"


def has_achievements(con, g):
    """Vrai si CE jeu a au moins un succès enregistré dans la base.

    Trois tables peuvent en porter : `achievements` (suivi local par YAML),
    `ra_achievements` (lien RetroAchievements) et `steam_achievements` (cache
    Steam téléchargé). Les réglages qui coupent une source ne sont pas
    consultés : la question est « la base en contient-elle ? », pas « la page
    Succès les montre-t-elle ? »."""
    for table in ("achievements", "ra_achievements", "steam_achievements"):
        if con.execute(f"SELECT 1 FROM {table} WHERE game_id=? LIMIT 1",
                       (g["id"],)).fetchone():
            return True
    return False


def load_lang(con):
    """Applique la langue enregistrée ; sans réglage, on garde celle du système (détectée par i18n)."""
    code = get_setting(con, "lang")
    if code in dict(available()):
        set_lang(code)


def save_lang(con, code):
    set_setting(con, "lang", code)
    set_lang(code)


MARQUEUR_MARGE_SEC = 300  # « joué vers telle heure » : une session mesurée qui couvre
                          # lp ± 5 min rend le marqueur inutile


def marqueur_couvert(con, game_id, lp):
    """Vrai si une session déjà connue couvre l'instant `lp`.

    Le marqueur — session (lp, lp) de zéro minute, que l'ajoutent les imports qui
    ne savent que QUAND on a joué (LastPlayed Steam, cave itch touché) — n'a sa
    place que là où Ludodex n'a rien mesuré. Une session réelle qui couvre le
    moment suffit à la frise : le jumeau ne ferait que la brouiller (jeu web
    itch : la session mesurée, puis le marqueur du cave touché 42 s plus tard)."""
    return con.execute(
        "SELECT 1 FROM sessions WHERE game_id=? AND started <= ? AND (ended=0 OR ended >= ?) LIMIT 1",
        (game_id, lp + MARQUEUR_MARGE_SEC, lp - MARQUEUR_MARGE_SEC)).fetchone() is not None


def upsert(con, source, ext_id, name, playtime_min=None, last_played=0):
    """Ajoute/met à jour un jeu ; playtime_min=None conserve la valeur existante."""
    con.execute(
        """INSERT INTO games (source, ext_id, name, last_played, playtime_min, installed)
           VALUES (?, ?, ?, ?, ?, 1)
           ON CONFLICT(source, ext_id) DO UPDATE SET
             name = CASE WHEN name_locked = 1 THEN name ELSE excluded.name END,
             installed = 1,
             last_played = MAX(last_played, excluded.last_played),
             playtime_min = CASE WHEN ? IS NULL THEN playtime_min ELSE excluded.playtime_min END""",
        (source, ext_id, name, last_played, playtime_min or 0, playtime_min))
    if last_played:
        gid = con.execute("SELECT id FROM games WHERE source=? AND ext_id=?", (source, ext_id)).fetchone()[0]
        if not marqueur_couvert(con, gid, last_played):
            con.execute("INSERT OR IGNORE INTO sessions (game_id, started, ended, minutes) VALUES (?, ?, ?, 0)",
                        (gid, last_played, last_played))


def upsert_owned(con, source, ext_id, name):
    """Ajoute un jeu possédé (non installé) ; ne dégrade jamais un jeu déjà installé."""
    con.execute(
        """INSERT INTO games (source, ext_id, name, installed) VALUES (?, ?, ?, 0)
           ON CONFLICT(source, ext_id) DO UPDATE SET
             name = CASE WHEN name_locked = 1 THEN name ELSE excluded.name END""",
        (source, ext_id, name))


def _ts(s):
    try:
        s = re.sub(r"(\.\d{6})\d+", r"\1", str(s)).replace("Z", "+00:00")
        return int(datetime.datetime.fromisoformat(s).timestamp())
    except ValueError:
        return 0


def _ro(path):
    """Ouvre une base EXISTANTE en lecture seule — `mode=ro` n'en crée jamais une.

    Le chemin passe par une URI (`Path.as_uri()`), la forme que SQLite interprète
    de façon identique partout ; les espaces, `#` et `%` y sont échappés `%HH`,
    qu'une simple interpolation ne saurait pas faire — un `#` ouvrirait une
    « frange » que SQLite ignore."""
    con = sqlite3.connect(f"{pathlib.Path(path).absolute().as_uri()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def find(con, frag):
    rows = con.execute("SELECT * FROM games WHERE name LIKE ?", (f"%{frag}%",)).fetchall()
    if len(rows) != 1:
        raise SystemExit(tr("{n} results for '{frag}': ", n=len(rows), frag=frag) + ", ".join(r["name"] for r in rows))
    return rows[0]
