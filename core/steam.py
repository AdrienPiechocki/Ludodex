#!/usr/bin/env python3
"""steam.py — import Steam : bibliothèques, appinfo.vdf, statistiques locales."""
import datetime, pathlib, re, struct

from .db import upsert, upsert_owned
from .platform import steam_roots, clean_path

STEAM_ROOTS = steam_roots()
IGNORE = ("Proton", "Steam Linux Runtime", "Steamworks Common", "Steam Controller")


# ---------- import Steam ----------
def steam_libraries():
    libs = set()
    for root in STEAM_ROOTS:
        if not root.exists():
            continue
        libs.add(root.resolve())
        vdf = root / "libraryfolders.vdf"
        if vdf.exists():
            for p in re.findall(r'"path"\s+"([^"]+)"', vdf.read_text(errors="ignore")):
                libs.add((clean_path(p) / "steamapps").resolve())
    return libs


def import_steam(con):
    libs = steam_libraries()
    if not libs:
        return 0
    con.execute("UPDATE games SET installed=0 WHERE source='steam'")
    n = 0
    for lib in libs:
        for acf in lib.glob("appmanifest_*.acf"):
            t = acf.read_text(errors="ignore")
            g = lambda k: (re.search(rf'"{k}"\s+"([^"]*)"', t) or [None, ""])[1]
            name, appid = g("name"), g("appid")
            if not appid or any(s in name for s in IGNORE):
                continue
            upsert(con, "steam", appid, name, None, int(g("LastPlayed") or 0))
            n += 1
    return n + import_steam_owned(con)


class _BvReader:
    """Lecteur de VDF binaire (appinfo.vdf)."""

    def __init__(self, buf, pos=0):
        self.b, self.i = buf, pos

    def u8(self):
        v = self.b[self.i]; self.i += 1; return v

    def u32(self):
        v = struct.unpack_from("<I", self.b, self.i)[0]; self.i += 4; return v

    def i32(self):
        v = struct.unpack_from("<i", self.b, self.i)[0]; self.i += 4; return v

    def f32(self):
        v = struct.unpack_from("<f", self.b, self.i)[0]; self.i += 4; return v

    def u64(self):
        v = struct.unpack_from("<Q", self.b, self.i)[0]; self.i += 8; return v

    def i64(self):
        v = struct.unpack_from("<q", self.b, self.i)[0]; self.i += 8; return v

    def cstr(self):
        j = self.b.index(0, self.i); s = self.b[self.i:j]; self.i = j + 1
        return s.decode("utf-8", "replace")

    def wstr(self):
        j = self.i
        while self.b[j:j + 2] != b"\x00\x00":
            j += 2
        s = self.b[self.i:j].decode("utf-16-le", "replace"); self.i = j + 2
        return s


def _bv_value(r, t, strings):
    if t == 0x00:
        return _bv_dict(r, strings)
    if t == 0x01:
        return r.cstr()
    if t == 0x02:
        return r.i32()
    if t == 0x03:
        return r.f32()
    if t == 0x04:
        return r.u32()
    if t == 0x05:
        return r.wstr()
    if t == 0x06:
        return r.u32()
    if t == 0x07:
        return r.u64()
    if t == 0x0a:
        return r.i64()
    raise ValueError(f"appinfo: type inconnu {t:#04x}")


def _bv_dict(r, strings):
    d = {}
    while True:
        t = r.u8()
        if t == 0x08:  # fin du dictionnaire
            return d
        key = strings[r.u32()]
        d[key] = _bv_value(r, t, strings)


def read_appinfo(path):
    """{appid: {'name', 'type'}} depuis appcache/appinfo.vdf (VDF binaire v41) ; {} si illisible."""
    try:
        b = pathlib.Path(path).read_bytes()
        if struct.unpack_from("<I", b, 0)[0] != 0x07564429:
            return {}
        st_off = struct.unpack_from("<Q", b, 8)[0]
        sr = _BvReader(b, st_off)
        strings = [sr.cstr() for _ in range(sr.u32())]
        apps, i = {}, 16
        while i < st_off:
            appid = struct.unpack_from("<I", b, i)[0]
            if appid == 0:
                break
            size = struct.unpack_from("<I", b, i + 4)[0]
            try:
                common = _bv_dict(_BvReader(b, i + 8 + 60), strings).get("appinfo", {}).get("common", {})
                if common.get("name"):
                    apps[appid] = {"name": common["name"], "type": common.get("type") or ""}
            except (ValueError, struct.error, IndexError):
                pass
            i += 8 + size
        return apps
    except (OSError, struct.error, ValueError, IndexError):
        return {}


