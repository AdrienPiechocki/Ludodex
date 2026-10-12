"""tracker.py — succès définis par l'utilisateur, détectés dans les logs d'un jeu moddé.

Un fichier YAML attaché à un jeu (dans l'application) décrit des succès ; chacun
teste les lignes du fichier de log du jeu : un `match` (regex), un ensemble `any`
(au moins une correspondance) ou `all` (chaque pattern doit apparaître), avec un
compteur `count` (ex. « tue 50 ennemis »). La lecture du log est incrémentale :
la position lue est mémorisée en base (une seule ligne n'est comptée qu'une fois),
et un log tronqué/reparti de zéro est re-scanné depuis le début.
"""
import os
import pathlib
import re
import time
import unicodedata

import yaml

from .db import get_setting, set_setting


def _slug(name):
    """« Première quête » → « premiere-quete » (id par défaut d'un succès)."""
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s or "achievement"


def load_tracker(path):
    """Lit et valide un fichier de succès ; renvoie la liste des définitions.

    Chaque définition : id, name, description, icon, op (match|any|all),
    patterns, regexes (compilées), target (= `count`, 1 par défaut).
    """
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except OSError:
        raise
    except yaml.YAMLError as e:
        raise ValueError(f"YAML invalide : {e}") from e
    if not isinstance(data, dict) or not isinstance(data.get("achievements"), list):
        raise ValueError("le fichier doit contenir une liste « achievements »")
    defs, ids = [], set()
    for i, a in enumerate(data["achievements"]):
        if not isinstance(a, dict) or not isinstance(a.get("name"), str) or not a["name"].strip():
            raise ValueError(f"succès n°{i + 1} : nom manquant")
        name = a["name"].strip()
        ach_id = str(a.get("id") or _slug(name))
        if not ach_id or re.search(r"\s", ach_id):
            raise ValueError(f"« {name} » : id invalide : {ach_id!r}")
        patterns, op = [], None
        if isinstance(a.get("match"), str) and a["match"].strip():
            op, patterns = "match", [a["match"].strip()]
        elif isinstance(a.get("any"), list):
            pats = [p for p in a["any"] if isinstance(p, str) and p.strip()]
            if pats:
                op, patterns = "any", pats
        elif isinstance(a.get("all"), list):
            pats = [p for p in a["all"] if isinstance(p, str) and p.strip()]
            if pats:
                op, patterns = "all", pats
        if not op:
            raise ValueError(f"« {name} » : il faut un « match » (ou « any »/« all »)")
        if "count" in a:
            count = a["count"]
            if not isinstance(count, int) or count < 1:
                raise ValueError(f"« {name} » : « count » doit être un entier ≥ 1")
        else:
            count = len(patterns) if op == "all" else 1  # « all » : chaque pattern doit apparaître
        flags = re.IGNORECASE if a.get("case_insensitive") else 0
        compiled = []
        for p in patterns:
            try:
                compiled.append(re.compile(p, flags))
            except re.error as e:
                raise ValueError(f"« {name} » : expression invalide : {e}") from e
        if ach_id in ids:
            raise ValueError(f"id dupliqué : {ach_id}")
        ids.add(ach_id)
        defs.append({"id": ach_id, "name": name,
                     "description": str(a.get("description") or ""),
                     "icon": str(a.get("icon") or ""),
                     "op": op, "patterns": patterns, "regexes": compiled,
                     "target": count})
    return defs


def tracker_paths(con, gid):
    """(yaml, log) configurés pour ce jeu, ou None si aucun suivi."""
    yaml_path, log_path = get_setting(con, f"tracker_yaml_{gid}"), get_setting(con, f"tracker_log_{gid}")
    if not yaml_path or not log_path:
        return None
    return (yaml_path, log_path)


def set_tracker(con, gid, yaml_path, log_path):
    """Attache un fichier de succès et un fichier de log à un jeu."""
    set_setting(con, f"tracker_yaml_{gid}", str(yaml_path))
    set_setting(con, f"tracker_log_{gid}", str(log_path))


