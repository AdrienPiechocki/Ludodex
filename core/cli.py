#!/usr/bin/env python3
"""cli.py — interface en ligne de commande (argparse) de Ludodex."""
import argparse, datetime, urllib.error

from i18n import available, get_lang, tr
from .db import db, find, save_lang, get_note, set_note
from .launch import launch_text, set_launch_override, check_command
from .steam import backfill_steam_logs
from .tags import diagnose, fetch_missing_tags
from .covers import sgdb_key, covers_dir, cover_file, fetch_missing_covers, fetch_cover, SgdbAuthError, \
    set_custom_cover, clear_custom_cover
from .stats import auto_tidy, refresh_stats, week_text, fmt_minutes
from .game_collections import find_collection, list_collections, add_collection, rename_collection, delete_collection, \
    collection_game_ids, add_game_to_collection, remove_game_from_collection
from .importers import import_all, add_custom
from .pick import excluded_tags, pick, STATUS_WEIGHT
from .sources import source_cond


# ---------- gestion ----------
def main():
    con = db()  # d'abord : charge la langue avant de construire les textes d'aide
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("import")
    p = sub.add_parser("pick")
    p.add_argument("-t", "--time", type=int, help=tr("minutes available"))
    p.add_argument("-m", "--mood", action="append", help=tr("mood(s): -m calm -m short, or -m calm,short"))
    p.add_argument("--mood-mode", choices=["any", "all"], default="any",
                   help=tr("moods filter: any (at least one, default) or all"))
    p.add_argument("-c", "--collection", action="append",
                   help=tr("restrict the draw to collection(s) (name; repeatable, or comma-separated)"))
    p.add_argument("-x", "--exclude", action="append",
                   help=tr("tag(s) to exclude from the draw (repeatable, or comma-separated)"))
    s = sub.add_parser("set")
    s.add_argument("name")
    s.add_argument("--status", choices=list(STATUS_WEIGHT))
    s.add_argument("--tags")
    s.add_argument("--min", type=int, dest="session_length")
    s.add_argument("--cover", help=tr("image file copied as this game's custom cover"))
    s.add_argument("--clear-cover", action="store_true", help=tr("remove the custom cover"))
    a = sub.add_parser("add")  # jeu hors Steam
    a.add_argument("name"); a.add_argument("command")
    rn = sub.add_parser("rename")  # le nom modifié résiste aux réimportations
    rn.add_argument("name"); rn.add_argument("new", help=tr('new name ("" = original name on next import)'))
    nt = sub.add_parser("note")  # note libre attachée à un jeu
    nt.add_argument("name")
    nt.add_argument("text", nargs="?", default=None, help=tr('no argument: show; "": clear the note'))
    ex = sub.add_parser("exe")  # chemin ou commande de lancement personnalisé
    ex.add_argument("name")
    ex.add_argument("command", nargs="?", default=None, help=tr('no argument: show; "": restore the default launch'))
    sub.add_parser("list")
    lg = sub.add_parser("lang")  # langue de l'interface : sans argument, affiche la langue courante
    lg.add_argument("code", nargs="?", choices=[c for c, _ in available()])
    sub.add_parser("stats")
    sub.add_parser("backfill")
    for nm in ("hide", "unhide"):
        sub.add_parser(nm).add_argument("name")
    col = sub.add_parser("col")  # collections : col <action> <nom> [extra]
    col.add_argument("action", choices=["list", "add", "rename", "del", "show", "add-game", "remove-game"])
    col.add_argument("name", nargs="?")
    col.add_argument("extra", nargs="?")
    tg = sub.add_parser("tags")
    tg.add_argument("-n", "--limit", type=int, default=100)
    tg.add_argument("--retry", action="store_true", help=tr("retry Steam / Itch / Lutris games whose fetch failed"))
    sub.add_parser("diag").add_argument("name", nargs="?", default="")
    cv = sub.add_parser("covers")
    cv.add_argument("-n", "--limit", type=int, default=50)
    cv.add_argument("--retry", action="store_true", help=tr("retry games without a cover"))
    cv.add_argument("--status", action="store_true", help=tr("cover status, no network"))
    c1 = sub.add_parser("cover")  # forcer l'id SteamGridDB d'un jeu (quand la recherche par nom échoue)
    c1.add_argument("name"); c1.add_argument("sgdb_id", type=int)
    args = ap.parse_args()
    if args.cmd != "import":
        auto_tidy(con)
    if args.cmd == "import":
        import_all(con)
    elif args.cmd == "pick":
        moods = [m.strip() for v in (args.mood or []) for m in v.split(",") if m.strip()]
        names = [n.strip() for v in (args.collection or []) for n in v.split(",") if n.strip()]
        cids = [find_collection(con, n)["id"] for n in names] or None
        excl = set(excluded_tags(con))
        excl.update(t.strip() for v in (args.exclude or []) for t in v.split(",") if t.strip())
        pick(con, args.time, moods or None, cids, args.mood_mode, sorted(excl))
    elif args.cmd == "set":
        g = find(con, args.name)
        for col in ("status", "tags", "session_length"):
            if getattr(args, col) is not None:
                con.execute(f"UPDATE games SET {col}=? WHERE id=?", (getattr(args, col), g["id"]))
        con.commit()
        if args.cover:
            try:
                set_custom_cover(con, g["id"], args.cover)
            except OSError as e:
                raise SystemExit(tr("cannot read {path}: {e}", path=args.cover, e=e))
        if args.clear_cover:
            clear_custom_cover(con, g["id"])
        print("OK:", g["name"])
    elif args.cmd == "add":
        try:
            _, missing = add_custom(con, args.name, args.command)
        except ValueError as e:
            raise SystemExit(tr("invalid command: {e}", e=e))
        print("OK:", args.name, tr("(warning, not found: {cmd})", cmd=missing) if missing else "")
    elif args.cmd == "rename":
        g = find(con, args.name)
        new = args.new.strip()
        if new:
            con.execute("UPDATE games SET name=?, name_locked=1 WHERE id=?", (new, g["id"]))
        else:
            con.execute("UPDATE games SET name_locked=0 WHERE id=?", (g["id"],))
        con.commit()
        print("OK:", new or tr("{name} (original name restored on next import)", name=g["name"]))
    elif args.cmd == "note":
        g = find(con, args.name)
        if args.text is None:
            print(get_note(con, g["id"]))
        else:
            set_note(con, g["id"], args.text)
            print("OK:", g["name"] if args.text.strip() else tr("note cleared"))
    elif args.cmd == "exe":
        g = find(con, args.name)
        if args.command is None:
            print(launch_text(g))
        elif not args.command.strip():
            if g["ext_id"].startswith("custom:"):
                raise SystemExit(tr("manually added game: a command is required"))
            set_launch_override(con, g["id"], "")
            print(tr("default launch restored:"), launch_text(con.execute("SELECT * FROM games WHERE id=?", (g["id"],)).fetchone()))
        else:
            try:
                missing = check_command(args.command)
            except ValueError as e:
                raise SystemExit(tr("invalid command: {e}", e=e))
            set_launch_override(con, g["id"], args.command)
            print("OK:", args.command, tr("(warning, not found: {cmd})", cmd=missing) if missing else "")
    elif args.cmd in ("hide", "unhide"):
        g = find(con, args.name)
        con.execute("UPDATE games SET hidden=? WHERE id=?", (int(args.cmd == "hide"), g["id"]))
        con.commit()
        print(tr("hidden: " if args.cmd == "hide" else "unhidden: ") + g["name"])
    elif args.cmd == "covers" and args.status:
        print(tr("API key: {state} | folder: {folder}",
                                  state=tr("present") if sgdb_key() else tr("MISSING"), folder=covers_dir()))
        for g in con.execute("SELECT * FROM games WHERE installed=1 AND hidden=0"
                             + ((" AND " + source_cond(con)) if source_cond(con) else "")
                             + " ORDER BY source, name"):
            f = cover_file(g)
            fetched = datetime.datetime.fromtimestamp(g["cover_fetched"]).strftime("%Y-%m-%d") if g["cover_fetched"] else tr("never")
            state = (tr("{name} ({kb} KB)", name=f.name, kb=f.stat().st_size // 1024) if f
                     else tr("file missing") if g["cover_path"] else tr("none"))
            print(tr("[{source}] {name}: {state} | attempt: {fetched} | SGDB id: {sgdb}",
                  source=g["source"], name=g["name"], state=state, fetched=fetched, sgdb=g["sgdb_id"] or "-"))
    elif args.cmd == "covers":
        if not sgdb_key():
            raise SystemExit(tr("No API key: set STEAMGRIDDB_API_KEY (at the top of picker.py or as an environment variable)."))
        if args.retry:
            con.execute("UPDATE games SET cover_fetched=0 WHERE cover_path=''")
            con.commit()
        print(tr("{n} cover(s) fetched", n=fetch_missing_covers(con, args.limit, progress=print)))
    elif args.cmd == "cover":
        if not sgdb_key():
            raise SystemExit(tr("No API key: set STEAMGRIDDB_API_KEY."))
        g = find(con, args.name)
        con.execute("UPDATE games SET sgdb_id=? WHERE id=?", (args.sgdb_id, g["id"]))
        g = con.execute("SELECT * FROM games WHERE id=?", (g["id"],)).fetchone()
        try:
            print(tr("cover fetched") if fetch_cover(con, g) else tr("no cover found for this id"))
        except (SgdbAuthError, urllib.error.URLError, OSError) as e:
            print(f"! {e}")
    elif args.cmd == "diag":
        diagnose(con, args.name)
    elif args.cmd == "tags":
        if args.retry:
            con.execute("UPDATE games SET tags_fetched=0 WHERE store_tags='' AND source IN ('steam', 'itch', 'lutris')")
            con.commit()
        print(tr("{n} game(s) tagged", n=fetch_missing_tags(con, args.limit, progress=print)))
    elif args.cmd == "backfill":
        added, nfiles, nlines = backfill_steam_logs(con)
        print(tr("{files} log file(s), {lines} launch(es) recognised, {added} day(s) added",
                  files=nfiles, lines=nlines, added=added))
        if not nlines:
            print(tr("Nothing recognised. Check: grep -h 'adding PID' ~/.local/share/Steam/logs/*.txt | head"))
    elif args.cmd == "stats":
        refresh_stats(con)
        print(week_text(con))
        for g in con.execute("SELECT id, name, playtime_min FROM games WHERE playtime_min > 0"
                             + ((" AND " + source_cond(con)) if source_cond(con) else "")
                             + " ORDER BY playtime_min DESC LIMIT 15"):
            print(f"{fmt_minutes(g['playtime_min']):>8}  {g['name']}")
    elif args.cmd == "lang":
        if args.code:
            save_lang(con, args.code)
        print(tr("language: {code}", code=get_lang()))
    elif args.cmd == "col":
        if args.action == "list":
            cols = list_collections(con, include_disabled=True)
            if not cols:
                print(tr("no collections"))
            for c in cols:
                print(f"{c['n']:>3}  {c['name']}" + (tr("  (auto)") if c["kind"] == "auto" else ""))
        elif args.action == "add":
            if not args.name:
                raise SystemExit(tr("collection name required"))
            try:
                add_collection(con, args.name)
            except ValueError as e:
                raise SystemExit(str(e))
            print("OK:", args.name)
        elif args.action == "rename":
            if not (args.name and args.extra):
                raise SystemExit(tr("collection name and new name required"))
            c = find_collection(con, args.name)
            try:
                rename_collection(con, c["id"], args.extra)
            except ValueError as e:
                raise SystemExit(str(e))
            print("OK:", args.extra)
        elif args.action == "del":
            c = find_collection(con, args.name)
            try:
                delete_collection(con, c["id"])
            except ValueError as e:
                raise SystemExit(str(e))
            print(tr("deleted: ") + c["name"])
        elif args.action == "show":
            c = find_collection(con, args.name)
            ids = collection_game_ids(con, c["id"])
            for g in sorted((r for r in con.execute("SELECT * FROM games") if r["id"] in ids),
                            key=lambda r: r["name"].casefold()):
                print(f"{g['status']:<10} {g['name']}")
        elif args.action in ("add-game", "remove-game"):
            if not (args.name and args.extra):
                raise SystemExit(tr("collection name and game name required"))
            c = find_collection(con, args.name)
            g = find(con, args.extra)
            try:
                if args.action == "add-game":
                    add_game_to_collection(con, c["id"], g["id"])
                else:
                    remove_game_from_collection(con, c["id"], g["id"])
            except ValueError as e:
                raise SystemExit(str(e))
            print(tr("added to “{col}”: {game}", col=c["name"], game=g["name"]) if args.action == "add-game"
                  else tr("removed from “{col}”: {game}", col=c["name"], game=g["name"]))
    elif args.cmd == "list":
        q = "SELECT * FROM games" + ((" WHERE " + source_cond(con)) if source_cond(con) else "")
        for g in con.execute(q + " ORDER BY status, name"):
            print(f"{g['status']:<10} {g['name']:<40} {g['tags']}  ({g['session_length']} min)")


if __name__ == "__main__":
    main()
