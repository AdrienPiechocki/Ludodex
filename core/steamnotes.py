#!/usr/bin/env python3
"""steamnotes.py — carnet de notes de l'overlay Steam.

Le bloc-notes de l'overlay (Shift+Tab) range ses notes par jeu dans
`userdata/<compte>/2371090/remote/notes_<appid>` : un JSON dont le contenu est
du BBCode, que le Steam Cloud propage entre les machines. Ludodex garde une
seule note Markdown par jeu — ce module sert de pont entre les deux mondes. Il
tient trois matières, dans l'ordre où elles s'empilent :

1. la conversion BBCode ↔ Markdown (les paires ci-dessous, rien de plus) ;
2. la lecture et l'écriture des fichiers Steam (écriture atomique) ;
3. la décision d'aller-retour (« le plus récent gagne »).

Les balises inconnues ou non fermées perdent leurs crochets, jamais leur
texte : une note reste lisible quelle que soit la saison de Steam.
"""
import json
import os
import pathlib
import random
import re
import time

from .db import get_setting, set_note
from .steam import STEAM_ROOTS, steam_appid_of

# Le carnet overlay de tous les jeux vit dans ce dossier distant du compte :
# chaque note est un fichier `notes_<appid>`, en JSON.
NOTES_REMOTE = "2371090/remote"

# Lien : `[url=U]T[/url]` <-> `[T](U)` ; `[url]U[/url]` -> `[U](U)`, le Markdown
# n'ayant pas d'autre ancre sans texte.
_URLEQ_RE = re.compile(r"\[url=([^\]]+)\](.*?)\[/url\]", re.S)
_URL_RE = re.compile(r"\[url\](.*?)\[/url\]", re.S)
_MDURL_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")

# Paires en ligne, cherchées des DEUX côtés : une balise seule ne devient jamais
# un marqueur orphelin (elle est retirée plus bas, texte gardé). Remplacements
# par lambda : un gabarit `\*` resterait littéral, barre oblique comprise.
_PAIRES_BB = ((r"\[b\](.*?)\[/b\]", lambda m: "**" + m.group(1) + "**"),
              (r"\[i\](.*?)\[/i\]", lambda m: "*" + m.group(1) + "*"),
              (r"\[u\](.*?)\[/u\]", lambda m: "__" + m.group(1) + "__"),
              (r"\[s\](.*?)\[/s\]", lambda m: "~~" + m.group(1) + "~~"),
              (r"\[strike\](.*?)\[/strike\]", lambda m: "~~" + m.group(1) + "~~"))
# `**` avant `*` : sinon le gras se couperait en deux italiques. Chaque marqueur
# Markdown devient une PAIRE ouvre/ferme du côté BBCode.
_PAIRES_MD = (("**", "[b]", "[/b]"), ("*", "[i]", "[/i]"), ("__", "[u]", "[/u]"),
              ("~~", "[s]", "[/s]"))

# Titres de l'overlay (trois niveaux, comme Steam) <-> `# `…`### `.
_HEADING_BB = re.compile(r"\[h([123])\](.*?)\[/h\1\]", re.S)
_HEADING_MD = re.compile(r"^(#{1,6}) +(.*)$")

_LIST_LINE = re.compile(r"^[ \t]*[-*+] +(.*)$", re.M)
_OLIST_LINE = re.compile(r"^[ \t]*\d+[.)] +(.*)$", re.M)
# Balise restante : nom sans espace (`[b]`, `[quote=x]`, `[/*]`, `[*]`) — un
# `[niveau 3]` saisi dans un texte n'est PAS une balise et garde ses crochets.
_TAG_RE = re.compile(r"\[/?(?:[a-z][a-z0-9]*|\*)(?:=[^\]]*)?\]")


def _liste_en_lignes(numerotee):
    """`[list]…[/list]` -> lignes Markdown, terminées par une ligne vide : un bloc
    qui suit la liste ne doit jamais s'y coller. L'overlay enveloppe ses items
    dans `[p]` et les ferme par `[/*]` — ces marques sont avalées ici, sinon
    elles casseraient la liste ou corrompraient le repoussoir."""
    def conv(m):
        items = []
        for i in m.group(1).split("[*]"):
            i = i.replace("[/*]", "").replace("[p]", " ").replace("[/p]", " ").strip()
            if i:
                items.append(i)
        if numerotee:
            corps = "\n".join(f"{n}. {i}" for n, i in enumerate(items, 1))
        else:
            corps = "\n".join("- " + i for i in items)
        return corps + "\n\n"
    return conv


