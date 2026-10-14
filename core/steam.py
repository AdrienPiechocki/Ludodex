#!/usr/bin/env python3
"""steam.py — import Steam : bibliothèques, appinfo.vdf, statistiques locales,
puis succès Steam récupérés sur la Steam Web API (même principe que `ra.py` :
identifiants en réglages, cache local, bascule ON/OFF, synchro à la partie)."""
import datetime, json, pathlib, re, struct
import urllib.error
import urllib.parse
import urllib.request

from i18n import tr
from .db import upsert, upsert_owned, get_setting, set_setting
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
            for p in re.findall(r'"path"\s+"([^"]+)"', vdf.read_text(encoding="utf-8", errors="ignore")):
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
            t = acf.read_text(encoding="utf-8", errors="ignore")
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
            apps = find_apps(parse_vdf(cfg.read_text(encoding="utf-8", errors="ignore"))) or {}
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
                lines = f.read_text(encoding="utf-8", errors="ignore").splitlines()
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


# ---------- succès Steam (Steam Web API) ----------
STEAM_API = "https://api.steampowered.com"
STEAM_UA = "Ludodex (Steam achievements sync)"
# SteamID64 = 76561197960265728 + n° de compte du dossier `userdata/<n>/` (Steam3).
STEAM_ID_BASE = 76561197960265728


class SteamError(ValueError):
    """Erreur de synchronisation des succès Steam."""


class SteamAuthError(SteamError):
    """Clé Web API ou SteamID64 manquant ou refusé."""


# ---------- identifiants ----------
def steam_api_key(con):
    return get_setting(con, "steam_api_key")


def steam_id_setting(con):
    """SteamID64 saisie dans les réglages ('' si on s'en remet à la détection)."""
    return get_setting(con, "steam_id64")


def set_steam_credentials(con, key, steam_id=""):
    """Enregistre la clé Web API et, éventuellement, la SteamID64 du compte."""
    set_setting(con, "steam_api_key", (key or "").strip())
    set_setting(con, "steam_id64", (steam_id or "").strip())


def steam_enabled(con):
    """L'intégration des succès Steam est-elle active ? (réglages → Succès)

    Absence de réglage = activée : comme `ra_enabled`, l'option s'active
    d'elle-même, sans migration de base."""
    return get_setting(con, "steam_enabled") != "0"


def set_steam_enabled(con, on):
    """Active ou désactive les succès Steam (case des réglages des succès)."""
    set_setting(con, "steam_enabled", "1" if on else "0")


def steam_userdata():
    """Dossier `userdata/<compte>/` du compte Steam local, ou None.

    Plusieurs comptes possibles : on retient celui dont le dossier a bougé en
    dernier — c'est celui qui vient de jouer."""
    for root in STEAM_ROOTS:
        base = root.parent / "userdata"
        if not base.is_dir():
            continue
        dossiers = [d for d in base.iterdir() if d.is_dir() and d.name.isdigit()]
        if dossiers:
            return max(dossiers, key=lambda d: d.stat().st_mtime)
    return None


def detect_steam_id64():
    """SteamID64 du compte local, lue dans `userdata/` — aucune saisie requise."""
    d = steam_userdata()
    return str(STEAM_ID_BASE + int(d.name)) if d else ""


def steam_id64(con):
    """Identifiant du compte : saisi dans les réglages, sinon détecté en local."""
    return steam_id_setting(con) or detect_steam_id64()


def steam_appid_of(g):
    """Appid lisible dans une ligne de `games`, ou None : une autre origine n'a pas
    d'équivalent Steam, et un `ext_id` non numérique n'est pas un appid (commande,
    chemin de ROM, JSON…) — l'appid ne sert qu'à bâtir une URL, mieux vaut ne pas
    en fabriquer une au hasard."""
    try:
        source, ext = g["source"], g["ext_id"]
    except (IndexError, KeyError, TypeError):
        return None
    if source != "steam" or not str(ext or "").isdigit():
        return None
    return int(ext)


def steam_appid(con, gid):
    """Appid du jeu, ou None. La règle tient dans `steam_appid_of`, lue aussi par
    les couvertures : une seule définition de « c'est un appid »."""
    r = con.execute("SELECT source, ext_id FROM games WHERE id=?", (gid,)).fetchone()
    return steam_appid_of(r) if r is not None else None


