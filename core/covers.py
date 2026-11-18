#!/usr/bin/env python3
"""covers.py — couvertures de jeux : SteamGridDB, CDN de Steam, puis IGDB en dernier recours."""
import json, os, pathlib, re, time, unicodedata, urllib.error, urllib.parse, urllib.request

from i18n import tr
from .db import DB_PATH
from .sources import source_cond
from .steam import steam_appid_of

# ---------- couvertures (SteamGridDB) ----------
STEAMGRIDDB_API_KEY = ""  # facultatif : la clé se règle aussi dans l'interface (⚙) ou via la variable STEAMGRIDDB_API_KEY
FETCH_COVERS = True
SGDB_API = "https://www.steamgriddb.com/api/v2"
SGDB_UA = "ludodex/1.0"
SGDB_DIMENSIONS = "600x900,342x482,660x930"  # formats « jaquette » (portrait)

# Jaquette de bibliothèque servie par le CDN de Steam sur simple appid : ni clé ni
# recherche de nom. C'est la SEULE source qui couvre les démos — les 244 jeux Steam
# sans jaquette de la base le sont toutes, toutes essayées et ratées sur SteamGridDB.
STEAM_COVER_URL = "https://cdn.akamai.steamstatic.com/steam/apps/{appid}/library_600x900_2x.jpg"
STEAM_COVER_UA = "Mozilla/5.0 (X11; Linux x86_64)"  # le CDN répond 403 aux agents vierges

# Les trois visages d'une connexion en rade — et exactement ceux qui font `break`
# dans `fetch_missing_covers`. Ce qui arrête la course reste absorbable ici : il ne
# manque le plus souvent qu'un HÔTE (SGDB), pendant que le CDN de Steam répond.
NETWORK_ERRORS = (urllib.error.URLError, TimeoutError, OSError)


def steam_cover_url(appid):
    """URL de la jaquette de bibliothèque d'un appid. 404 si Steam n'a pas d'art."""
    return STEAM_COVER_URL.format(appid=appid)


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
    """Nom ramené à l'essentiel pour comparaison : sans accents, sans ponctuation, en
    minuscules.

    Les accents sautent parce que la base écrit « Pokemon » et SteamGridDB
    « Pokémon » : « é » est un caractère WORD, `[\\W_]` ne l'atteignait pas, et les
    deux normes ne s'égalisaient jamais — pourtant l'API renvoyait le bon jeu au
    PREMIER rang, à chaque fois.

    Seuls les signes COMBINANTS partent, jamais `encode('ascii', 'ignore')` : ce
    repli avalerait les titres chinois ou cyrilliques, qui n'ont pas d'accents, et
    deux d'entre eux s'égaliseraient par leur vide commun — soit une mauvaise
    couverture garantie."""
    s = unicodedata.normalize("NFKD", _MARKS.sub("", s).casefold())
    return re.sub(r"[\W_]+", "", "".join(c for c in s if not unicodedata.combining(c)))


_MARKS = re.compile(r"[™®©℠]")   # marques déposées : « Tomb Raider™ » = « Tomb Raider »


def steamgrid_name(name):
    """Nom sans le « Demo » final : SteamGridDB n'a pas d'art pour les démos, mais il
    a celui du jeu complet. Seul le mot isolé en fin de chaîne saute — « Demo Man »,
    « The Demo Heist » et « Demolition » portent un autre sens et gardent leur nom.
    Les symboles ™ ® © ℠ sautent aussi : ils faussent la recherche et la comparaison.
    Renvoie '' quand il ne reste rien : il n'y a alors rien à chercher."""
    name = re.sub(r"\s{2,}", " ", _MARKS.sub("", name)).strip()
    return re.sub(r"[\s:,.!()\-–—]*\bdemo\b[\s:,.!()\-–—]*$", "", name, flags=re.I)


def _sans_marques(name):
    return re.sub(r"\s{2,}", " ", _MARKS.sub("", name)).strip()


_NUMERO = r"(?:[IVX]{1,4}|\d{1,2})"


