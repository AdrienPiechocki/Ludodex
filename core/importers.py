#!/usr/bin/env python3
"""importers.py — import des jeux depuis les différentes plateformes."""
import datetime, glob, json, os, pathlib, re, sqlite3, stat, urllib.parse, uuid

from i18n import tr
from .db import upsert, upsert_owned, _ts, _ro, LUTRIS_DBS, ITCH_DBS, FLATPAK_DIRS, FLATPAK_IGNORE, RETRO_DIRS
from .tags import clean_tags, LUTRIS_API
from .steam import import_steam
from .launch import default_command, parse_command, split_env, check_command
from . import platform
from .platform import join_command
from .sources import source_enabled
from .stats import refresh_stats, auto_tidy
from .game_collections import AUTO_SOURCES


def lutris_genres(src):
    """{id du jeu Lutris: [genres]} ; vide si la base est trop ancienne (pas de tables genres)."""
    out = {}
    try:
        for r in src.execute("""SELECT gg.game_id AS gid, g.name AS name FROM games_genres gg
                                JOIN genres g ON g.id = gg.genre_id ORDER BY g.name"""):
            out.setdefault(r["gid"], []).append(r["name"])
    except sqlite3.Error:
        pass
    return out


def import_lutris(con):
    dbs = [(k, p) for k, p in LUTRIS_DBS if p.exists()]
    if not dbs:
        return 0
    con.execute("UPDATE games SET installed=0 WHERE source='lutris'")
    n = 0
    for kind, path in dbs:
        src = _ro(path)
        genres = lutris_genres(src)
        for r in src.execute("SELECT * FROM games"):
            r = dict(r)
            if r.get("runner") == "steam":  # Steam est déjà importé (installés et possédés)
                continue
            hours = float(r.get("playtime") or 0)
            ext = f"{kind}:{r['id']}"
            if r.get("installed"):
                upsert(con, "lutris", ext, r["name"], int(hours * 60), int(r.get("lastplayed") or 0))
            else:  # jeu possédé (ajouté à Lutris) mais non installé
                upsert_owned(con, "lutris", ext, r["name"])
            if genres.get(r["id"]):
                con.execute("UPDATE games SET store_tags=? WHERE source='lutris' AND ext_id=?",
                            (clean_tags(genres[r["id"]]), ext))
            elif r.get("slug"):  # pas de genres en local : on les demandera à lutris.net
                con.execute("UPDATE games SET store_url=? WHERE source='lutris' AND ext_id=?",
                            (LUTRIS_API.format(urllib.parse.quote(r["slug"])), ext))
            n += 1
        src.close()
    return n


def import_flatpak(con):
    dirs = [d for d in FLATPAK_DIRS if d.is_dir()]
    if not dirs:
        return 0
    con.execute("UPDATE games SET installed=0 WHERE source='flatpak'")
    n = 0
    for d in dirs:
        for f in d.glob("*.desktop"):
            if f.stem in FLATPAK_IGNORE:
                continue
            t = f.read_text(errors="ignore")
            cats = re.search(r"^Categories=(.*)$", t, re.M)
            if not cats or "Game" not in cats.group(1).split(";") or re.search(r"^NoDisplay=true", t, re.M):
                continue
            name = re.search(r"^Name=(.*)$", t, re.M)
            upsert(con, "flatpak", f.stem, name.group(1) if name else f.stem)
            n += 1
    return n


