#!/usr/bin/env python3
r"""epic.py — la SESSION DU LANCEUR Epic officiel sert à charger les jeux possédés.

Le lanceur Epic ne range nulle part la liste de ce que l'utilisateur possède : ses
seuls fichiers (`LauncherInstalled.dat`, les `*.item`) ne listent que les
installations. Il garde en revanche SON PROPRE jeton de connexion, dans
`GameUserSettings.ini` (`%LOCALAPPDATA%\EpicGamesLauncher\Saved\Config\Windows`),
section `[RememberMe]`, clé `Data` : base64, dessous un JSON `[{...}]` — ou le même
JSON chiffré AES-256-ECB avec la clé publiée à côté du lanceur. C'est exactement ce
que `legendary auth --import` relit (« Import refresh token from EGL installation »,
`core.py::auth_import`).

Ce module fait donc trois choses, dans cet ordre de confiance :

1. il LIT le jeton du lanceur (jamais d'écriture chez lui) et l'échange contre une
   session — `oauth/token`, `grant_type=refresh_token`, `token_type=eg1` ;
2. il garde SA propre session rafraîchie dans `epic_session.json` (0600) : le jeton du
   lanceur n'est donc relu, au pire, qu'une fois par expire — et jamais écrit ;
3. il charge la bibliothèque (`library/api/public/items`, paginée par `cursor`) et
   donne un titre à chaque jeu via le catalogue (`catalog/.../bulk/items`) — une
   requête par jeu inconnu, donc en parallèle, la première fois seulement.

Sans session — pas de lanceur, aucun jeton lisible, Epic qui refuse — on renvoie
rien : hors Windows c'est le cas normal. Les pannes réelles (réseau) remontent,
pour être annoncées par l'appelant.
"""
import base64
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import platform

CLIENT_ID = "34a02cf8f4414e29b15921876da36f9a"          # le client du lanceur lui-même
CLIENT_SECRET = "daafbccc737745039dffe53d94fc76cf"       # public (legendary, Playnite)
BASIC = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
UA = "UELauncher/11.0.1-14907503+++Portal+Release-Live Windows/10.0.19041.1.256.64bit"

OAUTH_URL = "https://account-public-service-prod03.ol.epicgames.com/account/api/oauth/token"
LIBRARY_URL = "https://library-service.live.use1a.on.epicgames.com/library/api/public/items"
CATALOG_URL = "https://catalog-public-service-prod06.ol.epicgames.com/catalog/api/shared/namespace/"

# Clés de chiffrement du `Data` du lanceur, publiées avec lui (la clé change à chaque
# version du lanceur ; celle-ci accompagne la 15.18.2, relevée dans
# `api.legendary.gl/v1/version.json` → `egl_config.data_keys`). Les clés courantes
# sont relues chez legendary au besoin — `DATA_KEYS` n'est que le secours embarqué.
DATA_KEYS = ("A09C853C9E95409BB94D707EADEFA52E",)
LEGENDARY_VERSION_URL = "https://api.legendary.gl/v1/version.json"

SESSION_NAME = "epic_session.json"
CATALOG_CACHE_NAME = "epic_catalog.json"
DATA_KEYS_CACHE_NAME = "epic_data_keys.json"
MARGE_SEC = 600  # réutiliser une session qui n'a plus 10 minutes (règle de legendary)


class EpicError(Exception):
    """Refus d'Epic (jeton refusé, bibliothèque inexistante...) — annonçable."""


# ---------- le jeton du lanceur ----------

def settings_files(osname=None, env=None, home=None):
    """`GameUserSettings.ini` du lanceur Epic officiel (Windows natif, ou via Wine)."""
    return [d / "GameUserSettings.ini" for d in platform.epic_settings_dirs(osname, env, home)]


def _section_value(texte, section, cle):
    """Valeur d'une clé d'une section, lu SANS configparser : les fichiers d'Epic
    contiennent des lignes qu'un parseur strict refuserait, et la valeur base64 peut
    être coupée sur une ligne indentée (continuation)."""
    courant, valeur = None, None
    for ligne in texte.splitlines():
        s = ligne.strip()
        if s.startswith("[") and s.endswith("]"):
            courant = s[1:-1].strip()
            continue
        if valeur is not None:
            if ligne[:1] in (" ", "\t"):  # valeur poursuivie sur la ligne suivante
                valeur += s
            continue
        if courant != section:
            continue
        k, sep, v = s.partition("=")
        if sep and k.strip() == cle:
            valeur = v.strip()
    return valeur


