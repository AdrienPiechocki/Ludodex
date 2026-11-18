#!/usr/bin/env python3
"""community.py — dépôt de fichiers de succès YAML partagés (dossier `achievements/` du dépôt).

Chaque jeu a son dossier `achievements/<id>/` : le YAML, éventuellement le mod qui produit
les logs (zip, plugin…) et un README. `achievements/index.json`, généré par
`tools/build_index.py` (et vérifié en CI), décrit le tout : nom, alias, appid Steam, nombre de
succès, empreintes SHA-256. L'application ne lit que cet index et les fichiers qu'il nomme ;
rien n'est exécuté, aucun compte ni clé n'est demandé.

Source : `LUDODEX_REPO` (URL http(s) du dossier `achievements/`, ou chemin local — pratique pour
un fork, un dépôt d'équipe ou les tests), sinon le dépôt officiel sur GitHub.

Un fichier téléchargé n'est installé qu'après contrôle de son empreinte ET de sa validité
(`tracker.load_defs`) : un fichier corrompu, falsifié ou cassé ne remplace jamais le précédent.
"""
import hashlib
import json
import os
import pathlib
import re
import shutil
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

import yaml

from i18n import tr
from .platform import data_dir
from . import rules, tracker

DEFAULT_SOURCE = "https://raw.githubusercontent.com/AdrienPiechocki/Ludodex/main/achievements/"
SCHEMA = 1
UA = "ludodex/1.0"


class CommunityError(Exception):
    """Dépôt injoignable, index illisible ou fichier refusé (empreinte, YAML invalide)."""


def source():
    return os.environ.get("LUDODEX_REPO", "").strip() or DEFAULT_SOURCE


def install_dir():
    return data_dir() / "achievements"


def _read(rel, base=None, timeout=30):
    """Octets de `rel` dans le dépôt (URL ou dossier local)."""
    base = base or source()
    if re.match(r"^https?://", base):
        url = base.rstrip("/") + "/" + urllib.parse.quote(rel)
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}),
                                        timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            raise CommunityError(tr("Community repository answered {code} for {path}", code=e.code, path=rel))
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise CommunityError(tr("Community repository unreachable: {e}", e=e))
    path = pathlib.Path(base.removeprefix("file://")).expanduser() / rel
    try:
        return path.read_bytes()
    except OSError as e:
        raise CommunityError(tr("Cannot read {path}: {e}", path=path, e=e.strerror or e))


def load_index(base=None):
    """Entrées de l'index : [{id, name, aliases, steam_appid, count, version, yaml, sha256, extras, …}]."""
    try:
        data = json.loads(_read("index.json", base).decode("utf-8"))
        games = data["games"]
        if data.get("schema") != SCHEMA or not isinstance(games, list):
            raise ValueError("schema")
    except (ValueError, KeyError, TypeError, UnicodeDecodeError):
        raise CommunityError(tr("Community index unreadable or from a newer Ludodex"))
    return games


def _norm(s):
    """Nom sans accents, ponctuation ni casse (les écritures non latines sont conservées)."""
    s = unicodedata.normalize("NFKD", str(s).casefold())
    return re.sub(r"[\W_]+", "", "".join(c for c in s if not unicodedata.combining(c)))


def search(index, query=""):
    """Entrées dont l'id, le nom ou un alias contient `query` (accents et casse ignorés)."""
    q = _norm(query)
    if not q:
        return list(index)
    return [e for e in index if any(q in _norm(x) for x in [e["id"], e["name"], *e.get("aliases", [])])]


def match_game(index, game):
    """Entrée qui correspond à un jeu de la bibliothèque (appid Steam, puis nom ou alias exact), ou None."""
    try:
        appid = int(game["ext_id"]) if game["source"] == "steam" else 0
    except (TypeError, ValueError):
        appid = 0
    if appid:
        for e in index:
            if e.get("steam_appid") == appid:
                return e
    n = _norm(game["name"])
    for e in index:
        if n and n in {_norm(x) for x in [e["name"], e["id"], *e.get("aliases", [])]}:
            return e
    return None


# ---------- installation ----------

def _manifest_path():
    return install_dir() / "installed.json"


