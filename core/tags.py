#!/usr/bin/env python3
"""tags.py — tags du magasin : Steam, mots clés itch.io, genres Lutris."""
import datetime, json, re, shutil, subprocess, time, urllib.error, urllib.request
from html import unescape

from i18n import tr
from .db import LUTRIS_DBS, ITCH_DBS, _ro
from .sources import source_cond

# ---------- tags du magasin (Steam, mots clés itch.io) et genres Lutris ----------
FETCH_STORE_TAGS = True  # False = 100 % hors-ligne (pas de tags du magasin)
STORE_URL = "https://store.steampowered.com/app/{}/?l=english"
STORE_COOKIE = "birthtime=283993201; lastagecheckage=1-0-1980; mature_content=1; wants_mature_content=1"  # passe l'écran d'âge
APPDETAILS_URL = "https://store.steampowered.com/api/appdetails?appids={}&l=english&filters=genres"


def parse_store_tags(html):
    """Extrait les tags (par popularité) de la page du magasin ; None si le format n'est pas reconnu."""
    m = re.search(r"InitAppTagModal\(\s*\d+\s*,\s*(\[.*?\])\s*,", html, re.S)
    if not m:
        return None
    try:
        return [t["name"] for t in json.loads(m.group(1)) if t.get("name")]
    except (ValueError, TypeError, KeyError):
        return None