def bbcode_en_markdown(content):
    """Contenu BBCode d'une note Steam, rendu en Markdown de base.

    Les crochets littéraux de Steam (`\\[ ]`, case à cocher) et les liens sont
    protégés par sentinelles pendant toutes les conversions : le marqueur
    `[texte](url)` généré ne doit pas repasser pour une balise inconnue par le
    nettoyage final — sinon un texte minuscule serait avalé.
    """
    s = content or ""
    s = s.replace("\\[", "\x02")   # crochet littéral de l'overlay (checkbox)
    # les liens d'abord, avant les paires en ligne dont le texte peut en contenir
    s = _URLEQ_RE.sub(lambda m: f"\x00{m.group(2)}\x01({m.group(1)})", s)
    s = _URL_RE.sub(lambda m: f"\x00{m.group(1)}\x01({m.group(1)})", s)
    s = _HEADING_BB.sub(lambda m: "#" * int(m.group(1)) + " " + m.group(2).strip() + "\n\n", s)
    s = re.sub(r"\[list\](.*?)\[/list\]", _liste_en_lignes(False), s, flags=re.S)
    s = re.sub(r"\[olist\](.*?)\[/olist\]", _liste_en_lignes(True), s, flags=re.S)
    for motif, remp in _PAIRES_BB:
        s = re.sub(motif, remp, s, flags=re.S)
    # `[p]a[/p][p]b[/p]` -> paragraphes ; un `[p][/p]` vide ne laisse rien
    s = s.replace("[p]", "").replace("[/p]", "\n\n")
    s = _TAG_RE.sub("", s)      # inconnues, mal fermées, [/*] orphelin : texte gardé
    s = s.replace("\x00", "[").replace("\x01", "]").replace("\x02", "[")
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def _echange(s, md, ouvre, ferme):
    """Marqueur Markdown (identique des deux côtés) -> paire BBCode ouvre/ferme :
    les parties impaires du découpage sont « dedans » la balise."""
    parts = s.split(md)
    res = parts[0]
    for i, p in enumerate(parts[1:], 1):
        res += (ouvre if i % 2 else ferme) + p
    return res


def _en_ligne(s):
    """Markdown en ligne -> BBCode en ligne (liens puis paires)."""
    s = _MDURL_RE.sub(lambda m: f"[url={m.group(2)}]{m.group(1)}[/url]", s)
    for md, ouvre, ferme in _PAIRES_MD:
        s = _echange(s, md, ouvre, ferme)
    return s


def _echappe_checkbox(item):
    """La case à cocher du Markdown (`- [ ]`) repart échappée `\\[ ]`, comme
    l'écrit l'overlay — sinon Steam l'interpréterait comme une extension de
    crochets au lieu d'une case."""
    if re.match(r"\[[ xX]\]", item):
        return "\\[" + item[1:]
    return item


def markdown_en_bbcode(texte):
    """Note Markdown ramenée au BBCode de l'overlay : blocs séparés par une
    ligne vide, chacun `[p]`, titre ou liste — l'aller-retour d'un texte de
    base est stable, octet pour octet."""
    s = (texte or "").strip()
    if not s:
        return ""
    sortie = []
    for bloc in re.split(r"\n\s*\n", s):
        lignes = bloc.split("\n")
        titre = _HEADING_MD.match(bloc) if len(lignes) == 1 else None
        if titre:  # Steam n'a que trois niveaux de titre
            n = min(len(titre.group(1)), 3)
            sortie.append(f"[h{n}]{titre.group(2)}[/h{n}]")
        elif all(_LIST_LINE.match(l) for l in lignes):
            items = [_echappe_checkbox(_LIST_LINE.match(l).group(1)) for l in lignes]
            sortie.append("[list]" + "[*]".join([""] + items) + "[/list]")
        elif all(_OLIST_LINE.match(l) for l in lignes):
            items = [_echappe_checkbox(_OLIST_LINE.match(l).group(1)) for l in lignes]
            sortie.append("[olist]" + "[*]".join([""] + items) + "[/olist]")
        else:
            sortie.append("[p]" + _en_ligne(bloc) + "[/p]")
    return "".join(sortie)


def titre_depuis_markdown(texte):
    """(titre, corps) : le premier `# ` de la note devient le champ `title` de
    la note overlay et LA LIGNE QUITTE le corps — c'est ce retrait qui rend le
    cycle stable (pousser deux fois ne change rien)."""
    if not texte:
        return "", ""
    m = re.match(r"# +(.*)\n?", texte)
    if not m:
        return "", texte
    return m.group(1).strip(), texte[m.end():].lstrip("\n")


def notes_steam_en_markdown(notes):
    """Toutes les notes overlay d'un jeu, fondues en un seul Markdown : chaque
    titre devient un titre `# `, les textes se suivent séparés d'une ligne
    vide. Une liste vide ne laisse rien."""
    morceaux = []
    for n in notes or []:
        titre = (n.get("title") or "").strip()
        corps = bbcode_en_markdown(n.get("content") or "").strip()
        if titre:
            morceaux.append("# " + titre + (f"\n\n{corps}" if corps else ""))
        elif corps:
            morceaux.append(corps)
    return "\n\n".join(morceaux)


# ---------- fichiers overlay ----------

def comptes_notes():
    """Dossiers `userdata/<id>` portant un carnet overlay (les plus récents d'abord)."""
    dossiers = []
    for root in STEAM_ROOTS:
        base = root.parent / "userdata"
        if not base.is_dir():
            continue
        for d in base.iterdir():
            if d.is_dir() and d.name.isdigit() and (d / NOTES_REMOTE).is_dir():
                dossiers.append(d)
    return sorted(dossiers, key=lambda d: (d / NOTES_REMOTE).stat().st_mtime, reverse=True)