def import_itch(con):
    """Un 'cave' = un jeu installé dans l'app itch ; lancement via itch://caves/<id>/launch."""
    dbs = [p for p in ITCH_DBS if p.exists()]
    if not dbs:
        return 0
    con.execute("UPDATE games SET installed=0 WHERE source='itch'")
    n = 0
    for path in dbs:
        src = _ro(path)
        cols = {r["name"] for r in src.execute("PRAGMA table_info(caves)")}
        gcols = {r["name"] for r in src.execute("PRAGMA table_info(games)")}
        sel = "c.id AS cid, COALESCE(g.title, 'itch #' || c.game_id) AS title, " + \
              ("c.seconds_run" if "seconds_run" in cols else "0") + " AS secs, " + \
              ("c.last_touched_at" if "last_touched_at" in cols else "NULL") + " AS touched, " + \
              ("g.url" if "url" in gcols else "NULL") + " AS url"
        for r in src.execute(f"SELECT {sel} FROM caves c LEFT JOIN games g ON g.id = c.game_id"):
            # ancien format (chemin de l'exécutable) -> id de cave, en gardant statut/tags
            con.execute("""UPDATE games SET ext_id=? WHERE source='itch' AND name=? AND ext_id LIKE '/%'
                           AND NOT EXISTS (SELECT 1 FROM games WHERE source='itch' AND ext_id=?)""",
                        (r["cid"], r["title"], r["cid"]))
            upsert(con, "itch", r["cid"], r["title"], int(r["secs"] or 0) // 60, _ts(r["touched"]))
            if r["url"]:  # page du jeu : sert à récupérer les mots clés
                con.execute("UPDATE games SET store_url=? WHERE source='itch' AND ext_id=?", (r["url"], str(r["cid"])))
            n += 1
    return n


def runtime_paths(base, content):
    """Chemins des fichiers de runtime `.lrtl` de RetroArch pour ce contenu.

    RetroArch écrit un fichier de runtime par jeu (durée de jeu + dernière
    partie) et le met à jour en jouant : pour un jeu RetroAchievements, c'est
    l'équivalent du log d'un suivi YAML, et la même façon de repérer une partie."""
    stem = pathlib.Path(content.split("#")[-1]).stem
    return list((base / "playlists" / "logs").glob(f"*/{glob.escape(stem)}.lrtl"))


def runtime_path_for(ext_id):
    """Chemin du `.lrtl` d'un jeu importé depuis RetroArch, ou None.

    `ext_id` est le JSON posé par `import_retroarch` : playlist, core, contenu."""
    try:
        ext = json.loads(ext_id or "")
        base = pathlib.Path(ext["f"]).parent.parent   # <base>/playlists/<x>.lpl
        content = ext["content"]
    except (ValueError, KeyError, TypeError):
        return None
    paths = runtime_paths(base, content)
    return str(paths[0]) if paths else None


def retro_runtime(base, content):
    for f in runtime_paths(base, content):
        try:
            d = json.loads(f.read_text())
            minutes = int(d.get("runtime_hours", 0)) * 60 + int(d.get("runtime_minutes", 0))
            y = int(d.get("last_played_year", 0))
            last = int(datetime.datetime(y, int(d["last_played_month"]), int(d["last_played_day"]),
                                         int(d.get("last_played_hour", 0)), int(d.get("last_played_minute", 0)),
                                         int(d.get("last_played_second", 0))).timestamp()) if y else 0
            return minutes, last
        except (ValueError, KeyError, OSError):
            continue
    return None, 0


def import_retroarch(con):
    bases = [(f, b) for f, b in RETRO_DIRS if (b / "playlists").is_dir()]
    if not bases:
        return 0
    con.execute("UPDATE games SET installed=0 WHERE source='retroarch'")
    n = skipped = 0
    for flat, base in bases:
        for lpl in sorted((base / "playlists").glob("*.lpl")):
            if lpl.name.startswith("content_"):  # historique / favoris : doublons
                continue
            try:
                data = json.loads(lpl.read_text(errors="ignore"))
            except ValueError:
                continue
            default_core = data.get("default_core_path") or ""
            for it in data.get("items", []):
                core = it.get("core_path") or ""
                if core in ("", "DETECT"):
                    core = default_core
                content = it.get("path") or ""
                if core in ("", "DETECT") or not content:
                    skipped += 1
                    continue
                if not pathlib.Path(content.split("#")[0]).exists():
                    continue
                ext = json.dumps({"f": flat, "core": core, "content": content}, sort_keys=True)
                pt, lp = retro_runtime(base, content)
                upsert(con, "retroarch", ext, it.get("label") or pathlib.Path(content).stem, pt, lp)
                n += 1
    if skipped:
        print(tr("  RetroArch: {n} entry(ies) without an associated core skipped", n=skipped))
    return n


def _first(d, *keys, default=""):
    """Première valeur non vide parmi `keys` (les lanceurs nomment les champs différemment)."""
    for k in keys:
        v = d.get(k)
        if v is not None and v != "":
            return v
    return default


def parse_legendary(data):
    """Jeux installés d'un `installed.json` de Legendary/gogdl (Epic ou GOG via Heroic).

    Accepte un dict {id: objet} ou une liste d'objets ; ignore ce qui n'est pas un objet.
    """
    items = data.items() if isinstance(data, dict) else ((None, e) for e in data) if isinstance(data, list) else []
    out = []
    for key, e in items:
        if not isinstance(e, dict):
            continue
        ext = _first(e, "app_name", "appName", "app_id", default=key or "")
        if not ext:
            continue
        out.append({"ext_id": str(ext), "name": str(_first(e, "title", "name", "app_title", default=ext))})
    return out


def parse_launcher_installed(data):
    """`LauncherInstalled.dat` du lanceur Epic officiel -> liste d'objets {ext_id, name}."""
    out = []
    for e in (data.get("InstallationList") or []) if isinstance(data, dict) else []:
        if not isinstance(e, dict):
            continue
        ext = _first(e, "AppName", "app_name", "AppId")
        if not ext:
            continue
        out.append({"ext_id": str(ext), "name": str(_first(e, "DisplayName", "AppName", default=ext))})
    return out


def parse_epic_manifest(data):
    """Manifeste `.item` d'un jeu Epic -> {ext_id, name}, ou None si ce n'est pas un jeu."""
    if not isinstance(data, dict):
        return None
    cats = data.get("AppCategories") or []
    if cats and "games" not in cats:  # moteurs, plugins, applications...
        return None
    ext = _first(data, "AppName", "CatalogItemId", "app_name")
    if not ext:
        return None
    return {"ext_id": str(ext), "name": str(_first(data, "DisplayName", "AppName", default=ext))}


def parse_gog_registry(entries):
    """Entrées du registre GOG Galaxy -> [{ext_id, name, command}].

    `command` = `launchCommand` du registre, sinon le chemin `dossier + exe` (les jeux GOG
    sont sans DRM et se lancent directement), sinon ''.
    """
    out = []
    for e in entries or []:
        if not isinstance(e, dict):
            continue
        ext = _first(e, "gameID", "gameId", "gameid", "id", "appName")
        name = _first(e, "gameName", "name", "title", default=ext)
        if not ext and not name:
            continue
        command = _first(e, "launchCommand", "launch_command")
        if not command:
            path, exe = _first(e, "path", "install_path"), _first(e, "exe", "executable")
            if path and exe:
                command = join_command([str(pathlib.Path(str(path)) / str(exe))])
        out.append({"ext_id": str(ext or name), "name": str(name), "command": str(command)})
    return out


def heroic_game_files():
    """Fichiers JSON des jeux installés de Heroic (Legendary pour Epic, gogdl pour GOG)."""
    return [d / "installed.json" for d in platform.legendary_dirs()] + \
           [d / "gog_store" / "installed.json" for d in platform.heroic_dirs()]


def import_heroic(con):
    files = [p for p in heroic_game_files() if p.exists()]
    if not files:
        return 0
    con.execute("UPDATE games SET installed=0 WHERE source='heroic'")
    n = 0
    for path in files:
        try:
            data = json.loads(path.read_text(errors="ignore"))
        except (OSError, ValueError):  # fichier absent/illisible : on ignore
            continue
        for g in parse_legendary(data):
            upsert(con, "heroic", g["ext_id"], g["name"])
            n += 1
    return n


def epic_game_files():
    """(fichier LauncherInstalled.dat, dossier des manifestes .item) par installation Epic."""
    return [(base / "UnrealEngineLauncher" / "LauncherInstalled.dat",
             base / "EpicGamesLauncher" / "Data" / "Manifests")
            for base in platform.epic_programdata()]


def import_epic(con):
    sources = epic_game_files()
    if not any(dat.exists() or man.is_dir() for dat, man in sources):
        return 0
    con.execute("UPDATE games SET installed=0 WHERE source='epic'")
    n = 0
    for dat, man in sources:
        games = {}
        if dat.exists():  # liste des installés (noms = codes internes)
            try:
                data = json.loads(dat.read_text(errors="ignore"))
            except (OSError, ValueError):
                data = {}
            for g in parse_launcher_installed(data):
                games[g["ext_id"]] = g
        if man.is_dir():  # manifestes : plus riches (nom d'affichage)
            for f in sorted(man.glob("*.item")):
                try:
                    data = json.loads(f.read_text(errors="ignore"))
                except (OSError, ValueError):
                    continue
                g = parse_epic_manifest(data)
                if g:
                    games[g["ext_id"]] = g
        for g in games.values():
            upsert(con, "epic", g["ext_id"], g["name"])
            n += 1
    return n


def import_gog(con):
    entries = parse_gog_registry(platform.gog_registry_games())
    if not entries:
        return 0
    con.execute("UPDATE games SET installed=0 WHERE source='gog'")
    n = 0
    for g in entries:
        upsert(con, "gog", g["ext_id"], g["name"])
        if g["command"]:  # ne jamais écraser une commande personnalisée par l'utilisateur
            con.execute("UPDATE games SET launch_override=? WHERE source='gog' AND ext_id=? AND launch_override=''",
                        (g["command"], g["ext_id"]))
        n += 1
    return n


IMPORTERS = {"Steam": import_steam, "Lutris": import_lutris, "Flatpak": import_flatpak,
             "Itch": import_itch, "RetroArch": import_retroarch,
             "Heroic": import_heroic, "Epic Games": import_epic, "GOG Galaxy": import_gog}


def import_all(con):
    res = []
    src_by_label = {label: src for src, label in AUTO_SOURCES}
    for label, fn in IMPORTERS.items():
        src = src_by_label.get(label)
        if src and not source_enabled(con, src):
            print(f"- {label} : {tr('platform disabled')}")
            continue
        try:
            con.execute("SAVEPOINT imp")
            n = fn(con)
            con.execute("RELEASE imp")
        except Exception as e:  # une source cassée ne doit pas bloquer les autres
            con.execute("ROLLBACK TO imp")
            con.execute("RELEASE imp")
            print(f"! {label} : {e}")
            continue
        if n:
            res.append(f"{label} {n}")
    con.commit()
    refresh_stats(con, logs=True)
    print(tr("Import: {res}", res=", ".join(res) or tr("nothing found")))
    return auto_tidy(con)


def add_custom(con, name, command):
    """Jeu ajouté à la main : identifiant propre, commande stockée comme commande personnalisée.
    Renvoie (id, premier mot introuvable ou None). ValueError si nom/commande invalides."""
    name, command = name.strip(), command.strip()
    if not name:
        raise ValueError(tr("empty name"))
    argv, _ = parse_command(command)
    _, prog = split_env(argv)
    path = pathlib.Path(prog[0] if prog else argv[0])
    if path.is_file() and path.name.lower().endswith(".appimage") and not os.access(path, os.X_OK):
        try:
            path.chmod(path.stat().st_mode | stat.S_IXUSR)  # une AppImage doit être exécutable
        except OSError:
            pass
    cur = con.execute("INSERT INTO games (source, ext_id, name, name_locked, launch_override) VALUES ('custom', ?, ?, 1, ?)",
                      (f"custom:{uuid.uuid4().hex}", name, command))
    con.commit()
    return cur.lastrowid, check_command(command)