def fetch_steam_genres(appid, timeout=10):
    """Secours (page bloquée par un écran d'âge, par ex.) : genres de l'API publique du magasin."""
    req = urllib.request.Request(APPDETAILS_URL.format(appid), headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        try:
            d = json.loads(r.read().decode("utf-8", "ignore"))
        except ValueError:
            return None
    data = (d.get(str(appid)) or {}).get("data") or {}
    return [g["description"] for g in data.get("genres", []) if g.get("description")] or None


def fetch_store_tags(appid, timeout=10):
    req = urllib.request.Request(STORE_URL.format(appid),
                                 headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)", "Cookie": STORE_COOKIE})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        tags = parse_store_tags(r.read().decode("utf-8", "ignore"))
    return tags or fetch_steam_genres(appid, timeout)


BROWSER_UA = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
ITCH_HEADERS = {"User-Agent": BROWSER_UA, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5", "Upgrade-Insecure-Requests": "1"}
LUTRIS_API = "https://lutris.net/games/{}"


def http_get(url, headers, timeout=10):
    """GET -> texte. Si urllib est bloqué (403 anti-bot), réessaie avec curl s'il est installé."""
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as r:
            return r.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        if e.code != 403 or not shutil.which("curl"):
            raise
        err = e
    cmd = ["curl", "-sL", "--compressed", "-m", str(timeout), "-w", "\n%{http_code}"]
    for k, v in headers.items():
        cmd += ["-H", f"{k}: {v}"]
    try:
        out = subprocess.run(cmd + [url], capture_output=True, text=True, timeout=timeout + 5).stdout
    except (OSError, subprocess.SubprocessError):
        raise err
    body, _, code = out.rpartition("\n")
    if code.strip() != "200":
        raise urllib.error.HTTPError(url, int(code) if code.strip().isdigit() else 403, "blocked", None, None)
    return body


def parse_itch_tags(page):
    """Mots clés (ligne « Tags » du panneau d'infos) d'une page de jeu itch.io ; None si la page n'est pas reconnue."""
    m = re.search(r"<td[^>]*>\s*Tags\s*</td>\s*<td[^>]*>(.*?)</td>", page, re.S | re.I)
    if m:
        return [unescape(re.sub(r"<[^>]+>", "", t)).strip() for t in re.findall(r"<a\b[^>]*>(.*?)</a>", m.group(1), re.S)]
    return [] if "game_info_panel_widget" in page else None  # page valide, jeu sans mots clés


def fetch_itch_tags(url, timeout=10):
    if not url.startswith(("https://", "http://")):
        return None
    return parse_itch_tags(http_get(url, ITCH_HEADERS, timeout))


def fetch_lutris_genres(url, timeout=10):
    """Genres d'un jeu d'après l'API de lutris.net (par slug) ; None si absent."""
    try:
        d = json.loads(http_get(url, {"User-Agent": BROWSER_UA, "Accept": "application/json"}, timeout))
    except ValueError:
        return None
    gs = d.get("genres") if isinstance(d, dict) else None
    return [g.get("name") if isinstance(g, dict) else g for g in gs or []] or None


def fetch_game_tags(r):
    if r["source"] == "itch":
        return fetch_itch_tags(r["store_url"])
    if r["source"] == "lutris":
        return fetch_lutris_genres(r["store_url"])
    return fetch_store_tags(r["ext_id"])


def clean_tags(tags, limit=15):
    """Liste -> 'a,b,c' (sans doublons insensibles à la casse ; les virgules internes sont retirées)."""
    seen, out = set(), []
    for t in tags or []:
        t = " ".join(str(t).replace(",", " ").split())
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return ",".join(out[:limit])


def fetch_missing_tags(con, limit=100, delay=1.5, progress=None):
    """Récupère une seule fois les tags de chaque jeu Steam / itch.io, installé ou possédé, puis les garde en local.
    (Les genres Lutris, eux, sont lus dans la base de Lutris à l'import.)
    S'arrête au premier souci réseau (hors-ligne, limitation) : on reprendra au prochain lancement."""
    if not FETCH_STORE_TAGS:
        return 0
    retry_before = int(time.time()) - 30 * 86400
    where = ("(source='steam' OR (source IN ('itch', 'lutris') AND store_url != ''))"
             " AND (tags_fetched=0 OR (store_tags='' AND tags_fetched < ?))")
    cond = source_cond(con)
    if cond:
        where += f" AND {cond}"
    rows = con.execute(
        f"""SELECT id, source, ext_id, store_url, name FROM games
            WHERE {where}
            LIMIT ?""", (retry_before, limit)).fetchall()
    done, blocked = 0, set()
    for r in rows:
        if r["source"] in blocked:
            continue
        try:
            tags = fetch_game_tags(r)
        except urllib.error.HTTPError as e:
            if e.code not in (404, 410):  # page disparue : on passe ; sinon (limitation, blocage...) on laisse cette source
                if progress:
                    progress(tr("! tags {source}: {e}", source=r["source"], e=e))
                blocked.add(r["source"])
                continue
            tags = None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            if progress:
                progress(tr("! tags {source}: {e}", source=r["source"], e=e))
            blocked.add(r["source"])
            continue
        stored = clean_tags(tags)
        con.execute("UPDATE games SET store_tags=?, tags_fetched=? WHERE id=?", (stored, int(time.time()), r["id"]))
        con.commit()
        done += 1
        if progress:
            progress(tr("  {name}: {tags}", name=r["name"], tags=", ".join(stored.split(",")[:5]) if stored else tr("(no tags)")))
        time.sleep(delay)
    return done


def diagnose(con, frag=""):
    """Pourquoi un jeu non-Steam n'a-t-il pas de tags du magasin ? Affiche l'état de chaque maillon."""
    rows = con.execute("SELECT * FROM games WHERE source != 'steam' AND name LIKE ? ORDER BY source, name",
                       (f"%{frag}%",)).fetchall()
    print(tr("{n} non-Steam game(s)", n=len(rows)) + (tr(" containing '{frag}'", frag=frag) if frag else ""))
    for g in rows:
        fetched = datetime.datetime.fromtimestamp(g["tags_fetched"]).strftime("%Y-%m-%d") if g["tags_fetched"] else tr("never")
        print(tr("- [{source}] {name} | installed={installed} | fetched={fetched} | url={url} | tags={tags}",
                 source=g["source"], name=g["name"], installed=g["installed"], fetched=fetched,
                 url=g["store_url"] or tr("(empty)"), tags=g["store_tags"] or tr("(empty)")))
        if g["source"] == "lutris" and g["store_url"]:
            try:
                t = fetch_lutris_genres(g["store_url"])
                print("    lutris.net:", tr("no genre found") if not t else t)
            except Exception as e:
                print(tr("    lutris.net: error {e}", e=e))
        if g["source"] == "itch" and g["store_url"]:
            try:
                t = fetch_itch_tags(g["store_url"])
                print("    itch page:", tr("format not recognised (parser needs adjusting)") if t is None
                      else tr("{n} keyword(s): {kw}", n=len(t), kw=t[:6]))
            except Exception as e:
                print(tr("    itch page: error {e}", e=e))
    for path in [p for p in ITCH_DBS if p.exists()]:
        src = _ro(path)
        for tbl in ("games", "caves"):
            print(f"itch {path.name}.{tbl}:", sorted(r["name"] for r in src.execute(f"PRAGMA table_info({tbl})")))
        if frag:
            for r in src.execute("SELECT * FROM games WHERE title LIKE ?", (f"%{frag}%",)):
                print(tr("    butler row:"), {k: (str(r[k])[:60] if r[k] is not None else None) for k in r.keys()
                                             if k in ("id", "title", "url", "classification", "type")})
    for kind, path in [(k, p) for k, p in LUTRIS_DBS if p.exists()]:
        src = _ro(path)
        tables = {r["name"] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        info = {t: src.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("genres", "games_genres") if t in tables}
        print(tr("lutris {kind}: tables {tables}; local genres -> {info}",
                   kind=kind, tables=sorted(tables), info=info or tr("absent")))


def mood_options(con, include_owned=False):
    """Tags proposés au tirage : tags perso d'abord, puis ceux des magasins présents.

    Par défaut, seuls les jeux installés comptent ; `include_owned=True` ajoute les
    tags des jeux possédés non installés (cohérent avec l'option de tirage)."""
    user, store, shown = set(), set(), {}
    sql = "SELECT tags, store_tags FROM games WHERE hidden=0"
    if not include_owned:
        sql += " AND installed=1"
    cond = source_cond(con)
    if cond:
        sql += " AND " + cond
    for r in con.execute(sql):
        user.update(t.strip() for t in r["tags"].split(",") if t.strip())
        for t in r["store_tags"].split(","):
            t = t.strip()
            if t:  # insensible à la casse : « Action » (Lutris) et « action » (itch) comptent ensemble
                store.add(t.lower())
                shown.setdefault(t.lower(), t)
    lower = {u.lower() for u in user}
    extra = [shown[k] for k in sorted(store) if k not in lower]
    return sorted(user) + extra
