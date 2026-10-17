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

from .steam import STEAM_ROOTS

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
              (r"\[s\](.*?)\[/s\]", lambda m: "~~" + m.group(1) + "~~"))
# `**` avant `*` : sinon le gras se couperait en deux italiques. Chaque marqueur
# Markdown devient une PAIRE ouvre/ferme du côté BBCode.
_PAIRES_MD = (("**", "[b]", "[/b]"), ("*", "[i]", "[/i]"), ("__", "[u]", "[/u]"),
              ("~~", "[s]", "[/s]"))

_LIST_LINE = re.compile(r"^[ \t]*[-*+] +(.*)$", re.M)
_OLIST_LINE = re.compile(r"^[ \t]*\d+[.)] +(.*)$", re.M)
_TAG_RE = re.compile(r"\[/?[a-z][a-z0-9]*(?:=[^\]]*)?\]")


def bbcode_en_markdown(content):
    """Contenu BBCode d'une note Steam, rendu en Markdown de base.

    Les listes passent en premier (ce sont des blocs) ; les liens ensuite, avant
    les paires en ligne, dont le texte peut déjà contenir des liens convertis.
    """
    s = content or ""
    s = _URLEQ_RE.sub(lambda m: f"[{m.group(2)}]({m.group(1)})", s)
    s = _URL_RE.sub(lambda m: f"[{m.group(1)}]({m.group(1)})", s)
    s = re.sub(r"\[list\](.*?)\[/list\]",
               lambda m: "\n".join("- " + i for i in m.group(1).split("[*]") if i.strip()), s, flags=re.S)
    s = re.sub(r"\[olist\](.*?)\[/olist\]",
               lambda m: "\n".join(f"{n}. {i}" for n, i in
                                   enumerate((i for i in m.group(1).split("[*]") if i.strip()), 1)),
               s, flags=re.S)
    for motif, remp in _PAIRES_BB:
        s = re.sub(motif, remp, s, flags=re.S)
    # `[p]a[/p][p]b[/p]` -> paragraphes ; un `[p][/p]` vide ne laisse rien
    s = s.replace("[p]", "").replace("[/p]", "\n\n")
    s = _TAG_RE.sub("", s)          # inconnues, mal fermées : texte gardé
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


def markdown_en_bbcode(texte):
    """Note Markdown ramenée au BBCode de l'overlay : blocs séparés par une
    ligne vide, chacun `[p]` ou liste — l'aller-retour d'un texte de base est
    stable, octet pour octet."""
    s = (texte or "").strip()
    if not s:
        return ""
    sortie = []
    for bloc in re.split(r"\n\s*\n", s):
        lignes = bloc.split("\n")
        if all(_LIST_LINE.match(l) for l in lignes):
            items = [_LIST_LINE.match(l).group(1) for l in lignes]
            sortie.append("[list]" + "[*]".join([""] + items) + "[/list]")
        elif all(_OLIST_LINE.match(l) for l in lignes):
            items = [_OLIST_LINE.match(l).group(1) for l in lignes]
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


def _fichier_notes(appid):
    c = compte_notes()
    return (c / NOTES_REMOTE / f"notes_{appid}") if c else None


def lire_notes(appid):
    """Notes overlay d'un jeu : [] si le fichier n'existe pas, **None** si
    illisible — un JSON qu'on ne comprend pas n'est jamais écrasé."""
    f = _fichier_notes(appid)
    if f is None or not f.exists():
        return []
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
        notes = d["notes"]
        return notes if isinstance(notes, list) else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def nouvel_id():
    """Identifiant court aléatoire, format des ids Steam (base36, 8 lettres)."""
    return "".join(random.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(8))


def ecrire_notes(appid, note_id, titre, contenu, quand):
    """Le carnet du jeu devient une SEULE note : les autres entrées sont
    remplacées — leur texte est déjà dans la fusion. L'histoire (`time_created`)
    de la note réutilisée survive ; `time_modified` porte l'invariant de synchro.
    Écriture atomique : jamais de JSON tronqué sous la main de Steam."""
    f = _fichier_notes(appid)
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
