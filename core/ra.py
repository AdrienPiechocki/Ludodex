"""ra.py — synchronisation d'un compte RetroAchievements (API Web).

L'API Web de RetroAchievements (https://api-docs.retroachievements.org) renvoie du
JSON ; l'authentification se fait par le paramètre `y` (clé Web) et `u` (nom
d'utilisateur). Ce module récupère les jeux joués du compte, les relie aux jeux
Ludodex par titre normalisé, puis stocke les succès d'un jeu en base.
"""
import json
import re
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

from i18n import tr
from .db import get_setting, set_setting, _ts

RA_API = "https://retroachievements.org/API"
RA_UA = "Ludodex (RetroAchievements sync)"


class RaError(ValueError):
    """Erreur de synchronisation RetroAchievements."""


class RaAuthError(RaError):
    """Identifiants manquants ou refusés."""


# ---------- identifiants ----------
def ra_user(con):
    return get_setting(con, "ra_user")


def ra_key(con):
    return get_setting(con, "ra_key")


def set_ra_credentials(con, user, key):
    """Enregistre le nom d'utilisateur et la clé Web de l'API RetroAchievements."""
    set_setting(con, "ra_user", (user or "").strip())
    set_setting(con, "ra_key", (key or "").strip())


def ra_enabled(con):
    """L'intégration RetroAchievements est-elle active ? (réglages → Succès)

    Sémantique de la valeur en base, écrite par `set_ra_enabled` :
      - `'0'` → OFF : aucun succès RA affiché, aucune synchro, aucun appel réseau ;
      - `'1'` ou ABSENT → ON. L'absence compte comme activé : l'option est neuve
        et doit s'activer d'elle-même, sans migration de base."""
    return get_setting(con, "ra_enabled") != "0"


def set_ra_enabled(con, on):
    """Active ou désactive RetroAchievements (case des réglages des succès)."""
    set_setting(con, "ra_enabled", "1" if on else "0")


# ---------- accès HTTP ----------
def _http_json(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": RA_UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8", "replace"))
    if isinstance(data, dict):
        err = data.get("Error") or data.get("error") or data.get("message")
        if err:
            raise RaError(str(err))
        if data.get("Success") is False and err:
            raise RaError(str(err))
    return data


def ra_get(con, endpoint, params, fetch=None):
    """Appelle un endpoint de l'API Web (clé + utilisateur) et renvoie le JSON.

    `fetch(url)` est injectable (tests). Lève RaAuthError si les identifiants
    manquent ou sont refusés (401/403), RaError sinon."""
    user, key = ra_user(con), ra_key(con)
    if not (user and key):
        raise RaAuthError(tr("RetroAchievements: username and API key required"))
    q = {k: v for k, v in params.items() if v is not None}
    q["y"] = key
    url = f"{RA_API}/{endpoint}?" + urllib.parse.urlencode(q)
    fetch = fetch or _http_json
    try:
        return fetch(url)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise RaAuthError(tr("RetroAchievements: key rejected ({code})", code=e.code)) from e
        raise RaError(tr("RetroAchievements: network error ({code})", code=e.code)) from e
    except urllib.error.URLError as e:
        raise RaError(tr("RetroAchievements: unreachable ({e})", e=e.reason)) from e


# ---------- lecture des réponses ----------
def _val(d, *keys):
    """Première valeur non nulle parmi `keys` (l'API expose du PascalCase et du camelCase)."""
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


def parse_completion(data):
    """API_GetUserCompletionProgress / API_GetUserRecentlyPlayedGames → liste normalisée.

    Le premier renvoie un dict {Results}; le second un tableau brut avec
    NumAchieved / AchievementsTotal à la place de NumAwarded / MaxPossible."""
    results = (data.get("Results") or data.get("results")) if isinstance(data, dict) else data
    out = []
    for r in results or []:
        if not isinstance(r, dict):
            continue
        gid = _val(r, "GameID", "gameId")
        if not gid:
            continue
        out.append({
            "game_id": int(gid),
            "title": str(_val(r, "Title", "title") or ""),
            "console": str(_val(r, "ConsoleName", "consoleName") or ""),
            "num_awarded": int(_val(r, "NumAwarded", "numAwarded",
                                    "NumAchieved", "numAchieved") or 0),
            "max_possible": int(_val(r, "MaxPossible", "maxPossible",
                                     "AchievementsTotal", "achievementsTotal",
                                     "NumPossibleAchievements") or 0),
        })
    return out


def parse_game(data):
    """API_GetGameInfoAndUserProgress → {title, achievements: [...]}."""
    if not isinstance(data, dict):
        return {"title": "", "achievements": []}
    ach = data.get("Achievements") or data.get("achievements") or {}
    items = ach.values() if isinstance(ach, dict) else ach if isinstance(ach, list) else []
    out = []
    for a in items:
        if not isinstance(a, dict):
            continue
        aid = _val(a, "ID", "id")
        if aid is None:
            continue
        out.append({
            "ach_id": str(aid),
            "title": str(_val(a, "Title", "title") or ""),
            "description": str(_val(a, "Description", "description") or ""),
            "points": int(_val(a, "Points", "points") or 0),
            "badge": str(_val(a, "BadgeName", "badgeName") or ""),
            "earned_at": _ts(_val(a, "DateEarned", "dateEarned") or ""),
        })
    out.sort(key=lambda a: (a["earned_at"] == 0, a["title"].lower()))
    return {"title": str(_val(data, "Title", "title") or ""), "achievements": out}


