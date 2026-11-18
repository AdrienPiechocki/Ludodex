#!/usr/bin/env python3
"""hltb.py — durées de jeu HowLongToBeat (histoire / +extras / 100 %), gardées en local.

HowLongToBeat n'a pas d'API officielle : on parle à son site comme le fait son navigateur
(même protocole que la bibliothèque `howlongtobeatpy`, sans en dépendre) :

  1. la page d'accueil donne les scripts Next.js, dont l'un contient l'URL du POST de recherche ;
  2. `<url>/init?t=<ms>` renvoie un jeton + une paire clé/valeur anti-robot ;
  3. le POST de recherche les renvoie en en-têtes (x-auth-token, x-hp-key, x-hp-val) et dans le corps.

Ce protocole change de temps en temps : tout écart de format lève `HltbError`, jamais une
exception obscure, et la récupération s'arrête proprement (« format changé, mettez Ludodex à
jour ») sans toucher aux données déjà enregistrées. Rien n'est jamais récupéré tout seul : seuls
`ludodex hltb`, le bouton des réglages et la fiche d'un jeu appellent le réseau. Aucune clé.

Les durées sont stockées en minutes dans `games` (hltb_main, hltb_extra, hltb_complete) ;
0 = inconnu. `hltb_id` > 0 = jeu reconnu ; `hltb_fetched` = date de la dernière tentative.
"""
import difflib
import json
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

from i18n import tr
from .covers import NETWORK_ERRORS
from .sources import source_cond
from .steam import steam_appid_of

BASE = "https://howlongtobeat.com"
UA = "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0"
FALLBACK_ENDPOINTS = ("/api/find", "/api/s", "/api/search")
RETRY_AFTER = 30 * 86400          # un jeu introuvable est réessayé après 30 jours
_POST_FETCH = re.compile(
    r'fetch\s*\(\s*["\']/api/([a-zA-Z0-9_/]+)[^"\']*["\']\s*,\s*{[^}]*method:\s*["\']POST["\'][^}]*}',
    re.DOTALL | re.IGNORECASE)
_cache = {"endpoint": None}


class HltbError(Exception):
    """HowLongToBeat a refusé la requête ou changé de format."""


class HltbRateLimited(HltbError):
    """HTTP 429 : trop de requêtes — inutile d'insister cette fois."""


# ---------- réseau ----------