def parse_remember_me(value, keys=None):
    """Jeton contenu dans un champ `Data` -> str, ou None.

    La valeur est du base64 ; dessous, soit du JSON tout clair (une LISTE à un
    élément — legendary lit `json.loads(...)[0]`), soit ce même JSON chiffré
    AES-256-ECB avec l'une des clés publiées avec le lanceur. Un format inconnu
    n'est pas une erreur à crier : c'est « pas de jeton »."""
    try:
        s = str(value).strip()
        brut = base64.b64decode(s + "=" * (-len(s) % 4))
    except ValueError:  # base64 invalide
        return None
    if not brut:
        return None
    jeton = _jeton_du_json(brut)
    if jeton:
        return jeton
    for cle in (DATA_KEYS if keys is None else keys):
        try:
            jeton = _jeton_du_json(_aes_ecb_decrypt(cle.encode("ascii"), brut))
        except Exception:  # clé incorrecte, longueur non multiple de 16... : suivante
            continue
        if jeton:
            return jeton
    return None


def _jeton_du_json(brut):
    """`Token` d'un JSON Epic (liste ou objet), ou None si ce n'est pas du JSON."""
    try:
        obj = json.loads(brut.rstrip(b"\x00").decode("utf-8", "replace"))
    except ValueError:
        return None
    if isinstance(obj, list):
        obj = obj[0] if obj and isinstance(obj[0], dict) else None
    jeton = obj.get("Token") if isinstance(obj, dict) else None
    return str(jeton) if jeton else None