def clear_tracker(con, gid):
    """Retire la config (l'état des succès déjà acquis est conservé)."""
    set_setting(con, f"tracker_yaml_{gid}", "")
    set_setting(con, f"tracker_log_{gid}", "")


def tracked_games(con):
    """[(id, nom, yaml, log)] des jeux avec un suivi configuré."""
    out = []
    for r in con.execute("SELECT id, name FROM games"):
        p = tracker_paths(con, r["id"])
        if p:
            out.append((r["id"], r["name"], *p))
    return out


def _hits(d, line):
    """Nombre de correspondances de cette ligne pour la définition d."""
    if d["op"] == "match":
        return 1 if d["regexes"][0].search(line) else 0
    if d["op"] == "any":
        return 1 if any(r.search(line) for r in d["regexes"]) else 0
    return sum(1 for r in d["regexes"] if r.search(line))  # « all » : chaque pattern présent compte


def scan(con, gid, now=None):
    """Scanne les nouvelles lignes du log du jeu et met à jour les succès.

    Renvoie la liste des définitions avec leur état ({..., 'progress', 'done',
    'unlocked_at'}) — les succès verrouillés compris — ou None si aucun suivi
    n'est configuré. Lève ValueError (YAML invalide) / FileNotFoundError (log
    absent).
    """
    cfg = tracker_paths(con, gid)
    if cfg is None:
        return None
    yaml_path, log_path = cfg
    defs = load_tracker(yaml_path)
    now = time.time() if now is None else now

    state = {r["ach_id"]: dict(r) for r in con.execute(
        "SELECT ach_id, count, unlocked_at FROM achievements WHERE game_id=?", (gid,))}

    with open(log_path, "rb") as f:
        size = os.fstat(f.fileno()).st_size
        row = con.execute("SELECT offset, size, prefix FROM tracker_logs WHERE game_id=? AND log_path=?",
                          (gid, str(log_path))).fetchone()
        # « lignée » : si le début du fichier a changé, c'est un log réécrit depuis
        # zéro (rotation) → on re-scanne tout ; sinon c'est une simple append.
        check_n = min(4096, row["size"]) if row else 0
        keep = check_n and f.read(check_n).decode("latin-1") == row["prefix"]
        f.seek(0)
        prefix = f.read(min(4096, size)).decode("latin-1")
        if row is None or not keep or row["offset"] > size:
            start = 0  # premier scan, log réécrit ou tronqué
        else:
            start = row["offset"]
        f.seek(start)
        data = f.read()
    for line in data.decode("utf-8", "replace").splitlines():
        for d in defs:
            st = state.get(d["id"])
            if st and st["unlocked_at"]:
                continue  # déjà acquis
            hit = _hits(d, line)
            if not hit:
                continue
            if st is None:
                st = state[d["id"]] = {"ach_id": d["id"], "count": 0, "unlocked_at": 0}
            st["count"] = min(d["target"], st["count"] + hit)
            if st["count"] >= d["target"] and not st["unlocked_at"]:
                st["unlocked_at"] = now

    for d in defs:
        st = state.get(d["id"])
        if st:
            con.execute(
                "INSERT INTO achievements (game_id, ach_id, count, unlocked_at) VALUES (?,?,?,?) "
                "ON CONFLICT(game_id, ach_id) DO UPDATE SET count=excluded.count, "
                "unlocked_at=excluded.unlocked_at",
                (gid, d["id"], st["count"], st["unlocked_at"]))
    con.execute(
        "INSERT INTO tracker_logs (game_id, log_path, offset, size, prefix) VALUES (?,?,?,?,?) "
        "ON CONFLICT(game_id, log_path) DO UPDATE SET offset=excluded.offset, size=excluded.size, "
        "prefix=excluded.prefix",
        (gid, str(log_path), size, size, prefix))
    con.commit()

    out = []
    for d in defs:
        st = state.get(d["id"]) or {"ach_id": d["id"], "count": 0, "unlocked_at": 0}
        out.append({"id": d["id"], "name": d["name"], "description": d["description"],
                    "icon": d["icon"], "target": d["target"], "progress": st["count"],
                    "unlocked_at": st["unlocked_at"], "done": bool(st["unlocked_at"])})
    return out