def _http(method, url, headers=None, body=None, timeout=25):
    """(statut, texte). Les erreurs HTTP reviennent en statut ; le réseau en panne lève."""
    req = urllib.request.Request(url, data=body, method=method,
                                 headers={"User-Agent": UA, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        return e.code, ""


def _check(status, what):
    if status == 429:
        raise HltbRateLimited(tr("HowLongToBeat: too many requests, try again later"))
    if status != 200:
        raise HltbError(tr("HowLongToBeat answered {code} ({what})", code=status, what=what))


def discover_endpoint(http=None):
    """Chemin du POST de recherche, lu dans les scripts du site (mémorisé pour la session)."""
    if _cache["endpoint"]:
        return _cache["endpoint"]
    http = http or _http
    status, html = http("GET", BASE + "/")
    if status == 200:
        scripts = re.findall(r'<script[^>]+src="([^"]*/_next/static/chunks/[^"]+)"', html)
        for src in scripts:
            st, js = http("GET", urllib.parse.urljoin(BASE + "/", src))
            m = _POST_FETCH.search(js) if st == 200 else None
            if m:
                _cache["endpoint"] = "/api/" + m.group(1).rstrip("/")
                return _cache["endpoint"]
    elif status == 429:
        raise HltbRateLimited(tr("HowLongToBeat: too many requests, try again later"))
    return None


def _auth(endpoint, http):
    """(jeton, clé, valeur) pour UNE recherche ; HltbError si la réponse n'a pas la forme attendue."""
    status, text = http("GET", f"{BASE}{endpoint}/init?t={int(time.time() * 1000)}",
                        {"Referer": BASE + "/"})
    _check(status, "init")
    try:
        d = json.loads(text)
        token = d["token"]
    except (ValueError, KeyError, TypeError):
        raise HltbError(tr("HowLongToBeat: unexpected format (Ludodex may need an update)"))
    key = val = None
    for k, v in d.items():
        low = k.lower()
        if "key" in low:
            key = v
        elif "val" in low:
            val = v
    return str(token), key, val


def _payload(terms, key, val):
    p = {"searchType": "games", "searchTerms": terms.split(), "searchPage": 1, "size": 20,
         "searchOptions": {"games": {"userId": 0, "platform": "", "sortCategory": "popular",
                                     "rangeCategory": "main", "rangeTime": {"min": 0, "max": 0},
                                     "gameplay": {"perspective": "", "flow": "", "genre": "", "difficulty": ""},
                                     "rangeYear": {"max": "", "min": ""}, "modifier": "hide_dlc"},
                           "users": {"sortCategory": "postcount"}, "lists": {"sortCategory": "follows"},
                           "filter": "", "sort": 0, "randomizer": 0},
         "useCache": True}
    if key:
        p[key] = val
    return p


def _minutes(seconds):
    try:
        return max(0, int(seconds) // 60)
    except (TypeError, ValueError):
        return 0


def _entry(d):
    """Résultat HLTB → {id, name, alias, main, extra, complete, steam} (durées en minutes)."""
    return {"id": int(d.get("game_id") or 0), "name": str(d.get("game_name") or ""),
            "alias": str(d.get("game_alias") or ""),
            "main": _minutes(d.get("comp_main")), "extra": _minutes(d.get("comp_plus")),
            "complete": _minutes(d.get("comp_100")), "steam": int(d.get("profile_steam") or 0)}


def search(terms, http=None):
    """Résultats HLTB pour `terms` : liste d'entrées (voir `_entry`)."""
    http = http or _http
    endpoint = discover_endpoint(http)
    candidates = [endpoint] if endpoint else list(FALLBACK_ENDPOINTS)
    last = None
    for ep in candidates:
        try:
            token, key, val = _auth(ep, http)
        except HltbRateLimited:
            raise
        except HltbError as e:
            last = e
            continue
        headers = {"content-type": "application/json", "accept": "*/*", "Referer": BASE + "/",
                   "Origin": BASE, "x-auth-token": token, "x-hp-key": str(key), "x-hp-val": str(val)}
        status, text = http("POST", BASE + ep, headers, json.dumps(_payload(terms, key, val)).encode())
        if status == 200:
            _cache["endpoint"] = ep
            try:
                return [_entry(d) for d in json.loads(text)["data"]]
            except (ValueError, KeyError, TypeError):
                raise HltbError(tr("HowLongToBeat: unexpected format (Ludodex may need an update)"))
        if status == 429:
            raise HltbRateLimited(tr("HowLongToBeat: too many requests, try again later"))
        last = HltbError(tr("HowLongToBeat answered {code} ({what})", code=status, what="search"))
    _cache["endpoint"] = None        # l'adresse a peut-être changé : on la redécouvrira
    raise last or HltbError(tr("HowLongToBeat: search endpoint not found"))


# ---------- appariement ----------

def _norm(s):
    s = unicodedata.normalize("NFKD", str(s).casefold().replace("&", "and"))
    return re.sub(r"[\W_]+", "", "".join(c for c in s if not unicodedata.combining(c)))


def clean_name(name):
    """Nom de recherche : sans ™ ®, sans « Demo » final, sans édition entre parenthèses."""
    n = re.sub(r"[™®©℠]", "", name)
    n = re.sub(r"\s*[\(\[][^)\]]*(edition|remaster|demo|version)[^)\]]*[\)\]]", "", n, flags=re.I)
    n = re.sub(r"[\s:,.!\-–—]*\bdemo\b[\s:,.!\-–—]*$", "", n, flags=re.I)
    return n.strip() or name


def _digits(s):
    return re.findall(r"\d+", s)


def best_match(name, entries, appid=None):
    """Entrée HLTB qui désigne CE jeu, ou None (pas de durée plutôt que celle d'un autre jeu).

    Dans l'ordre : même appid Steam ; même nom une fois normalisé (alias compris) ; nom très
    proche (≥ 0,93) portant les mêmes nombres (« 2 » ≠ « 3 »)."""
    if appid:
        for e in entries:
            if e["steam"] == appid:
                return e
    n = _norm(clean_name(name))
    if not n:
        return None
    for e in entries:
        names = {_norm(e["name"])} | {_norm(a) for a in re.split(r"[,;]", e["alias"]) if a.strip()}
        if n in names:
            return e
    best, score = None, 0.0
    for e in entries:
        r = difflib.SequenceMatcher(None, n, _norm(e["name"])).ratio()
        if r > score and _digits(clean_name(name)) == _digits(e["name"]):
            best, score = e, r
    return best if score >= 0.93 else None


# ---------- base ----------

def _store(con, gid, e, now=None):
    con.execute("UPDATE games SET hltb_id=?, hltb_name=?, hltb_main=?, hltb_extra=?, hltb_complete=?, "
                "hltb_fetched=? WHERE id=?",
                (e["id"], e["name"], e["main"], e["extra"], e["complete"], int(now or time.time()), gid))
    con.commit()


def fetch_hltb(con, g, http=None):
    """Cherche les durées d'un jeu et les enregistre. True si le jeu est reconnu.

    Un jeu déjà relié (`link` ou récupération précédente) garde sa fiche : seules ses durées sont rafraîchies."""
    http = http or _http
    entries = search(clean_name(g["name"]), http)
    appid = steam_appid_of(g) if g["source"] == "steam" else None
    pinned = int(g["hltb_id"] or 0)
    if pinned:      # relié (à la main ou par une récupération précédente) : on ne change jamais de fiche
        e = next((x for x in entries if x["id"] == pinned), None)
    else:
        e = best_match(g["name"], entries, appid)
    if e is None:
        con.execute("UPDATE games SET hltb_fetched=? WHERE id=?", (int(time.time()), g["id"]))
        con.commit()
        return False
    _store(con, g["id"], e)
    return True


def fetch_missing(con, limit=30, delay=1.5, progress=None, retry=False, http=None):
    """Durées des jeux jamais interrogés — TOUS les jeux, installés ou non, masqués ou non — (et des introuvables après 30 jours). Renvoie le nombre trouvé.

    `retry` retente aussi les introuvables récents. Hors ligne, bloqué ou limité : on s'arrête
    sur-le-champ — insister ferait bannir l'adresse, et le reste attendra la prochaine fois."""
    where = "(hltb_fetched=0 OR (hltb_id=0 AND hltb_fetched < ?))"
    cutoff = int(time.time()) + 1 if retry else int(time.time()) - RETRY_AFTER
    cond = source_cond(con)
    if cond:
        where += f" AND {cond}"
    rows = con.execute(f"SELECT * FROM games WHERE {where} "
                       "ORDER BY CASE status WHEN 'en_cours' THEN 0 WHEN 'backlog' THEN 1 ELSE 2 END, "
                       "last_played DESC LIMIT ?", (cutoff, limit)).fetchall()
    done = 0
    for i, g in enumerate(rows):
        try:
            ok = fetch_hltb(con, g, http)
        except (HltbError, *NETWORK_ERRORS) as e:
            if progress:
                progress(tr("! hltb: {e}", e=e))
            break
        done += ok
        if progress:
            progress(tr("  {name}: {state}", name=g["name"], state=tr("ok") if ok else tr("not found")))
        if i + 1 < len(rows):
            time.sleep(delay)
    return done


def title_of(hltb_id, http=None):
    """Titre HLTB d'une fiche (page /game/<id>) ; None si la page n'existe pas."""
    http = http or _http
    status, html = http("GET", f"{BASE}/game/{int(hltb_id)}")
    if status == 404:
        return None
    _check(status, "game")
    m = re.search(r"<title>\s*(?:How long is\s*)?(.*?)\??\s*\|\s*HowLongToBeat", html, re.S | re.I)
    return m.group(1).strip() if m else None


def link(con, gid, hltb_id, http=None):
    """Relie un jeu à une fiche HLTB (quand la recherche par nom échoue) et charge ses durées."""
    http = http or _http
    title = title_of(hltb_id, http)
    if not title:
        return False
    e = next((x for x in search(title, http) if x["id"] == int(hltb_id)), None)
    if e is None:
        return False
    _store(con, gid, e)
    return True


def set_manual(con, gid, main=None, extra=None, complete=None):
    """Durées saisies à la main (en HEURES, décimales permises) ; None = inchangé.

    Sans id HLTB (`hltb_id` = 0) : ce n'est qu'une saisie locale, jamais écrasée par une
    récupération tant que `hltb_fetched` reste non nul."""
    vals = {"hltb_main": main, "hltb_extra": extra, "hltb_complete": complete}
    sets, args = [], []
    for col, hours in vals.items():
        if hours is not None:
            sets.append(f"{col}=?")
            args.append(max(0, round(float(hours) * 60)))
    if not sets:
        return
    con.execute(f"UPDATE games SET {', '.join(sets)}, hltb_fetched=MAX(hltb_fetched, ?) WHERE id=?",
                (*args, int(time.time()), gid))
    con.commit()


# ---------- lecture ----------

def durations(g):
    """{'main', 'extra', 'complete'} en minutes, ou None si aucune durée connue."""
    d = {"main": g["hltb_main"], "extra": g["hltb_extra"], "complete": g["hltb_complete"]}
    return d if any(d.values()) else None


def fmt_hours(minutes):
    """12 h 30 → « 12.5 h » ; moins d'une heure → « 40 min »."""
    if minutes < 60:
        return f"{minutes} min"
    h = minutes / 60
    return f"{h:.0f} h" if abs(h - round(h)) < 0.05 else f"{h:.1f} h"


def summary(g):
    """« Story 12 h · +Extras 18 h · 100 % 40 h » (parties inconnues omises) ; '' si rien."""
    d = durations(g)
    if not d:
        return ""
    parts = [(tr("Story"), d["main"]), (tr("+Extras"), d["extra"]), ("100 %", d["complete"])]
    return " · ".join(f"{label} {fmt_hours(m)}" for label, m in parts if m)


def remaining_minutes(g, played_min):
    """Minutes restantes pour finir l'histoire principale (≥ 0), ou None si la durée est inconnue."""
    main = g["hltb_main"]
    return max(0, main - int(played_min or 0)) if main else None