def appmanifest_path(appid):
    """Chemin du `appmanifest_<appid>.acf`, où Steam écrit `LastPlayed` en jouant.

    Un fichier par jeu, réécrit quand une partie commence ou se termine : c'est
    pour un jeu Steam l'équivalent du log d'un suivi YAML, et la même façon de
    repérer une partie sans inspecter de processus. None si le jeu n'est pas
    installé dans une bibliothèque connue."""
    for lib in steam_libraries():
        p = lib / f"appmanifest_{appid}.acf"
        if p.is_file():
            return str(p)
    return None


# ---------- accès HTTP ----------
def _steam_json(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": STEAM_UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8", "replace"))
    if isinstance(data, dict):
        # Steam glisse ses erreurs dans l'enveloppe plutôt que dans un statut HTTP.
        for enveloppe in (data.get("playerstats"), data.get("response"), data):
            if isinstance(enveloppe, dict) and (enveloppe.get("error") or enveloppe.get("Error")):
                raise SteamError(str(enveloppe.get("error") or enveloppe.get("Error")))
    return data


def steam_get(con, endpoint, params, fetch=None):
    """Appelle un endpoint de la Steam Web API (clé requise) et renvoie le JSON.

    `fetch(url)` est injectable (tests). Lève SteamAuthError si la clé manque
    ou est refusée (401/403), SteamError sinon."""
    key = steam_api_key(con)
    if not key:
        raise SteamAuthError(tr("Steam: API key required"))
    q = {k: v for k, v in params.items() if v is not None}
    q["key"] = key
    url = f"{STEAM_API}/{endpoint}?" + urllib.parse.urlencode(q)
    fetch = fetch or _steam_json
    try:
        return fetch(url)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise SteamAuthError(tr("Steam: key rejected ({code})", code=e.code)) from e
        raise SteamError(tr("Steam: network error ({code})", code=e.code)) from e
    except urllib.error.URLError as e:
        raise SteamError(tr("Steam: unreachable ({e})", e=e.reason)) from e


# ---------- lecture des réponses ----------
def _loc(v):
    """Texte de l'API Steam : chaîne directe, ou dict localisé `{"english": …}`."""
    if isinstance(v, dict):
        v = v.get("english") or next(iter(v.values()), "")
    return str(v or "")


def _int(v):
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def parse_steam_schema(data):
    """GetSchemaForGame → [{ach_id, title, description, icon, hidden}] ; [] si le
    jeu n'a aucun succès Steam (c'est le signe à retenir : rien à afficher).

    Steam range les succès sous `game.availableGameStats.achievements` — pas
    directement sous `game`, qui ne porte que `gameName` et `gameVersion`. Le
    schéma donne le libellé, l'icône et le drapeau `hidden` (spoiler), mais PAS
    la description : elle ne vient que de la progression du compte, d'où le
    merge dans `sync_steam_game`. Un jeu sans succès répond `{"game": {}}`."""
    jeu = data.get("game") if isinstance(data, dict) else None
    stats = jeu.get("availableGameStats") if isinstance(jeu, dict) else None
    if not isinstance(stats, dict):
        return []
    out = []
    for a in stats.get("achievements") or []:
        if not isinstance(a, dict) or not a.get("name"):
            continue
        out.append({"ach_id": str(a["name"]),
                    "title": _loc(a.get("displayName")) or str(a["name"]),
                    "description": "",
                    "icon": _loc(a.get("icon")),
                    "hidden": _int(a.get("hidden"))})
    return out


def parse_steam_progress(data):
    """GetPlayerAchievements → {ach_id: timestamp de déblocage} ; 0 = verrouillé."""
    return {k: v["earned_at"] for k, v in parse_steam_compte(data).items()}


def parse_steam_compte(data):
    """GetPlayerAchievements → {ach_id: {earned_at, description}}.

    C'est la SEULE source des descriptions : le schéma n'en contient pas. Un
    joueur qui n'a jamais lancé le jeu reçoit un 400 « no stats » — pas de
    progression, donc rien à débloquer, et surtout rien à faire échouer."""
    ps = data.get("playerstats") if isinstance(data, dict) else None
    if not isinstance(ps, dict) or ps.get("success") is False:
        return {}
    out = {}
    for a in ps.get("achievements") or []:
        if not isinstance(a, dict) or not a.get("apiname"):
            continue
        out[str(a["apiname"])] = {
            "earned_at": _int(a.get("unlocktime")) if _int(a.get("achieved")) else 0,
            "description": str(a.get("description") or "")}
    return out