def read_appinfo_all():
    """Fusionne l'appinfo.vdf de toutes les racines Steam connues."""
    info = {}
    for root in STEAM_ROOTS:
        p = root.parent / "appcache" / "appinfo.vdf"
        if p.exists():
            info.update(read_appinfo(p))
    return info


def steam_owned_appids():
    """Ids des jeux réellement présents dans la bibliothèque Steam (tous comptes).

    Steam télécharge les images de bibliothèque (`appcache/librarycache/<appid>/`) pour
    chaque jeu de la bibliothèque, alors que `librarycache/*.json` n'est créé que de
    façon paresseuse et contient en plus des entrées « liées » non possédées (le jeu de
    base d'une démo possédée, une fiche consultée…). On se fie donc aux images, seul
    signal fiable pour ne rater aucun jeu (surtout les démos) ni inclure de fantômes.
    """
    ids = set()
    for root in STEAM_ROOTS:
        cache = root.parent / "appcache" / "librarycache"
        if not cache.is_dir():
            continue
        for p in cache.iterdir():
            if p.is_dir() and p.name.isdigit():
                ids.add(int(p.name))
    return ids


def import_steam_owned(con):
    """Ajoute à la bibliothèque les jeux Steam possédés mais non installés (type « game » ou « demo »)."""
    info, ids = read_appinfo_all(), steam_owned_appids()
    if not info or not ids:
        return 0
    n = 0
    for appid in ids:
        meta = info.get(appid)
        if not meta or meta["type"].lower() not in ("game", "demo"):
            continue
        row = con.execute("SELECT installed FROM games WHERE source='steam' AND ext_id=?", (str(appid),)).fetchone()
        if row and row["installed"]:  # déjà installé : la passe « installés » s'en occupe
            continue
        upsert_owned(con, "steam", str(appid), meta["name"])
        n += 1
    return n


def parse_vdf(text):
    stack, key = [{}], None
    for s, b in re.findall(r'"((?:[^"\\]|\\.)*)"|([{}])', text):
        if b == "{":
            d = {}
            stack[-1][key] = d
            stack.append(d)
            key = None
        elif b == "}":
            if len(stack) > 1:
                stack.pop()
        elif key is None:
            key = s
        else:
            stack[-1][key] = s
            key = None
    return stack[0]


def find_apps(d):
    for k, v in d.items():
        if isinstance(v, dict):
            if k.lower() == "apps":
                return v
            if (r := find_apps(v)):
                return r
    return None


def _num(x):
    return int(x) if isinstance(x, str) and x.isdigit() else 0


def steam_stats():
    """{appid: (minutes jouées, dernier lancement)} lu dans localconfig.vdf (local, sans API)."""
    out = {}
    for root in STEAM_ROOTS:
        for cfg in (root.parent / "userdata").glob("*/config/localconfig.vdf"):
            apps = find_apps(parse_vdf(cfg.read_text(errors="ignore"))) or {}
            for appid, v in apps.items():
                if isinstance(v, dict):
                    v = {k.lower(): x for k, x in v.items()}
                    old = out.get(appid, (0, 0))
                    out[appid] = (max(_num(v.get("playtime")), old[0]), max(_num(v.get("lastplayed")), old[1]))
    return out


LOG_LINE = re.compile(r"^\[(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})\].*?AppID (\d+)")


def steam_log_launches():
    """{appid: {jour: 1er lancement}} d'après les logs du client Steam (historique limité par leur rotation)."""
    out, nfiles, nlines = {}, 0, 0
    for root in STEAM_ROOTS:
        for f in (root.parent / "logs").glob("*.txt"):
            try:
                if f.stat().st_size > 50_000_000:
                    continue
                lines = f.read_text(errors="ignore").splitlines()
            except OSError:
                continue
            nfiles += 1
            for line in lines:
                if "adding PID" not in line and "Game process added" not in line:
                    continue
                m = LOG_LINE.match(line)
                if not m:
                    continue
                day, hms, appid = m.groups()
                ts = int(datetime.datetime.fromisoformat(f"{day} {hms}").timestamp())
                days = out.setdefault(appid, {})
                days[day] = min(ts, days.get(day, ts))
                nlines += 1
    return out, nfiles, nlines


def backfill_steam_logs(con):
    launches, nfiles, nlines = steam_log_launches()
    ids = {g["ext_id"]: g["id"] for g in con.execute("SELECT id, ext_id FROM games WHERE source='steam'")}
    added = 0
    for appid, days in launches.items():
        if appid in ids:
            for ts in days.values():
                added += con.execute("INSERT OR IGNORE INTO sessions (game_id, started, ended, minutes) VALUES (?, ?, ?, 0)",
                                     (ids[appid], ts, ts)).rowcount
    con.commit()
    return added, nfiles, nlines