def _prefixes_sous_titre(name):
    """Préfixes acceptés pour une jaquette dont le titre complet porte un SOUS-TITRE.

    Les boutiques vendent « Ultima Underworld I » ; SteamGridDB et IGDB connaissent
    « Ultima Underworld: The Stygian Abyss ». Un titre qui finit par un numéro peut donc
    être suivi de « : sous-titre » (« Underworld II » → « Underworld II: Labyrinth of
    Worlds »), et le numéro 1 (« I », « 1 ») peut même disparaître (« Underworld : The
    Stygian Abyss »). Les autres titres restent soumis à l'égalité stricte : sans numéro,
    « Hades » ne doit jamais prendre « Hades: Battle Out of Hell »."""
    nom = steamgrid_name(_sans_marques(name))
    if not re.search(r"\s" + _NUMERO + r"$", nom, flags=re.I):
        return []
    out = [nom]
    base = re.sub(r"\s+(?:I|1)$", "", nom, flags=re.I)
    if base != nom:
        out.append(base)
    return out


def _a_sous_titre(candidat, prefixe):
    """`candidat` est-il « <prefixe> : <sous-titre> » ? (marques et casse ignorées)"""
    c = unicodedata.normalize("NFKC", _MARKS.sub("", candidat)).casefold()
    p = unicodedata.normalize("NFKC", prefixe).casefold()
    return re.match(re.escape(p) + r"\s*[:\-–—]\s*\S", c) is not None


def _choisir(resultats, nom, name, cle=lambda d: d):
    """Élément de `resultats` (liste de dicts avec « name ») qui correspond à `nom` :
    égalité exacte d'abord, puis — pour les titres numérotés seulement — « préfixe : sous-titre »."""
    for d in resultats:
        if _norm(d.get("name", "")) == _norm(nom):
            return d
    for pre in _prefixes_sous_titre(name):
        for d in resultats:
            if _a_sous_titre(d.get("name", ""), pre):
                return d
    return None


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
    """id SteamGridDB si un résultat porte exactement le même nom (on préfère pas de
    couverture à la mauvaise). Une démo retombe sur le jeu complet : le nom exact est
    cherché d'abord, puis sans son « Demo » final — c'est le seul nom qui existe."""
    candidats = [_sans_marques(name), steamgrid_name(name), *_prefixes_sous_titre(name)[1:]]
    for nom in dict.fromkeys(candidats):  # ordre conservé, doublon retiré
        if not nom:
            continue
        d = _choisir(sgdb_get("/search/autocomplete/" + urllib.parse.quote(nom, safe="")) or [], nom, name)
        if d:
            return d["id"]
    return None