# ---------- stockage des succès ----------
def save_steam_achievements(con, gid, achievements):
    con.execute("DELETE FROM steam_achievements WHERE game_id=?", (gid,))
    for a in achievements:
        con.execute(
            "INSERT OR REPLACE INTO steam_achievements "
            "(game_id, ach_id, title, description, icon, hidden, earned_at) VALUES (?,?,?,?,?,?,?)",
            (gid, a["ach_id"], a["title"], a["description"], a.get("icon", ""),
             int(a.get("hidden") or 0), a["earned_at"]))
    con.commit()


def steam_achievements(con, gid):
    """Succès Steam en cache : débloqués d'abord, puis par titre."""
    return [dict(r) for r in con.execute(
        "SELECT ach_id, title, description, icon, hidden, earned_at FROM steam_achievements "
        "WHERE game_id=? ORDER BY (earned_at=0), title COLLATE NOCASE", (gid,))]


def has_steam(con, gid):
    """Le jeu a-t-il des succès Steam téléchargés ?

    Sert à distinguer « à réinitialiser » de « à retirer de la liste » : un jeu
    qui n'a que ça pour source n'a aucune progression locale à effacer."""
    return con.execute("SELECT 1 FROM steam_achievements WHERE game_id=? LIMIT 1",
                       (gid,)).fetchone() is not None


def forget_steam(con, gid):
    """Oublie les succès Steam téléchargés du jeu.

    Purger est indispensable, comme pour RetroAchievements : des lignes
    laissées en place réapparaîtraient au moindre ré-synchro."""
    con.execute("DELETE FROM steam_achievements WHERE game_id=?", (gid,))
    con.commit()


# ---------- synchronisation ----------
def sync_steam_game(con, gid, fetch=None):
    """Récupère et stocke les succès Steam d'un jeu. Renvoie leur nombre.

    Deux appels, dans l'ordre : le schéma (libellés, descriptions, icônes) puis
    la progression du compte (déblocages et dates). Un jeu sans aucun succès
    Steam vide son cache et compte 0 : il ne reste donc pas dans la liste."""
    appid = steam_appid(con, gid)
    if appid is None:
        raise SteamError(tr("this game is not from Steam"))
    sid = steam_id64(con)
    if not sid:
        raise SteamAuthError(tr("Steam: Steam ID required"))
    schema = parse_steam_schema(steam_get(
        con, "ISteamUserStats/GetSchemaForGame/v2/", {"appid": appid}, fetch=fetch))
    if not schema:
        save_steam_achievements(con, gid, [])
        return 0
    compte = parse_steam_compte(steam_get(
        con, "ISteamUserStats/GetPlayerAchievements/v1/",
        {"steamid": sid, "appid": appid, "l": "english"}, fetch=fetch))
    out = []
    for a in schema:
        etat = compte.get(a["ach_id"], {})
        # Le schéma porte le libellé, la progression porte la description et
        # la date : aucun des deux endpoints n'est complet tout seul.
        out.append({**a,
                    "description": a["description"] or etat.get("description", ""),
                    "earned_at": etat.get("earned_at", 0)})
    out.sort(key=lambda a: (a["earned_at"] == 0, a["title"].lower()))
    save_steam_achievements(con, gid, out)
    return len(out)


def sync_steam_games(con, fetch=None, progress=None):
    """Télécharge les succès des jeux Steam installés ou joués non encore en cache.

    Seuls les jeux installés ou déjà joués sont interrogés : inutile de
    dépenser deux appels par jeu de bibliothèque jamais lancé. Déjà en cache →
    rappelé seulement par la 🔄 du jeu, comme pour RetroAchievements. Les
    identifiants sont vérifiés AVANT la boucle : un compte mal renseigné doit
    le dire une fois, pas une fois par jeu. `progress(i, total)` est appelé
    après chaque jeu : le compte réel compte ~120 jeux et ~2 appels chacun,
    soit une bonne minute d'attente qui ne doit pas se deviner. Renvoie le
    nombre de jeux enregistrés."""
    if not steam_api_key(con):
        raise SteamAuthError(tr("Steam: API key required"))
    if not steam_id64(con):
        raise SteamAuthError(tr("Steam: Steam ID required"))
    rows = con.execute("SELECT id FROM games WHERE source='steam' "
                       "AND (installed=1 OR playtime_min>0)").fetchall()
    n = 0
    for i, r in enumerate(rows, 1):
        gid = r["id"]
        try:
            deja = con.execute("SELECT 1 FROM steam_achievements WHERE game_id=?",
                               (gid,)).fetchone()
            if not deja and sync_steam_game(con, gid, fetch=fetch):
                n += 1
        except (SteamError, OSError):
            pass                     # un jeu en échec ne bloque pas les suivants
        if progress:
            progress(i, len(rows))
    return n
