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

from i18n import tr

from .db import get_setting, set_setting


def _slug(name):
    """« Première quête » → « premiere-quete » (id par défaut d'un succès)."""
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s or "achievement"


def load_tracker(path):
    """Lit et valide un fichier de succès ; renvoie la liste des définitions.

    Chaque définition : id, name, description, icon, hidden, op (match|any|all),
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
                     "hidden": bool(a.get("hidden")),
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


def reset(con, gid):
    """Efface la progression des succès d'un jeu.

    Le log est marqué comme lu jusqu'à sa fin courante : les lignes déjà
    présentes ne sont pas recomptées (on repart « à partir de maintenant »,
    sinon le scan suivant re-débloquerait tout). Renvoie le nombre d'états
    de succès effacés.
    """
    n = con.execute("SELECT COUNT(*) AS n FROM achievements WHERE game_id=?",
                    (gid,)).fetchone()["n"]
    con.execute("DELETE FROM achievements WHERE game_id=?", (gid,))
    cfg = tracker_paths(con, gid)
    if cfg is not None:
        log_path = cfg[1]
        try:
            with open(log_path, "rb") as f:
                size = os.fstat(f.fileno()).st_size
                f.seek(0)
                prefix = f.read(min(4096, size)).decode("latin-1")
        except OSError:
            pass  # log absent : on n'avance pas la position
        else:
            con.execute(
                "INSERT INTO tracker_logs (game_id, log_path, offset, size, prefix) VALUES (?,?,?,?,?) "
                "ON CONFLICT(game_id, log_path) DO UPDATE SET offset=excluded.offset, size=excluded.size, "
                "prefix=excluded.prefix",
                (gid, str(log_path), size, size, prefix))
    con.commit()
    return n


def tracked_games(con):
    """[(id, nom, yaml, log)] des jeux avec un suivi configuré."""
    out = []
    for r in con.execute("SELECT id, name FROM games"):
        p = tracker_paths(con, r["id"])
        if p:
            out.append((r["id"], r["name"], *p))
    return out


def achievement_games(con):
    """[(id, nom, has_tracker, has_ra)] des jeux ayant un suivi YAML et/ou des succès RA.

    Sert à la liste de gauche de la page « Achievements » : un jeu apparaît s'il a un
    fichier de log suivi et/ou un lien vers un jeu RetroAchievements."""
    from .ra import ra_linked_games  # import local : évite un cycle de chargement
    info = {}
    for gid, name, _yaml, _log in tracked_games(con):
        info[gid] = [name, True, False]
    for gid, _ra in ra_linked_games(con):
        row = con.execute("SELECT name FROM games WHERE id=?", (gid,)).fetchone()
        if row is None:
            continue
        entry = info.setdefault(gid, [row["name"], False, False])
        entry[2] = True
    return sorted(((gid, v[0], v[1], v[2]) for gid, v in info.items()),
                  key=lambda t: t[1].lower())


def show_hidden(con):
    """Réglage global « Reveal hidden achievements » : faut-il afficher les succès `hidden`.

    Sémantique de la valeur en base, écritue par la case des réglages :
      - `'1'`  → les succès secrets sont AFFICHÉS même verrouillés ;
      - `'0'`  → ils sont MASQUÉS ;
      - absent → ils sont MASQUÉS (c'est le comportement par défaut).

    ⚠️ Attention : le réglage `show_hidden` (sans préfixe) existe déjà mais
    concerne les JEUX masqués de la bibliothèque — d'où le préfixe `ach_`.

    Ne pas écrire `== "0"` ici : cela inverserait le sens de la case (cocher
    masquerait les succès). Le `'1'` de cette ligne correspond au `'1'` écrit
    par `_on_ach_show_hidden` quand la case est cochée."""
    return get_setting(con, "ach_show_hidden") == "1"


def log_fingerprint(con, gid):
    """(taille, mtime_ns) du log du jeu ; (0, 0) s'il n'est pas suivi ou a disparu.

    Sert à repérer une écriture sans ouvrir le fichier — bien plus cheap qu'un
    scan, et suffisant pour savoir si le jeu tourne encore."""
    p = tracker_paths(con, gid)
    if not p:
        return (0, 0)
    try:
        st = os.stat(p[1])
    except OSError:
        return (0, 0)
    return (st.st_size, st.st_mtime_ns)


class LogWatcher:
    """Détecte les logs en cours d'écriture pour actualiser les succès sans permission.

    On repère l'écriture du fichier de log plutôt qu'un processus : le chemin est
    déjà connu, et ça marche avec n'importe quel jeu.

    Politique, une seule passe par sonde :
      - le log a changé depuis la sonde précédente → le jeu écrit → « write » ;
      - il ne change plus alors qu'il écrivait encore → fermeture → « closed »
        (une seule fois, pour récupérer les dernières lignes écrites) ;
      - rien ne bouge → aucune notification, aucun travail.

    La première sonde établit la base sans rien renvoyer, sinon tous les jeux
    seraient rescannés au démarrage."""

    def __init__(self):
        self._prev = {}
        self._writing = set()

    def poll(self, con):
        """[(gid, raison)] des jeux à re-scanner : « write » ou « closed »."""
        out, alive = [], set()
        for gid, _name, _yaml, _log in tracked_games(con):
            alive.add(gid)
            fp = log_fingerprint(con, gid)
            prev = self._prev.get(gid)
            self._prev[gid] = fp
            if prev is None:
                continue                       # premier relevé : base établie
            if fp != prev:
                self._writing.add(gid)
                out.append((gid, "write"))
            elif gid in self._writing:         # les écritures viennent de s'arrêter
                self._writing.discard(gid)
                out.append((gid, "closed"))
        # un jeu retiré du suivi ne doit pas garder un état fantôme
        for gid in list(self._prev):
            if gid not in alive:
                del self._prev[gid]
                self._writing.discard(gid)
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
                    "hidden": d["hidden"],
                    "unlocked_at": st["unlocked_at"], "done": bool(st["unlocked_at"])})
    return out

# ---------- tri et filtre des succès (partagé par les succès YAML et RetroAchievements) ----------
ACHIEVEMENT_FILTERS = ("all", "unlocked", "locked")
ACHIEVEMENT_SORTS = ("origin", "title-asc", "title-desc", "unlocked", "locked")


def _norm(s):
    """Texte ramené à sa racine (sans accents ni ponctuation) pour la recherche/tri."""
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def is_unlocked(a):
    """Un succès est débloqué selon sa source : `done` (YAML) ou `earned_at` (RetroAchievements)."""
    return bool(a.get("done") or a.get("earned_at"))


def _title(a):
    """Nom d'affichage : `name` pour les succès YAML, `title` pour RetroAchievements."""
    return a.get("name") or a.get("title") or ""


def _description(a):
    return a.get("description") or ""


def is_masked(a, show_hidden=False):
    """Le succès est-il affiché sous un libellé générique (spoiler protégé) ?

    Source unique de vérité du masquage : le rendu de l'UI et la recherche des
    succès doivent donner le même verdict, sinon la barre de recherche sert
    d'oracle — on tape un nom au hasard et l'apparition d'une ligne confirme le
    spoiler. `hidden` n'existe que dans le YAML local : les succès
    RetroAchievements (sans cette clé) ne sont jamais masqués."""
    return bool(a.get("hidden")) and not bool(a.get("done")) and not show_hidden


def filter_achievements(rows, status="all", query="", show_hidden=False):
    """Sous-liste de `rows` selon le statut et la recherche (insensible aux accents).

    La recherche porte sur le titre ET la description **de ce qui s'affiche** :
    un succès masqué n'est retrouvable que par son libellé générique, jamais par
    son vrai nom ni par sa description (sinon la recherche trahirait le
    spoiler). `rows` est retourné tel quel quand aucun filtre n'est appliqué
    (la liste d'origine n'est pas copiée)."""
    if status not in ACHIEVEMENT_FILTERS:
        status = "all"
    if status != "all":
        want = status == "unlocked"
        rows = [a for a in rows if is_unlocked(a) == want]
    q = _norm(query)
    if q:
        generic = _norm(tr("Hidden achievement"))

        def matches(a):
            if is_masked(a, show_hidden):
                return q in generic
            return q in _norm(_title(a)) or q in _norm(_description(a))

        rows = [a for a in rows if matches(a)]
    return rows


def sort_achievements(rows, mode="origin"):
    """Copie triée de `rows` selon `mode` (cf. ACHIEVEMENT_SORTS).

    Les tris par statut sont stables : à part égalité, l'ordre d'origine est
    conservé (prévisible, et identique à l'ordre du fichier YAML)."""
    if mode == "title-asc":
        return sorted(rows, key=lambda a: _norm(_title(a)))
    if mode == "title-desc":
        return sorted(rows, key=lambda a: _norm(_title(a)), reverse=True)
    if mode == "unlocked":
        return sorted(rows, key=lambda a: not is_unlocked(a))
    if mode == "locked":
        return sorted(rows, key=lambda a: is_unlocked(a))
    return list(rows)