def _norm(s):
    """Titre normalisé pour la correspondance : sans accents, tags de région ni ponctuation.

    Retire aussi les marqueurs RetroAchievements « ~Hack~ », « ~Homebrew~ »…, qui
    préfixent certains titres sans exister dans les playlists RetroArch."""
    s = str(s)
    s = re.sub(r"~[^~]*~", " ", s)  # marqueurs ~...~
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[\[(][^\])]*[\])]", " ", s)
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def match_game(ra_games, name):
    """Jeu RA correspondant à un nom Ludodex (exact normalisé, sinon préfixe)."""
    target = _norm(name)
    if not target:
        return None
    for g in ra_games:
        if _norm(g["title"]) == target:
            return g
    for g in ra_games:
        n = _norm(g["title"])
        if n and (n.startswith(target) or target.startswith(n)):
            return g
    return None


# ---------- liaison jeu Ludodex ↔ jeu RetroAchievements ----------
def link(con, gid, ra_game_id, title=""):
    set_setting(con, f"ra_game_{gid}", str(ra_game_id))
    set_setting(con, f"ra_title_{gid}", title or "")


def unlink(con, gid):
    """Dissocie le jeu du compte RA ET purge le cache téléchargé.

    Purger est indispensable : sans ça, la table `ra_achievements` garde des
    lignes orphelines qui réapparaissent au moindre rétablissement du lien."""
    set_setting(con, f"ra_game_{gid}", "")
    set_setting(con, f"ra_title_{gid}", "")
    con.execute("DELETE FROM ra_achievements WHERE game_id=?", (gid,))
    con.commit()


def linked(con, gid):
    """Id du jeu RetroAchievements relié à ce jeu Ludodex (None sinon)."""
    v = get_setting(con, f"ra_game_{gid}")
    return int(v) if v.isdigit() else None


def ra_linked_games(con):
    """[(gid, ra_game_id)] des jeux reliés à un jeu RetroAchievements."""
    out = []
    for r in con.execute("SELECT key, value FROM settings WHERE key LIKE 'ra_game_%'"):
        gid = r["key"][len("ra_game_"):]
        if gid.isdigit() and r["value"].isdigit():
            out.append((int(gid), int(r["value"])))
    return out


# ---------- stockage des succès ----------
def save_ra_achievements(con, gid, achievements):
    con.execute("DELETE FROM ra_achievements WHERE game_id=?", (gid,))
    for a in achievements:
        con.execute(
            "INSERT OR REPLACE INTO ra_achievements "
            "(game_id, ach_id, title, description, points, badge, earned_at) VALUES (?,?,?,?,?,?,?)",
            (gid, a["ach_id"], a["title"], a["description"], a["points"], a["badge"], a["earned_at"]))
    con.commit()


def ra_achievements(con, gid):
    """Succès RetroAchievements en cache : débloqués d'abord, puis par titre."""
    return [dict(r) for r in con.execute(
        "SELECT ach_id, title, description, points, badge, earned_at FROM ra_achievements "
        "WHERE game_id=? ORDER BY (earned_at=0), title COLLATE NOCASE", (gid,))]


# ---------- synchronisation ----------
def account_games(con, fetch=None):
    """Jeux RetroAchievements du compte, réunis depuis les deux endpoints utiles.

    `API_GetUserCompletionProgress` ne liste que les jeux ayant déjà une
    progression (succès ou trophée) : un compte qui n'a encore rien débloqué
    renvoie une liste vide. `API_GetUserRecentlyPlayedGames` liste eux les jeux
    effectivement joués. Les deux sont donc fusionnés (dé-doublonnés par id)."""
    merged = {}
    for endpoint, params in (
        ("API_GetUserCompletionProgress.php", {"u": ra_user(con), "c": 500}),
        ("API_GetUserRecentlyPlayedGames.php", {"u": ra_user(con), "c": 50}),
    ):
        try:
            data = ra_get(con, endpoint, params, fetch=fetch)
        except RaAuthError:
            raise  # clé refusée : inutile de continuer, message déjà explicite
        except RaError:
            continue  # un endpoint en erreur ne doit pas bloquer l'autre
        for g in parse_completion(data):
            merged.setdefault(g["game_id"], g)
    return list(merged.values())


def sync_account(con, fetch=None):
    """Récupère les jeux joués du compte, les relie, et télécharge leurs succès.

    Un jeu relié sans succès en cache serait inutile : on appelle donc aussi
    `sync_game` pour chaque lien (réseau réel, injectable en tests). Renvoie le
    nombre de jeux Ludodex reliés."""
    games = account_games(con, fetch=fetch)
    by_title = {}
    for g in games:
        by_title.setdefault(_norm(g["title"]), g)
    linked_n = 0
    for r in con.execute("SELECT id, name FROM games"):
        g = by_title.get(_norm(r["name"]))
        if g:
            link(con, r["id"], g["game_id"], g["title"])
            linked_n += 1
    # Les liens seuls n'affichent rien : on récupère les succès de chaque jeu lié.
    for gid, _ra_id in ra_linked_games(con):
        if con.execute("SELECT 1 FROM ra_achievements WHERE game_id=?", (gid,)).fetchone():
            continue  # déjà en cache : on évite un appel API par jeu à chaque synchro
        try:
            sync_game(con, gid, fetch=fetch)
        except RaError:
            continue  # un jeu inaccessible ne doit pas faire échouer la synchro
    return linked_n


def sync_game(con, gid, fetch=None):
    """Récupère et stocke les succès RetroAchievements d'un jeu relié. Renvoie leur nombre."""
    ra_gid = linked(con, gid)
    if ra_gid is None:
        raise RaError(tr("this game is not linked to RetroAchievements"))
    data = ra_get(con, "API_GetGameInfoAndUserProgress.php",
                  {"g": ra_gid, "u": ra_user(con)}, fetch=fetch)
    parsed = parse_game(data)
    save_ra_achievements(con, gid, parsed["achievements"])
    return len(parsed["achievements"])
