#!/usr/bin/env python3
"""rules.py — moteur de règles des succès YAML.

Un succès porte UNE règle principale parmi :

  match: <regex>            une ligne correspond (compteur avec `count`)
  any:   [<regex>, …]       au moins un pattern sur la ligne
  all:   [<regex>, …]       chaque pattern apparaît quelque part dans le log
  sequence: [<regex>, …]    les patterns apparaissent DANS L'ORDRE (étapes successives)
  distinct: <regex>         `count` valeurs différentes capturées (groupe nommé `v`, sinon 1er groupe)
  sum:  <regex>             somme des nombres capturés ≥ `count`
  max:  <regex>             plus grand nombre capturé ≥ `count`
  all_of: [<id>, …]         méta-succès : tous les succès listés sont débloqués
  any_of: [<id>, …]         méta-succès : au moins `count` (1 par défaut) des succès listés

et des modificateurs facultatifs, valables pour toutes les règles sur lignes :

  exclude:  <regex>|[…]     une ligne qui correspond est ignorée
  reset_on: <regex>|[…]     remet la progression à zéro (« sans mourir », « d'affilée »)
  requires: [<id>, …]       ne compte qu'une fois ces succès débloqués
  count: N                  seuil (défaut : 1 ; `all` : nombre de patterns ; `sequence` : nombre d'étapes)
  case_insensitive: true

Les règles historiques (match / any / all + count) gardent exactement leur comportement.
La progression de chaque succès est un petit état (`State`) rejoué ligne à ligne : le
moteur ne lit jamais le log lui-même, tracker.scan() lui donne les nouvelles lignes.
"""
import json
import re
import unicodedata

LEGACY_OPS = ("match", "any", "all")
LINE_OPS = LEGACY_OPS + ("sequence", "distinct", "sum", "max")
META_OPS = ("all_of", "any_of")
PRIMARY = LINE_OPS + META_OPS
MODIFIERS = ("exclude", "reset_on", "requires", "count", "case_insensitive")
COMMON = ("id", "name", "description", "icon", "hidden")
KNOWN_KEYS = frozenset(PRIMARY + MODIFIERS + COMMON)
HEADER_KEYS = ("name", "steam_appid", "aliases", "author", "version", "language", "log", "mod", "description", "url")


def slug(name):
    """« Première quête » → « premiere-quete » (id par défaut d'un succès)."""
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s or "achievement"


# ---------- lecture / validation ----------

def _strings(v, name, field, strip=False):
    """Chaîne ou liste de chaînes non vides → liste ; None → [].

    Les expressions ne sont PAS rognées : une espace finale fait partie d'un pattern
    (« item=189 » ne doit pas reconnaître « item=1890 »). Seuls les ids le sont."""
    if v is None:
        return []
    if isinstance(v, str):
        v = [v]
    if not isinstance(v, list) or not all(isinstance(x, str) and x.strip() for x in v):
        raise ValueError(f"« {name} » : « {field} » doit être une expression ou une liste d'expressions")
    return [x.strip() if strip else x for x in v]


def _ids(v, name, field):
    out = _strings(v, name, field, strip=True)
    if not out:
        raise ValueError(f"« {name} » : « {field} » ne peut pas être vide")
    return out


def _compile(p, flags, name, field):
    try:
        return re.compile(p, flags)
    except re.error as e:
        raise ValueError(f"« {name} » : expression invalide dans « {field} » : {e}") from e


def _group(rx, name, field):
    """Groupe capturé par une règle distinct / sum / max : `v` s'il existe, sinon le 1er."""
    if rx.groups < 1:
        raise ValueError(f"« {name} » : « {field} » doit capturer une valeur avec un groupe ( … )")
    return "v" if "v" in rx.groupindex else 1


def _primary_ops(a):
    """Règles principales présentes, dans l'ordre de PRIMARY."""
    out = []
    for op in PRIMARY:
        v = a.get(op)
        if op == "match":
            if isinstance(v, str) and v.strip():
                out.append(op)
        elif op in ("distinct", "sum", "max"):
            if v is not None:
                out.append(op)
        elif isinstance(v, list) and any(isinstance(p, str) and p.strip() for p in v):
            out.append(op)
    return out