def cles_cache(osname=None, env=None, home=None):
    """Clés de chiffrement mises en cache par `cles_de_legendary`, ou [] si le cache
    est absent ou illisible."""
    try:
        data = json.loads((platform.data_dir(osname, env, home) / DATA_KEYS_CACHE_NAME)
                          .read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    cles = data.get("keys") if isinstance(data, dict) else data
    return [str(c) for c in cles if isinstance(c, str)] if isinstance(cles, list) else []


def cles_de_legendary(fetch=None, osname=None, env=None, home=None):
    """Clés PUBLIÉES par legendary (`version.json` → `egl_config.data_keys`), mises
    en cache pour les imports suivants — la même source que `legendary auth --import`.

    La clé du `Data` change à chaque version du lanceur : sans ce repli, un lanceur
    mis à jour rend son jeton illisible et les possédés disparaissent en silence.
    Toute panne (réseau, réponse inattendue) retombe sur le cache sans crier."""
    cles = cles_cache(osname, env, home)
    try:
        # timeout court : ce petit JSON ne vaut pas 15 s d'attente sur un réseau mort
        res = _http_json(LEGENDARY_VERSION_URL, timeout=5, fetch=fetch)
        publiees = res["egl_config"]["data_keys"]
        cles = list(dict.fromkeys(
            [*cles, *(str(c) for c in publiees if isinstance(c, str))]))
        chemin = platform.data_dir(osname, env, home) / DATA_KEYS_CACHE_NAME
        chemin.parent.mkdir(parents=True, exist_ok=True)
        temp = chemin.with_name(chemin.name + ".tmp")
        temp.write_text(json.dumps({"keys": cles}), encoding="utf-8")
        os.replace(temp, chemin)
    except Exception:  # réseau coupé, clé absente... : le cache tient lieu de secours
        pass
    return cles


def launcher_refresh_token(osname=None, env=None, home=None, fetch=None):
    """Jeton de rafraîchissement du lanceur, lu EN LECTURE SEULE, ou None.

    Le champ est chiffré avec une clé qui change à chaque version du lanceur : on
    essaie celles embarquées, puis celles en cache, puis la publication de legendary
    — une panne réseau ne rend donc illisible qu'un jeton déjà inconnu des trois."""
    for path in settings_files(osname, env, home):
        try:
            texte = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        valeur = _section_value(texte, "RememberMe", "Data")
        if not valeur:
            continue
        jeton = (parse_remember_me(valeur)
                 or parse_remember_me(valeur, keys=cles_cache(osname, env, home))
                 or parse_remember_me(
                     valeur, keys=cles_de_legendary(fetch=fetch, osname=osname,
                                                    env=env, home=home)))
        if jeton:
            return jeton
    return None


# ---------- AES-256-ECB ----------
# Le champ du lanceur est chiffré AES-256-ECB avec une clé ASCII de 32 caractères.
# Python n'a pas d'AES dans sa bibliothèque standard et Ludodex n'a qu'une seule
# dépendance (PyYAML) : ce qui suit est l'écriture directe du FIPS-197, bloc de 16
# en bloc, sans table pré-calculée.

def _gf_mul(a, b):
    """Produit dans GF(2^8), polynôme x^8+x^4+x^3+x+1."""
    p = 0
    for _ in range(8):
        if b & 1:
            p ^= a
        a <<= 1
        if a & 0x100:
            a ^= 0x11B
        b >>= 1
    return p


def _sboxes():
    """S-box et sa réciproque, construites (affine ∘ inverse multiplicatif)."""
    inv = [0] * 256
    for i in range(1, 256):
        for j in range(1, 256):
            if _gf_mul(i, j) == 1:
                inv[i] = j
                break

    def rot(x):
        return ((x << 1) | (x >> 7)) & 0xFF

    sbox = [0] * 256
    for i in range(256):
        v = inv[i]
        sbox[i] = v ^ rot(v) ^ rot(rot(v)) ^ rot(rot(rot(v))) ^ rot(rot(rot(rot(v)))) ^ 0x63
    retour = [0] * 256
    for i, v in enumerate(sbox):
        retour[v] = i
    return sbox, retour


_SBOX, _INV_SBOX = _sboxes()


def _expand_key(cle):
    """Les 60 mots de la clé AES-256 (8 mots de clé, 14 tours)."""
    if len(cle) != 32:
        raise ValueError("clé AES-256 attendue")
    mots = [int.from_bytes(cle[4 * i:4 * i + 4], "big") for i in range(8)]
    rcon = 1
    for i in range(8, 60):
        t = mots[i - 1]
        if i % 8 == 0:  # RotWord + SubWord + Rcon
            t = ((_SBOX[(t >> 16) & 0xFF] << 24) | (_SBOX[(t >> 8) & 0xFF] << 16) |
                 (_SBOX[t & 0xFF] << 8) | _SBOX[(t >> 24) & 0xFF]) ^ (rcon << 24)
            rcon = _gf_mul(rcon, 2)
        elif i % 8 == 4:  # SubWord
            t = ((_SBOX[(t >> 24) & 0xFF] << 24) | (_SBOX[(t >> 16) & 0xFF] << 16) |
                 (_SBOX[(t >> 8) & 0xFF] << 8) | _SBOX[t & 0xFF])
        mots.append(mots[i - 8] ^ t)
    return mots


def _inv_shift_rows(s):
    """Rangée r décalée de r vers la droite (indices de colonne 4c+r)."""
    s[1], s[5], s[9], s[13] = s[13], s[1], s[5], s[9]
    s[2], s[6], s[10], s[14] = s[10], s[14], s[2], s[6]
    s[3], s[7], s[11], s[15] = s[7], s[11], s[15], s[3]


def _inv_mix_columns(s):
    for c in range(4):
        i = 4 * c
        a0, a1, a2, a3 = s[i:i + 4]
        s[i] = _gf_mul(a0, 14) ^ _gf_mul(a1, 11) ^ _gf_mul(a2, 13) ^ _gf_mul(a3, 9)
        s[i + 1] = _gf_mul(a0, 9) ^ _gf_mul(a1, 14) ^ _gf_mul(a2, 11) ^ _gf_mul(a3, 13)
        s[i + 2] = _gf_mul(a0, 13) ^ _gf_mul(a1, 9) ^ _gf_mul(a2, 14) ^ _gf_mul(a3, 11)
        s[i + 3] = _gf_mul(a0, 11) ^ _gf_mul(a1, 13) ^ _gf_mul(a2, 9) ^ _gf_mul(a3, 14)


def _add_round_key(s, mots, tour):
    for c in range(4):
        k = mots[4 * tour + c]
        s[4 * c] ^= (k >> 24) & 0xFF
        s[4 * c + 1] ^= (k >> 16) & 0xFF
        s[4 * c + 2] ^= (k >> 8) & 0xFF
        s[4 * c + 3] ^= k & 0xFF


def _aes_ecb_decrypt(cle, brut):
    """Déchiffre AES-256-ECB puis retire le remplissage PKCS#7.

    Déroulé inverse du FIPS-197 : AddRoundKey(14), puis les tours 13 → 1
    (InvShiftRows, InvSubBytes, AddRoundKey, InvMixColumns), puis le tour 0 sans
    InvMixColumns."""
    if len(brut) % 16 or not brut:
        raise ValueError("données AES invalides")
    mots = _expand_key(cle)
    sortie = bytearray()
    for i in range(0, len(brut), 16):
        s = list(brut[i:i + 16])
        _add_round_key(s, mots, 14)
        for tour in range(13, 0, -1):
            _inv_shift_rows(s)
            s[:] = [_INV_SBOX[v] for v in s]
            _add_round_key(s, mots, tour)
            _inv_mix_columns(s)
        _inv_shift_rows(s)
        s[:] = [_INV_SBOX[v] for v in s]
        _add_round_key(s, mots, 0)
        sortie += bytes(s)
    texte = bytes(sortie)
    n = texte[-1] if texte else 0
    if 1 <= n <= 16 and texte.endswith(bytes([n]) * n):  # PKCS#7
        texte = texte[:-n]
    return texte


# ---------- la session ----------

def session_path(osname=None, env=None, home=None):
    """Notre propre session, dans NOTRE dossier de données (0600 : c'est un jeton)."""
    return platform.data_dir(osname, env, home) / SESSION_NAME


def load_session(osname=None, env=None, home=None):
    try:
        data = json.loads(session_path(osname, env, home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("access_token") else None


def save_session(res, osname=None, env=None, home=None):
    """Conserve la session retournée par oauth — y compris le jeton de rafraîchissement
    tourné, si Epic en a renvoyé un : c'est NOTRE session, plus jamais celle du
    lanceur qu'il faille retoucher."""
    ancien = load_session(osname, env, home) or {}
    valide_jusqua = time.time() + float(res.get("expires_in") or 3600) - MARGE_SEC
    sess = {"access_token": res["access_token"],
            "refresh_token": res.get("refresh_token") or ancien.get("refresh_token"),
            "valid_until": valide_jusqua}
    path = session_path(osname, env, home)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(sess), encoding="utf-8")
    os.chmod(temp, 0o600)
    os.replace(temp, path)
    return sess


def clear_session(osname=None, env=None, home=None):
    try:
        session_path(osname, env, home).unlink()
    except OSError:
        pass


def _cache_key(asset):
    """Clé d'entrée de catalogue : namespace + item, la paire que l'API exige."""
    return f"{asset['namespace']}/{asset['catalog_id']}"


def load_catalog_cache(osname=None, env=None, home=None):
    """Cache des titres déjà lus au catalogue : {clé, titre | null} — `null` veut dire
    « lu, mais pas un jeu » (DLC, jeton, moteur). C'est ce fichier qui empêche chaque
    démarrage de repayer le catalogue de tous les DLC : Playnite range la même chose
    dans `catalogcache`, legendary dans ses fichiers de métadonnées."""
    try:
        data = json.loads(catalog_cache_path(osname, env, home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_catalog_cache(cache, osname=None, env=None, home=None):
    path = catalog_cache_path(osname, env, home)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, path)


def catalog_cache_path(osname=None, env=None, home=None):
    return platform.data_dir(osname, env, home) / CATALOG_CACHE_NAME


def _http_json(url, data=None, headers=None, timeout=15, fetch=None):
    """JSON d'une requête Epic — `fetch` (url, data, headers) est le transport
    injectable des tests.

    Epic met ses refus dans le CORPS d'une réponse 4xx : l'exception doit donc être
    lue, pas laissée tomber."""
    if fetch is not None:
        return fetch(url, data, headers)
    req = urllib.request.Request(url, data=data, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            corps = r.read()
    except urllib.error.HTTPError as e:
        corps = e.read()
    try:
        return json.loads(corps.decode("utf-8", "replace"))
    except ValueError:
        raise EpicError(f"réponse illisible de {url.split('?')[0]}") from None


def start_session(refresh_token, fetch=None):
    """Échange un jeton de rafraîchissement contre une session (`token_type=eg1`)."""
    corps = urllib.parse.urlencode({"grant_type": "refresh_token",
                                    "refresh_token": refresh_token,
                                    "token_type": "eg1"}).encode()
    res = _http_json(OAUTH_URL, data=corps,
                     headers={"Authorization": "Basic " + BASIC,
                              "Content-Type": "application/x-www-form-urlencoded",
                              "User-Agent": UA}, fetch=fetch)
    if not isinstance(res, dict) or "access_token" not in res or "errorCode" in res:
        raison = res.get("errorMessage") if isinstance(res, dict) else None
        raise EpicError(str(raison or "échange de jeton refusé"))
    return res


def ensure_session(fetch=None, now=None, osname=None, env=None, home=None):
    """Session valide : LA NOTRE d'abord, celle du lanceur seulement en repli.

    L'ordre compte : le fichier du lanceur n'est lu que quand nous n'avons plus rien
    de valable, ce qui limite à une fois par expire notre emprunt à sa session — on
    ne le modifie jamais, on ne fait que le LIRE."""
    now = time.time() if now is None else now
    ancien = load_session(osname, env, home)
    if ancien and float(ancien.get("valid_until") or 0) > now:
        return ancien
    candidats = list(dict.fromkeys(
        [t for t in ((ancien or {}).get("refresh_token"),
                     launcher_refresh_token(osname, env, home, fetch=fetch)) if t]))
    for jeton in candidats:
        try:
            res = start_session(jeton, fetch=fetch)
        except EpicError:  # jeton refusé : le suivant, sinon rien du tout
            continue
        return save_session(res, osname, env, home)
    return None


# ---------- la bibliothèque ----------

def fetch_library(access_token, fetch=None):
    """Toute la bibliothèque possédée : la réponse est paginée par `cursor`.

    Epic renvoie parfois la chaîne « null » au lieu d'un absent : prise telle quelle,
    elle ferait boucler la lecture jusqu'à l'éternel."""
    out, url = [], LIBRARY_URL + "?includeMetadata=true"
    while url:
        res = _http_json(url, headers={"Authorization": f"bearer {access_token}",
                                       "User-Agent": UA}, fetch=fetch)
        if not isinstance(res, dict) or "errorCode" in res:
            raison = res.get("errorMessage") if isinstance(res, dict) else None
            raise EpicError(str(raison or "bibliothèque introuvable"))
        out += [r for r in (res.get("records") or []) if isinstance(r, dict)]
        meta = res.get("responseMetadata")
        curseur = (meta or {}).get("nextCursor") if isinstance(meta, dict) else None
        url = (f"{LIBRARY_URL}?includeMetadata=true"
               f"&cursor={urllib.parse.quote(str(curseur), safe='')}"
               if curseur and str(curseur) != "null" else None)
    return out


def parse_asset(rec):
    """Entrée de la bibliothèque -> {ext_id, namespace, catalog_id}, ou None.

    Filtres du plugin Epic de Playnite : `ue` est l'éditeur d'Unreal Engine, pas un
    jeu ; `PRIVATE` un sandbox privé ; sans `appName` rien n'est identifiable —
    Playnite renonce dans les trois cas."""
    if not isinstance(rec, dict):
        return None
    ext = rec.get("appName")
    if not ext or rec.get("namespace") == "ue" or rec.get("sandboxType") == "PRIVATE":
        return None
    return {"ext_id": str(ext), "namespace": str(rec.get("namespace") or ""),
            "catalog_id": str(rec.get("catalogItemId") or "")}


def catalog_game(item):
    """Titre affichable si l'objet du catalogue est un vrai jeu, sinon None.

    Les filtres de Playnite : sans catégorie `applications`, ce n'est pas un jeu ; un
    `mainGameItem` fait un DLC — sauf addon lanceable ; `digitalextras`, `plugins` et
    `plugins/engine` sont des extras (jetons, extension d'éditeur, moteur). Les ™®©
    qu'Epic met partout dans les titres sortent du nom."""
    if not isinstance(item, dict):
        return None
    titre = item.get("title")
    if not titre:
        return None
    cats = {c.get("path") for c in (item.get("categories") or [])
            if isinstance(c, dict) and c.get("path")}
    if "applications" not in cats:
        return None
    if item.get("mainGameItem") and "addons/launchable" not in cats:
        return None
    if cats & {"digitalextras", "plugins", "plugins/engine"}:
        return None
    return re.sub(r"[™®©]", "", str(titre)).strip()


def fetch_catalog_title(access_token, asset, fetch=None):
    """Titre d'un jeu via le catalogue, ou None (identifiants absents, requête perdue)."""
    cid, ns = asset.get("catalog_id"), asset.get("namespace")
    if not cid or not ns:
        return None
    url = (f"{CATALOG_URL}{urllib.parse.quote(ns, safe='')}/bulk/items"
           f"?id={urllib.parse.quote(cid, safe='')}&country=US&locale=en-US"
           "&includeMainGameDetails=true")
    res = _http_json(url, headers={"Authorization": f"bearer {access_token}",
                                   "User-Agent": UA}, fetch=fetch)
    if not isinstance(res, dict):
        return None
    item = res.get(cid)
    if item is None and len(res) == 1:  # clé inattendue : la réponse unique est la bonne
        item = next(iter(res.values()))
    return catalog_game(item)


def catalog_titles(access_token, assets, fetch=None, workers=6):
    """{ext_id: titre ou None} — une requête de catalogue par jeu, EN PARALLÈLE.

    Une première importation vaut une requête par jeu inconnu : en série, une grande
    bibliothèque coûterait des dizaines de secondes de démarrage gelé. Un jeu
    introuvable n'empêche pas les autres d'être nommés ; mais si RIEN n'a pu être lu,
    la panne est réelle et doit remonter — un import vide silencieux mentirait.

    Un `None` dans le résultat est une LECTURE (« pas un jeu ») ; une clé ABSENTE est
    une requête perdue, à redemander — c'est cette distinction qui protège le cache
    d'une panne réseau."""
    if not assets:
        return {}
    titres, erreurs = {}, []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futurs = {pool.submit(fetch_catalog_title, access_token, a, fetch): a
                  for a in assets}
        for fut in as_completed(futurs):
            try:
                titre = fut.result()
            except Exception as e:  # un jeu à part n'emporte pas les autres
                erreurs.append(e)
                continue
            titres[futurs[fut]["ext_id"]] = titre
    if erreurs and not titres:
        raise erreurs[0]
    return titres


def epic_owned_games(known=(), fetch=None, workers=6, osname=None, env=None, home=None):
    """Jeux possédés sur Epic, via la session du LANCEUR -> [{ext_id, name}].

    Trois portes d'entrée, du moins cher au plus cher, et aucune ne se referme
    deux fois : les jeux DÉJÀ connus de la base sont écartés d'abord (ils ont leur
    titre), puis le cache des titres (les DLC y dorment en « pas un jeu »), et le
    catalogue n'est payé jeu par jeu qu'à ceux qui restent.

    Pas de session : rien, en silence, sauf si le lanceur est présent sans jeton
    lisible, auquel cas c'est dit (un ami qui voit toujours sa bibliothèque vide a
    besoin de savoir pourquoi)."""
    sess = ensure_session(fetch=fetch, osname=osname, env=env, home=home)
    if not sess:
        if any(p.exists() for p in settings_files(osname, env, home)):
            print("! Epic Games : session impossible (jeton introuvable ou refusé)")
        return []
    try:
        records = fetch_library(sess["access_token"], fetch=fetch)
    except EpicError:
        clear_session(osname, env, home)  # session refusée : on en reprend une neuve
        sess = ensure_session(fetch=fetch, osname=osname, env=env, home=home)
        if not sess:
            raise
        records = fetch_library(sess["access_token"], fetch=fetch)
    cache = load_catalog_cache(osname, env, home)
    assets = [a for a in (parse_asset(r) for r in records)
              if a and a["ext_id"] not in known]
    manquants = [a for a in assets if _cache_key(a) not in cache]
    if manquants:
        resultats = catalog_titles(sess["access_token"], manquants, fetch=fetch,
                                   workers=workers)
        if any(a["ext_id"] in resultats for a in manquants):  # seuls les LUS sont gardés
            for a in manquants:
                if a["ext_id"] in resultats:
                    cache[_cache_key(a)] = resultats[a["ext_id"]]
            save_catalog_cache(cache, osname, env, home)
    out = []
    for a in assets:
        titre = cache.get(_cache_key(a))
        if titre:
            out.append({"ext_id": a["ext_id"], "name": titre})
    return out