def compte_notes():
    """Compte Steam dont le carnet est le plus récemment à jour, ou None."""
    l = comptes_notes()
    return l[0] if l else None


def fichier_notes(appid):
    """Fichier overlay d'une note de jeu (`…/remote/notes_<appid>`), ou None."""
    c = compte_notes()
    return (c / NOTES_REMOTE / f"notes_{appid}") if c else None


def lire_notes(appid):
    """Notes overlay d'un jeu : [] si le fichier n'existe pas, **None** si
    illisible — un JSON qu'on ne comprend pas n'est jamais écrasé. Une entrée
    inutilisable (pas un dict, sans `id`/`content` textuels) rend tout le
    fichier illisible : mieux vaut ne rien faire qu'écraser à moitié."""
    f = fichier_notes(appid)
    if f is None or not f.exists():
        return []
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
        notes = d["notes"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not isinstance(notes, list):
        return None
    for n in notes:
        if (not isinstance(n, dict) or not isinstance(n.get("id"), str)
                or not isinstance(n.get("content"), str)
                or not isinstance(n.get("title", ""), str)):
            return None
    return notes


def nouvel_id():
    """Identifiant court aléatoire, format des ids Steam (base36, 8 lettres)."""
    return "".join(random.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(8))


def ecrire_notes(appid, note_id, titre, contenu, quand):
    """Le carnet du jeu devient une SEULE note : les autres entrées sont
    remplacées — leur texte est déjà dans la fusion. L'histoire (`time_created`)
    de la note réutilisée survive ; `time_modified` porte l'invariant de synchro.
    Écriture atomique : jamais de JSON tronqué sous la main de Steam."""
    f = fichier_notes(appid)
    if f is None:
        return
    ancien = next((n for n in (lire_notes(appid) or []) if n.get("id") == note_id), None)
    note = {"id": note_id, "appid": appid, "ordinal": 0,
            "time_created": (ancien or {}).get("time_created", quand),
            "time_modified": quand, "title": titre, "content": contenu}
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f.name + ".tmp")
    tmp.write_text(json.dumps({"notes": [note]}, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, f)


# ---------- décision ----------

def resoudre(note_ludodex, t_ludodex, notes_steam):
    """Le arbitre d'aller-retour : ('importer', texte_steam, t_steam) /
    ('pousser', note_ludodex, 0) / ('rien', '', 0).

    Règles, dans l'ordre : un carnet illisible (None) n'est jamais écrasé ;
    un `[p][/p]` de placeholder compte comme un côté vide ; un côté vide cède à
    l'autre ; deux textes identiques ne bougent rien ; sinon le plus récent
    gagne — **égalité ou premier contact (t_ludodex nul) : Steam gagne**.
    """
    if notes_steam is None:
        return "rien", "", 0
    st = notes_steam_en_markdown(notes_steam).strip()
    ld = (note_ludodex or "").strip()
    if not st and not ld:
        return "rien", "", 0
    if not st:
        return "pousser", note_ludodex, 0
    if not ld:
        return "importer", st, max(n.get("time_modified") or 0 for n in notes_steam)
    if st == ld:
        return "rien", "", 0
    t_steam = max(n.get("time_modified") or 0 for n in notes_steam)
    if t_ludodex == 0 or t_steam >= t_ludodex:
        return "importer", st, t_steam
    return "pousser", note_ludodex, 0


def synchroniser(con, gid):
    """Le va-et-vient d'une note : importe la note overlay, pousse la note
    Ludodex, ou ne fait rien. Retourne l'action prise ('ignore' hors champ :
    réglage éteint, jeu non Steam ou carnet illisible). L'invariant qui rend
    l'opération stable : `time_modified` poussé = `notes_modified` de Ludodex —
    pousser puis rouvrir ne bouge donc rien."""
    if get_setting(con, "steam_notes_sync") == "0":
        return "ignore"
    g = con.execute("SELECT source, ext_id, notes, notes_modified FROM games WHERE id=?",
                    (gid,)).fetchone()
    appid = steam_appid_of(g)
    if g is None or appid is None or compte_notes() is None:
        return "ignore"
    notes = lire_notes(appid)
    if notes is None:
        return "ignore"
    action, texte, quand = resoudre(g["notes"], g["notes_modified"] or 0, notes)
    if action == "importer":
        set_note(con, gid, texte)
        # l'horodatage Steam est hérité tel quel : pousser plus tard ne réécrit pas
        con.execute("UPDATE games SET notes_modified=? WHERE id=?", (quand, gid))
        con.commit()
    elif action == "pousser":
        note_id = (notes[0]["id"] if notes else nouvel_id())
        titre, corps = titre_depuis_markdown(texte)
        quand = g["notes_modified"] or int(time.time())
        ecrire_notes(appid, note_id, titre, markdown_en_bbcode(corps), quand)
        if not g["notes_modified"]:  # ligne héritée à 0 : le poussage referme l'invariant
            con.execute("UPDATE games SET notes_modified=? WHERE id=?", (quand, gid))
            con.commit()
    return action