def _http_bytes(url, ua, timeout=30):
    """Octets de `url`, ou None si le serveur répond 404 — « cette image n'existe
    pas », ce qui n'est pas une panne. Toute autre erreur remonte : hors ligne, il
    vaut mieux interrompre la boucle que d'enterrer des dizaines de jeux."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": ua})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
        return None


def _ecrire(g, url, ua, fetch=None):
    """Écrit l'image `url` dans le dossier des jaquettes. Nom du fichier, ou '' si
    elle est absente. `fetch(url)` remplace la requête, pour les tests."""
    data = (fetch or (lambda u: _http_bytes(u, ua)))(url)
    if not data:
        return ""
    ext = pathlib.Path(urllib.parse.urlparse(url).path).suffix.lower() or ".png"
    dest = covers_dir() / f"{g['id']}{ext}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return dest.name


def _grille(path):
    """URL de la première grille SGDB du `chemin`, ou None : chemin vide, aucune
    grille, ou appid inconnu (404). Autre erreur : la connexion est en rade."""
    if not path:
        return None
    try:
        grids = sgdb_get(path, {"dimensions": SGDB_DIMENSIONS, "types": "static",
                                "mimes": "image/png,image/jpeg"})
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
        return None
    return grids[0]["url"] if grids else None


def _cover_sgdb(g, sid, fetch=None):
    """Jaquette SteamGridDB, et l'id retenu : (nom du fichier, sgdb_id).

    Un jeu Steam est d'abord cherché par appid ; quand cet appid n'a aucune grille —
    le cas des démos — on retombe sur le nom, sans le « Demo » final : SteamGridDB
    n'a pas d'art pour les démos, mais il a celui du jeu complet. Faute d'id, le nom
    exact sert de recherche, et l'égalité stricte reste de mise : on préfère pas de
    couverture à la mauvaise.
    """
    url = None
    if g["source"] == "steam":
        url = _grille(f"/grids/steam/{g['ext_id']}")
    if url is None:
        sid = sid or sgdb_find_game(g["name"])
        # Personne trouvé : surtout pas de requête vers `/grids/game/None`.
        url = _grille(f"/grids/game/{sid}") if sid else None
    return (_ecrire(g, url, SGDB_UA, fetch) if url else ""), sid


# ---------- IGDB (dernier recours) ----------
# Ordre des sources : SteamGridDB (si clé) → CDN de Steam → IGDB. IGDB n'est interrogé que
# pour un jeu qu'aucune autre source n'a couvert. Il demande un compte Twitch gratuit
# (client_id + client_secret, https://api-docs.igdb.com/#account-creation), réglé dans
# l'interface (⚙ → Connexions), par `ludodex igdb login`, ou par les variables
# IGDB_CLIENT_ID / IGDB_CLIENT_SECRET. Sans identifiants, la source est simplement ignorée.
IGDB_API = "https://api.igdb.com/v4"
IGDB_TOKEN_URL = "https://id.twitch.tv/oauth2/token"
IGDB_IMG = "https://images.igdb.com/igdb/image/upload/t_cover_big_2x/{image_id}.jpg"   # 528×748
IGDB_UA = "ludodex/1.0"
IGDB_STATE = {"error": ""}     # une panne d'authentification coupe IGDB pour le reste de la passe
_igdb_token_cache = {"id": "", "value": "", "exp": 0.0}


class IgdbAuthError(Exception):
    """Identifiants IGDB/Twitch absents, refusés ou quota dépassé : inutile d'insister."""


def igdb_file():
    return DB_PATH.parent / "igdb.key"


def igdb_credentials():
    """(client_id, client_secret) : variables d'environnement, sinon fichier enregistré, sinon ('', '')."""
    cid, secret = os.environ.get("IGDB_CLIENT_ID", ""), os.environ.get("IGDB_CLIENT_SECRET", "")
    if not (cid and secret):
        try:
            cid, _, secret = igdb_file().read_text(encoding="utf-8").strip().partition(":")
        except OSError:
            cid = secret = ""
    return cid.strip(), secret.strip()


def igdb_configured():
    return all(igdb_credentials())


def save_igdb_credentials(client_id, client_secret):
    """Enregistre (ou efface, si l'un est vide) les identifiants dans un fichier lisible par toi seul."""
    f = igdb_file()
    client_id, client_secret = client_id.strip(), client_secret.strip()
    if not (client_id and client_secret):
        f.unlink(missing_ok=True)
        return
    f.parent.mkdir(parents=True, exist_ok=True)
    f.touch(mode=0o600, exist_ok=True)
    f.chmod(0o600)
    f.write_text(f"{client_id}:{client_secret}", encoding="utf-8")