def compile_def(a, index):
    """Valide un succès du YAML et renvoie sa définition (voir tracker.load_tracker)."""
    if not isinstance(a, dict) or not isinstance(a.get("name"), str) or not a["name"].strip():
        raise ValueError(f"succès n°{index + 1} : nom manquant")
    name = a["name"].strip()
    ach_id = str(a.get("id") or slug(name))
    if not ach_id or re.search(r"\s", ach_id):
        raise ValueError(f"« {name} » : id invalide : {ach_id!r}")
    ops = _primary_ops(a)
    if not ops:
        raise ValueError(f"« {name} » : il faut une règle (« match », « any », « all », « sequence », "
                         f"« distinct », « sum », « max », « all_of » ou « any_of »)")
    if len(ops) > 1 and any(o not in LEGACY_OPS for o in ops):
        raise ValueError(f"« {name} » : une seule règle principale, trouvé : {', '.join(ops)}")
    op = ops[0]           # anciens fichiers : match > any > all, comme avant
    flags = re.IGNORECASE if a.get("case_insensitive") else 0

    patterns, members, group = [], [], None
    if op == "match":
        patterns = [a["match"].strip()]
    elif op in ("any", "all", "sequence"):
        patterns = _strings([p for p in a[op] if isinstance(p, str) and p.strip()], name, op)
    elif op in ("distinct", "sum", "max"):
        patterns = _strings(a[op], name, op)
        if len(patterns) != 1:
            raise ValueError(f"« {name} » : « {op} » prend une seule expression")
    else:
        members = _ids(a[op], name, op)
    regexes = [_compile(p, flags, name, op) for p in patterns]
    if op in ("distinct", "sum", "max"):
        group = _group(regexes[0], name, op)

    # seuil
    numeric = op in ("sum", "max")
    if "count" in a:
        count = a["count"]
        if numeric:
            if isinstance(count, bool) or not isinstance(count, (int, float)) or count <= 0:
                raise ValueError(f"« {name} » : « count » doit être un nombre > 0")
        elif not isinstance(count, int) or isinstance(count, bool) or count < 1:
            raise ValueError(f"« {name} » : « count » doit être un entier ≥ 1")
    elif numeric:
        raise ValueError(f"« {name} » : « {op} » demande un seuil « count »")
    elif op in ("all", "sequence"):
        count = len(patterns)
    elif op == "all_of":
        count = len(members)
    else:
        count = 1
    if op in ("all_of",) and count > len(members):
        raise ValueError(f"« {name} » : « count » dépasse le nombre de succès listés")
    if op == "any_of" and count > len(members):
        raise ValueError(f"« {name} » : « count » dépasse le nombre de succès listés")
    if op in ("all", "sequence") and count > len(patterns):
        raise ValueError(f"« {name} » : « count » dépasse le nombre de patterns")
    if isinstance(count, float) and count.is_integer():
        count = int(count)

    exclude = [_compile(p, flags, name, "exclude") for p in _strings(a.get("exclude"), name, "exclude")]
    reset = [_compile(p, flags, name, "reset_on") for p in _strings(a.get("reset_on"), name, "reset_on")]
    requires = _strings(a.get("requires"), name, "requires", strip=True)
    if op in META_OPS and (exclude or reset or requires):
        raise ValueError(f"« {name} » : « exclude », « reset_on » et « requires » ne s'appliquent pas à un méta-succès")
    return {"id": ach_id, "name": name, "description": str(a.get("description") or ""),
            "icon": str(a.get("icon") or ""), "hidden": bool(a.get("hidden")),
            "op": op, "patterns": patterns, "regexes": regexes, "target": count,
            "members": members, "group": group, "exclude": exclude, "reset": reset,
            "requires": requires}