def installed():
    """{id: {version, sha256, yaml, installed}} des fichiers déjà installés."""
    try:
        return json.loads(_manifest_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_manifest(m):
    install_dir().mkdir(parents=True, exist_ok=True)
    _manifest_path().write_text(json.dumps(m, indent=1, ensure_ascii=False), encoding="utf-8")


def _verified(rel, sha, base):
    data = _read(rel, base)
    if hashlib.sha256(data).hexdigest() != sha:
        raise CommunityError(tr("Checksum mismatch for {path}: file refused", path=rel))
    return data


def install(entry, with_extras=False, base=None):
    """Télécharge le YAML (et, si demandé, le mod) d'une entrée ; renvoie le chemin du YAML installé.

    Le YAML est vérifié (empreinte puis `load_defs`) AVANT d'être écrit, et écrit par
    remplacement atomique : en cas d'échec, la version déjà installée reste intacte."""
    data = _verified(entry["yaml"], entry["sha256"], base)
    try:
        tracker.load_defs(yaml.safe_load(data.decode("utf-8")))
    except (ValueError, yaml.YAMLError, UnicodeDecodeError, TypeError, KeyError) as e:
        raise CommunityError(tr("Invalid achievements file in the repository: {e}", e=e))
    dest = install_dir() / entry["id"]
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / pathlib.PurePosixPath(entry["yaml"]).name
    tmp = target.with_suffix(".part")
    tmp.write_bytes(data)
    tmp.replace(target)
    if with_extras:
        for ex in entry.get("extras", []):
            blob = _verified(ex["path"], ex["sha256"], base)
            (dest / pathlib.PurePosixPath(ex["path"]).name).write_bytes(blob)
    m = installed()
    m[entry["id"]] = {"version": entry.get("version", ""), "sha256": entry["sha256"],
                      "yaml": str(target), "installed": int(time.time())}
    _save_manifest(m)
    return target


def outdated(index):
    """Entrées installées dont l'empreinte de l'index diffère de celle installée."""
    have = installed()
    return [e for e in index if e["id"] in have and have[e["id"]]["sha256"] != e["sha256"]]


def uninstall(entry_id):
    m = installed()
    if entry_id not in m:
        return False
    shutil.rmtree(install_dir() / entry_id, ignore_errors=True)
    del m[entry_id]
    _save_manifest(m)
    return True


# ---------- construction de l'index (utilisé par tools/build_index.py) ----------

def entry_for(folder, root):
    """Entrée d'index d'un dossier `achievements/<id>/` ; ValueError s'il est invalide."""
    folder = pathlib.Path(folder)
    yamls = sorted(folder.glob("*.yaml")) + sorted(folder.glob("*.yml"))
    if len(yamls) != 1:
        raise ValueError(f"{folder.name}: un seul fichier .yaml attendu, trouvé {len(yamls)}")
    y = yamls[0]
    data = tracker.read_tracker_file(y)
    defs = tracker.load_defs(data)
    head = rules.parse_header(data)
    sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    rel = lambda p: p.relative_to(root).as_posix()
    extras = [{"path": rel(p), "sha256": sha(p)} for p in sorted(folder.iterdir())
              if p.is_file() and p != y and p.name.lower() not in ("readme.md", "readme")]
    e = {"id": folder.name, "name": head.get("name") or folder.name,
         "aliases": head.get("aliases", []), "author": head.get("author", ""),
         "version": head.get("version", ""), "language": head.get("language", ""),
         "description": head.get("description", ""), "log": head.get("log", ""),
         "mod": head.get("mod", ""), "url": head.get("url", ""),
         "count": len(defs), "hidden": sum(1 for d in defs if d["hidden"]),
         "yaml": rel(y), "sha256": sha(y), "extras": extras}
    if "steam_appid" in head:
        e["steam_appid"] = head["steam_appid"]
    return e, rules.lint(data)


def build_index(root):
    """(index, avertissements) pour tous les dossiers de `root`. Lève ValueError au premier fichier invalide."""
    root = pathlib.Path(root)
    games, warnings = [], []
    for folder in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
        entry, warn = entry_for(folder, root)
        games.append(entry)
        warnings += [f"{folder.name}: {w}" for w in warn]
    return {"schema": SCHEMA, "games": games}, warnings


def index_json(index):
    return json.dumps(index, indent=1, ensure_ascii=False, sort_keys=True) + "\n"