def igdb_token(force=False, timeout=15):
    """Jeton d'accès Twitch (client credentials), gardé en mémoire jusqu'à son expiration."""
    cid, secret = igdb_credentials()
    if not (cid and secret):
        raise IgdbAuthError(tr("IGDB credentials missing"))
    c = _igdb_token_cache
    if not force and c["id"] == cid and c["value"] and time.time() < c["exp"] - 60:
        return c["value"]
    url = IGDB_TOKEN_URL + "?" + urllib.parse.urlencode(
        {"client_id": cid, "client_secret": secret, "grant_type": "client_credentials"})
    try:
        req = urllib.request.Request(url, data=b"", method="POST", headers={"User-Agent": IGDB_UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8", "ignore"))
    except urllib.error.HTTPError as e:
        if e.code in (400, 401, 403, 429):
            raise IgdbAuthError(tr("Twitch answered {code} (IGDB credentials rejected)", code=e.code))
        raise
    c.update(id=cid, value=d.get("access_token", ""), exp=time.time() + float(d.get("expires_in", 0)))
    if not c["value"]:
        raise IgdbAuthError(tr("Twitch returned no token"))
    return c["value"]


def igdb_post(path, body, timeout=15, _retry=True):
    """Requête Apicalypse sur l'API IGDB ; liste JSON. Jeton périmé : un seul renouvellement."""
    cid, _ = igdb_credentials()
    req = urllib.request.Request(
        IGDB_API + path, data=body.encode("utf-8"), method="POST",
        headers={"Client-ID": cid, "Authorization": f"Bearer {igdb_token()}",
                 "Accept": "application/json", "User-Agent": IGDB_UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "ignore"))
    except urllib.error.HTTPError as e:
        if e.code == 401 and _retry:
            igdb_token(force=True)
            return igdb_post(path, body, timeout, _retry=False)
        if e.code in (401, 403, 429):
            raise IgdbAuthError(tr("IGDB answered {code} (credentials rejected or too many requests)", code=e.code))
        raise


def _apicalypse_str(text):
    return text.replace("\\", "\\\\").replace('"', '\\"')


def igdb_find_image(name, post=None):
    """image_id de la couverture IGDB dont le nom est EXACTEMENT celui du jeu, ou None.

    Même politique que SteamGridDB : pas de couverture plutôt que la mauvaise. Une démo
    retombe sur le jeu complet (nom sans son « Demo » final)."""
    post = post or igdb_post
    for nom in dict.fromkeys([_sans_marques(name), steamgrid_name(name), *_prefixes_sous_titre(name)[1:]]):
        if not nom:
            continue
        rows = post("/games", f'search "{_apicalypse_str(nom)}"; fields name,cover.image_id; '
                              f'where cover != null; limit 10;') or []
        d = _choisir([r for r in rows if (r.get("cover") or {}).get("image_id")], nom, name)
        if d:
            return d["cover"]["image_id"]
    return None


def _cover_igdb(g, fetch=None, post=None):
    """Nom du fichier de la jaquette IGDB écrite pour `g`, ou ''."""
    image = igdb_find_image(g["name"], post)
    return _ecrire(g, IGDB_IMG.format(image_id=image), IGDB_UA, fetch) if image else ""


def fetch_cover(con, g, fetch=None):
    """Télécharge la couverture d'un jeu et l'enregistre. Renvoie True si trouvée.

    SteamGridDB est PRIVILÉGIÉ : ses grilles par appid, puis le nom exact, puis le
    nom sans son « Demo » final. Le CDN de Steam n'intervient QUE là où SGDB n'a rien
    — il ne remplace jamais une couverture SGDB — et sans clé il devient la seule
    source, puisqu'il n'en demande aucune.

    `fetch(url)` remplace le téléchargement, pour les tests.
    """
    sid = g["sgdb_id"] or None
    name = ""
    src = ""                                # origine de la couverture : 'sgdb' ou 'steam'
    if sgdb_key():
        try:
            name, sid = _cover_sgdb(g, sid, fetch)
        except NETWORK_ERRORS:
            # Un hôte qui manque n'est pas « hors ligne » : relevé en l'état, un
            # `URLError` sorti de SGDB à 13:16 a fait `break` dans la boucle et a
            # arrêté la récupération de 170 jaquettes, pendant que le CDN de Steam
            # répondait. La panne se limite-t-elle à SGDB, le secours la couvre ;
            # tout est en rade, c'est le CDN qui lèvera — et la boucle cassera, comme
            # il se doit hors ligne.
            name = ""
        else:
            if name:
                src = "sgdb"
    if not name:  # SGDB n'a rien : le CDN comble, ou sert seul faute de clé
        appid = steam_appid_of(g)
        if appid is not None:
            name = _ecrire(g, steam_cover_url(appid), STEAM_COVER_UA, fetch)
            if name:
                src = "steam"
    ancien = _field(g, "cover_path")
    ancien_src = _field(g, "cover_src")
    garde_igdb = bool(ancien and ancien_src == "igdb" and (covers_dir() / ancien).exists())
    if not name and not garde_igdb and igdb_configured() and not IGDB_STATE["error"]:
        # Dernier recours : ni SGDB ni le CDN n'ont de jaquette. Un échec d'IGDB (réseau,
        # identifiants) ne doit jamais interrompre la récupération des autres jeux.
        try:
            name = _cover_igdb(g, fetch)
        except IgdbAuthError as e:
            IGDB_STATE["error"] = str(e)
        except NETWORK_ERRORS:
            name = ""
        if name:
            src = "igdb"
    if not name and ancien and (covers_dir() / ancien).exists():
        # Tentative infructueuse sur une couverture déjà en place (relance après
        # une clé configurée, id SteamGridDB corrigé…) : la vider laisserait une
        # case blanche pour rien. L'ancienne vaut IGDB si elle en vient, sinon CDN
        # — jamais SGDB : l'origine reste ainsi connue.
        name, src = ancien, ("igdb" if ancien_src == "igdb" else "steam")
    if name:  # remplacement : le fichier d'une autre extension ne doit pas survivre
        for f in covers_dir().glob(f"{g['id']}.*"):
            if f.name != name:
                f.unlink(missing_ok=True)
    con.execute("UPDATE games SET cover_path=?, cover_fetched=?, sgdb_id=?, cover_src=? WHERE id=?",
                (name, int(time.time()), sid or 0, src, g["id"]))
    con.commit()
    return bool(name)


def fetch_missing_covers(con, limit=50, delay=0.5, progress=None, fetch=None):
    """Une couverture par jeu, récupérée une fois puis gardée en local (réessai après 30 jours si introuvable).

    TOUS les jeux sont visés — installés ou non, masqués ou non : masquer un jeu de la
    bibliothèque n'a rien à voir avec le fait de lui trouver une jaquette.

    Aucune clé n'est exigée d'entrée : le CDN de Steam en fournit sans. SteamGridDB
    n'intervient que s'il y a une clé, donc après — jamais à la place. IGDB (identifiants
    Twitch facultatifs) ne couvre que ce qui reste.
    """
    if not FETCH_COVERS:
        return 0
    IGDB_STATE["error"] = ""
    sql_reset = "SELECT id, cover_path FROM games WHERE cover_path != ''"
    cond = source_cond(con)
    if cond:
        sql_reset += " AND " + cond
    for r in con.execute(sql_reset).fetchall():
        if not (covers_dir() / r["cover_path"]).exists():  # fichier supprimé : on le retélécharge
            con.execute("UPDATE games SET cover_path='', cover_fetched=0 WHERE id=?", (r["id"],))
    retry_before = int(time.time()) - 30 * 86400
    # Clé en main : SGDB reprend aussi les couvertures d'origine CDN — ou
    # d'origine inconnue, héritage des bases d'avant la colonne cover_src. La
    # garde 30 jours limite ces tentatives comme celles des manquantes ; sans
    # clé, rien ne change : le CDN reste la seule source.
    porte = "(cover_path='' OR cover_src != 'sgdb')" if sgdb_key() else "cover_path=''"
    where = f"custom_cover='' AND {porte} AND (cover_fetched=0 OR cover_fetched < ?)"
    cond = source_cond(con)
    if cond:
        where += f" AND {cond}"
    rows = con.execute(f"SELECT * FROM games WHERE {where} LIMIT ?", (retry_before, limit)).fetchall()
    done = 0
    for g in rows:
        try:
            ok = fetch_cover(con, g, fetch)
        except SgdbAuthError as e:
            if progress:
                progress(tr("! covers: {e}", e=e))
            break
        except NETWORK_ERRORS as e:  # hors ligne pour de bon : on casse plutôt que d'enterrer des dizaines de jeux
            if progress:
                progress(tr("! covers: {e}", e=e))
            break
        done += ok
        if progress:
            progress(tr("  {name}: {state}", name=g["name"], state=tr("ok") if ok else tr("not found")))
        time.sleep(delay)
    if IGDB_STATE["error"] and progress:
        progress(tr("! covers: IGDB: {e}", e=IGDB_STATE["error"]))
    return done
