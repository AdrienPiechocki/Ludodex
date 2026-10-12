#!/usr/bin/env python3
"""covers.py — couvertures de jeux via SteamGridDB."""
import json, os, pathlib, re, time, urllib.error, urllib.parse, urllib.request

from i18n import tr
from .db import DB_PATH
from .sources import source_cond

# ---------- couvertures (SteamGridDB) ----------
STEAMGRIDDB_API_KEY = ""  # facultatif : la clé se règle aussi dans l'interface (⚙) ou via la variable STEAMGRIDDB_API_KEY
FETCH_COVERS = True
SGDB_API = "https://www.steamgriddb.com/api/v2"
SGDB_UA = "ludodex/1.0"
SGDB_DIMENSIONS = "600x900,342x482,660x930"  # formats « jaquette » (portrait)


class SgdbAuthError(Exception):
    """Clé API absente, refusée ou quota dépassé : inutile d'insister."""


def key_file():
    return DB_PATH.parent / "steamgriddb.key"


def sgdb_key():
    """Variable d'environnement, sinon clé enregistrée par l'interface, sinon constante ci-dessus."""
    try:
        saved = key_file().read_text(encoding="utf-8").strip()
    except OSError:
        saved = ""
    return (os.environ.get("STEAMGRIDDB_API_KEY") or saved or STEAMGRIDDB_API_KEY).strip()


def save_sgdb_key(key):
    """Enregistre (ou efface, si vide) la clé dans un fichier lisible par toi seul."""
    f = key_file()
    key = key.strip()
    if not key:
        f.unlink(missing_ok=True)
        return
    f.parent.mkdir(parents=True, exist_ok=True)
    f.touch(mode=0o600, exist_ok=True)
    f.chmod(0o600)
    f.write_text(key, encoding="utf-8")


def covers_dir():
    return DB_PATH.parent / "covers"


def custom_covers_dir():
    """Couvertures choisies à la main par l'utilisateur (prioritaires sur celles de SteamGridDB)."""
    return covers_dir() / "custom"


def _field(g, key, default=""):
    """Valeur d'une colonne, même sur une ligne issue d'un SELECT partiel."""
    try:
        v = g[key]
    except (IndexError, KeyError, TypeError):
        return default
    return default if v is None else v


def cover_file(g):
    """Chemin de la couverture locale d'un jeu (perso prioritaire), ou None."""
    if not g:
        return None
    custom = _field(g, "custom_cover")
    if custom:
        f = custom_covers_dir() / custom
        if f.exists():
            return f
    path = _field(g, "cover_path")
    if not path:
        return None
    f = covers_dir() / path
    return f if f.exists() else None


def set_custom_cover(con, gid, src):
    """Copie l'image choisie par l'utilisateur comme couverture perso (remplace l'ancienne).

    Ne touche pas à la couverture automatique (cover_path) : elle sert de secours après suppression.
    Renvoie le nom du fichier enregistré dans covers/custom/.
    """
    src = pathlib.Path(src)
    ext = src.suffix.lower() or ".png"
    dest_dir = custom_covers_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)
    for old in dest_dir.glob(f"{gid}.*"):  # une seule couverture perso par jeu
        old.unlink(missing_ok=True)
    dest = dest_dir / f"{gid}{ext}"
    dest.write_bytes(src.read_bytes())
    con.execute("UPDATE games SET custom_cover=? WHERE id=?", (dest.name, gid))
    con.commit()
    return dest.name


def clear_custom_cover(con, gid):
    """Retire la couverture perso d'un jeu ; la couverture automatique reste en secours. Renvoie True si retirée."""
    row = con.execute("SELECT custom_cover FROM games WHERE id=?", (gid,)).fetchone()
    name = row["custom_cover"] if row else ""
    if name:
        (custom_covers_dir() / name).unlink(missing_ok=True)
    con.execute("UPDATE games SET custom_cover='' WHERE id=?", (gid,))
    con.commit()
    return bool(name)


def _norm(s):
    return re.sub(r"[\W_]+", "", s.casefold())


def sgdb_get(path, params=None, timeout=15):
    url = SGDB_API + path + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {sgdb_key()}", "User-Agent": SGDB_UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8", "ignore"))
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 429):
            raise SgdbAuthError(tr("SteamGridDB answered {code} (key rejected or too many requests)", code=e.code))
        raise
    return d.get("data") if d.get("success") else None


def sgdb_find_game(name):
    """id SteamGridDB si un résultat porte exactement le même nom (on préfère pas de couverture à la mauvaise)."""
    for d in sgdb_get("/search/autocomplete/" + urllib.parse.quote(name, safe="")) or []:
        if _norm(d.get("name", "")) == _norm(name):
            return d["id"]
    return None


def fetch_cover(con, g):
    """Télécharge la couverture d'un jeu et l'enregistre. Renvoie True si trouvée."""
    sid = g["sgdb_id"] or None
    try:
        if g["source"] == "steam":
            path = f"/grids/steam/{g['ext_id']}"
        else:
            sid = sid or sgdb_find_game(g["name"])
            path = f"/grids/game/{sid}" if sid else None
        grids = sgdb_get(path, {"dimensions": SGDB_DIMENSIONS, "types": "static", "mimes": "image/png,image/jpeg"}) if path else None
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
        grids = None  # jeu inconnu de SteamGridDB
    name = ""
    if grids:
        url = grids[0]["url"]
        ext = pathlib.Path(urllib.parse.urlparse(url).path).suffix.lower() or ".png"
        dest = covers_dir() / f"{g['id']}{ext}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": SGDB_UA}), timeout=30) as r:
            dest.write_bytes(r.read())
        name = dest.name
    con.execute("UPDATE games SET cover_path=?, cover_fetched=?, sgdb_id=? WHERE id=?",
                (name, int(time.time()), sid or 0, g["id"]))
    con.commit()
    return bool(name)


def fetch_missing_covers(con, limit=50, delay=0.5, progress=None, include_not_installed=False):
    """Une couverture par jeu, récupérée une fois puis gardée en local (réessai après 30 jours si introuvable)."""
    if not (FETCH_COVERS and sgdb_key()):
        return 0
    sql_reset = "SELECT id, cover_path FROM games WHERE cover_path != ''"
    cond = source_cond(con)
    if cond:
        sql_reset += " AND " + cond
    for r in con.execute(sql_reset).fetchall():
        if not (covers_dir() / r["cover_path"]).exists():  # fichier supprimé : on le retélécharge
            con.execute("UPDATE games SET cover_path='', cover_fetched=0 WHERE id=?", (r["id"],))
    retry_before = int(time.time()) - 30 * 86400
    inst = "" if include_not_installed else "installed=1 AND "
    where = f"{inst}hidden=0 AND cover_path='' AND custom_cover='' AND (cover_fetched=0 OR cover_fetched < ?)"
    cond = source_cond(con)
    if cond:
        where += f" AND {cond}"
    rows = con.execute(f"SELECT * FROM games WHERE {where} LIMIT ?", (retry_before, limit)).fetchall()
    done = 0
    for g in rows:
        try:
            ok = fetch_cover(con, g)
        except SgdbAuthError as e:
            if progress:
                progress(tr("! covers: {e}", e=e))
            break
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            if progress:
                progress(tr("! covers: {e}", e=e))
            break
        done += ok
        if progress:
            progress(tr("  {name}: {state}", name=g["name"], state=tr("ok") if ok else tr("not found")))
        time.sleep(delay)
    return done