def check_graph(defs):
    """Références (requires / all_of / any_of) : ids connus, pas de cycle."""
    ids = {d["id"]: d for d in defs}
    for d in defs:
        for ref in d["requires"] + d["members"]:
            if ref not in ids:
                raise ValueError(f"« {d['name']} » : succès inconnu « {ref} »")
            if ref == d["id"]:
                raise ValueError(f"« {d['name']} » : se référence lui-même")
    state = {}

    def visit(i, path):
        if state.get(i) == 2:
            return
        if state.get(i) == 1:
            raise ValueError("dépendance circulaire : " + " → ".join(path + [i]))
        state[i] = 1
        d = ids[i]
        for ref in d["requires"] + d["members"]:
            visit(ref, path + [i])
        state[i] = 2

    for i in ids:
        visit(i, [])


def lint(data):
    """Avertissements non bloquants sur un YAML déjà valide (coquilles, oublis).

    Utilisé par `ludodex repo validate` ; l'application, elle, ignore les clés inconnues."""
    out = []
    names = {}
    for i, a in enumerate(data.get("achievements") or []):
        if not isinstance(a, dict):
            continue
        label = a.get("name") or f"n°{i + 1}"
        for k in a:
            if k not in KNOWN_KEYS:
                out.append(f"« {label} » : clé inconnue « {k} » (coquille ?)")
        if not a.get("description"):
            out.append(f"« {label} » : pas de description")
        if a.get("name") in names:
            out.append(f"« {label} » : nom en double (n°{names[a['name']] + 1})")
        names.setdefault(a.get("name"), i)
        for op in LINE_OPS:
            pats = a.get(op)
            for p in ([pats] if isinstance(pats, str) else pats if isinstance(pats, list) else []):
                try:
                    if isinstance(p, str) and re.search(p, ""):
                        out.append(f"« {label} » : « {op} » correspond à une ligne vide (tout débloquerait)")
                except re.error:
                    pass
    return out


def parse_header(data):
    """Bloc facultatif `game:` d'un YAML (nom, appid, auteur, mod…) ; {} s'il manque."""
    h = data.get("game") if isinstance(data, dict) else None
    if not isinstance(h, dict):
        return {}
    out = {}
    for k in HEADER_KEYS:
        v = h.get(k)
        if v is None or v == "":
            continue
        if k == "aliases":
            v = [str(x) for x in v] if isinstance(v, list) else [str(v)]
        elif k == "steam_appid":
            try:
                v = int(v)
            except (TypeError, ValueError):
                continue
        else:
            v = str(v)
        out[k] = v
    return out


# ---------- état et évaluation ----------

def new_state():
    return {"count": 0, "unlocked_at": 0, "seen": set(), "step": 0, "value": 0.0}


def load_state(d, row):
    """État d'un succès depuis sa ligne `achievements` (ou neuf si absente)."""
    st = new_state()
    if row is None:
        return st
    st["count"], st["unlocked_at"] = row["count"], row["unlocked_at"]
    if d["op"] == "all":
        st["seen"] = {s for s in (row["seen_patterns"] or "").split(",") if s}
    extra = {}
    try:
        extra = json.loads(row["state"]) if row["state"] else {}
    except (ValueError, KeyError, IndexError):
        extra = {}
    if d["op"] == "distinct":
        st["seen"] = set(extra.get("seen", []))
    elif d["op"] == "sequence":
        st["step"] = int(extra.get("step", st["count"]))
    elif d["op"] in ("sum", "max"):
        st["value"] = float(extra.get("value", st["count"]))
    return st


def dump_state(d, st):
    """(seen_patterns, state_json) à écrire en base."""
    if d["op"] == "all":
        return ",".join(sorted(st["seen"])), ""
    if d["op"] == "distinct":
        return "", json.dumps({"seen": sorted(st["seen"])}, ensure_ascii=False)
    if d["op"] == "sequence":
        return "", json.dumps({"step": st["step"]})
    if d["op"] in ("sum", "max"):
        return "", json.dumps({"value": st["value"]})
    return "", ""


def number(text):
    """Nombre d'un texte de log : « 1 234 », « 1,234 », « 1.234,5 », « 3,5 ». None si illisible."""
    t = str(text).strip().replace(" ", "").replace(" ", "").replace(" ", "")
    if "," in t and "." in t:
        t = t.replace(",", "") if t.rfind(".") > t.rfind(",") else t.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"-?\d{1,3}(,\d{3})+", t):
        t = t.replace(",", "")
    else:
        t = t.replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


def _reset(st):
    st.update(count=0, seen=set(), step=0, value=0.0)


def feed(d, st, line, unlocked, now):
    """Rejoue une ligne de log sur la définition `d`. `unlocked(id)` dit si un autre succès est acquis.

    Renvoie True si la progression a bougé ; pose `unlocked_at` dès que le seuil est atteint
    (un `requires` doit voir, dans le même scan, ce qui vient d'être débloqué)."""
    if st["unlocked_at"] or d["op"] in META_OPS:
        return False
    if d["reset"] and any(r.search(line) for r in d["reset"]):
        moved = bool(st["count"] or st["seen"] or st["step"] or st["value"])
        _reset(st)
        return moved
    if d["requires"] and not all(unlocked(i) for i in d["requires"]):
        return False
    if d["exclude"] and any(r.search(line) for r in d["exclude"]):
        return False
    op, rx = d["op"], d["regexes"]
    if op == "match":
        if not rx[0].search(line):
            return False
        st["count"] += 1
    elif op == "any":
        if not any(r.search(line) for r in rx):
            return False
        st["count"] += 1
    elif op == "all":
        new = {str(i) for i, r in enumerate(rx) if r.search(line)} - st["seen"]
        if not new:
            return False
        st["seen"] |= new
        st["count"] = len(st["seen"])
    elif op == "sequence":
        if st["step"] >= len(rx) or not rx[st["step"]].search(line):
            return False
        st["step"] += 1
        st["count"] = st["step"]
    else:                                    # distinct / sum / max
        m = rx[0].search(line)
        if not m:
            return False
        raw = m.group(d["group"])
        if raw is None:
            return False
        if op == "distinct":
            val = raw.strip()
            if val in st["seen"]:
                return False
            st["seen"].add(val)
            st["count"] = len(st["seen"])
        else:
            x = number(raw)
            if x is None:
                return False
            if op == "sum":
                st["value"] += x
            elif x > st["value"]:
                st["value"] = x
            else:
                return False
            st["count"] = int(min(st["value"], d["target"]))
    if op not in ("sum", "max"):
        st["count"] = min(st["count"], int(d["target"]))
    if reached(d, st):
        st["unlocked_at"] = now
    return True


def reached(d, st):
    if d["op"] in ("sum", "max"):
        return st["value"] >= d["target"]
    return st["count"] >= d["target"]


def resolve_metas(defs, states, now):
    """Méta-succès (all_of / any_of) : débloqués dès que leurs membres le sont, jusqu'à stabilité."""
    metas = [d for d in defs if d["op"] in META_OPS]
    changed = True
    while changed:
        changed = False
        for d in metas:
            st = states[d["id"]]
            if st["unlocked_at"]:
                continue
            n = sum(1 for m in d["members"] if states[m]["unlocked_at"])
            st["count"] = min(n, d["target"])
            if n >= d["target"]:
                st["unlocked_at"] = now
                changed = True


def run(defs, states, lines, now):
    """Rejoue `lines` sur tous les succès (`states` : {id: état}, modifié en place)."""
    def unlocked(i):
        return bool(states[i]["unlocked_at"])

    active = [d for d in defs if d["op"] not in META_OPS and not states[d["id"]]["unlocked_at"]]
    for line in lines:
        if not active:
            break
        fresh = False
        for d in active:
            st = states[d["id"]]
            feed(d, st, line, unlocked, now)
            fresh = fresh or bool(st["unlocked_at"])
        if fresh:
            active = [d for d in active if not states[d["id"]]["unlocked_at"]]
            resolve_metas(defs, states, now)
    resolve_metas(defs, states, now)
