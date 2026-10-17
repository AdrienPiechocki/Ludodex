#!/usr/bin/env python3
"""steamnotes.py — carnet de notes de l'overlay Steam.

Le bloc-notes de l'overlay (Shift+Tab) range ses notes par jeu dans
`userdata/<compte>/2371090/remote/notes_<appid>` : un JSON dont le contenu est
du BBCode, que le Steam Cloud propage entre les machines. Ludodex garde un
MIROIR note à note de ce carnet (table `game_notes`, tous les jeux) : ce module
sert de pont entre les deux mondes. Il tient trois matières, dans l'ordre où
elles s'empilent :

1. la conversion BBCode ↔ Markdown (les paires ci-dessous, rien de plus) ;
2. la lecture et l'écriture des fichiers Steam (écriture atomique) ;
3. la synchronisation note à note (« le plus récent gagne », tombstones).

Les balises inconnues ou non fermées perdent leurs crochets, jamais leur
texte : une note reste lisible quelle que soit la saison de Steam.
"""
import json
import os
import pathlib
import random
import re
import time

from .db import (create_note, drop_note, get_setting, lier_steam_id, note_row,
                 notes_of_game, purge_tombstones, set_note_import, tombstoned_ids,
                 vanished_rows)
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


def ecrire_notes_multi(appid, notes):
    """Le carnet du jeu devient exactement `notes` — la liste complète, dans
    l'ordre du carnet. Écriture atomique du fichier entier (tmp + rename) :
    jamais de JSON tronqué sous la main de Steam."""
    f = fichier_notes(appid)
    if f is None:
        return
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f.name + ".tmp")
    tmp.write_text(json.dumps({"notes": notes}, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, f)


def synchroniser(con, gid):
    """Le va-et-vient note à note du carnet overlay : chaque note du fichier a
    sa ligne `game_notes`, chaque arbitrage oppose `time_modified` de la note à
    `modified` de la ligne — le plus récent gagne, égalité (ou premier contact)
    → Steam. Retourne l'action prise ('ignore' hors champ : réglage éteint, jeu
    non Steam, pas de compte ou fichier illisible — jamais écrasé).

    Règles d'appoint, toutes testées :
    - migration : une note locale dont le texte est EXACTEMENT la fusion du
      carnet en est le doublon → retirée (modifiée entre-temps, elle reste) ;
    - miroir : une note disparue du carnet est supprimée localement, mais
      JAMAIS si le fichier n'existe pas (absence ≠ carnet vidé) ;
    - placeholders vides (`[p][/p]`) : pas de ligne locale, mais préservés à
      la poussée — Ludodex ne supprime rien qu'il n'a pas créé ;
    - poussée : dicts existants mis à jour sur place (`ordinal`,
      `time_created`, clés inconnues intacts), notes locales appendues avec un
      nouvel id, tombes omises ; purgées UNIQUEMENT après écriture réussie.
    L'invariant qui rend l'opération stable : `time_modified` poussé =
    `modified` de la ligne — pousser puis rouvrir ne bouge donc rien."""
    if get_setting(con, "steam_notes_sync") == "0":
        return "ignore"
    g = con.execute("SELECT source, ext_id FROM games WHERE id=?", (gid,)).fetchone()
    if g is None:
        return "ignore"
    appid = steam_appid_of(g)
    if appid is None or compte_notes() is None:
        return "ignore"
    fichier = lire_notes(appid)
    if fichier is None:
        return "ignore"
    try:
        existe = fichier_notes(appid).exists()
    except OSError:
        existe = False

    importe = False

    # Dédup de migration (décision 1) : seule la ligne MIGRÉE, non retitrée et
    # non éditée, dont le texte est EXACTEMENT la fusion du carnet en est le
    # doublon — une note locale postérieure au même texte reste (revue I1).
    if fichier:
        fusion = notes_steam_en_markdown(fichier).strip()
        for ligne in con.execute(
                "SELECT id, title, content FROM game_notes "
                "WHERE game_id=? AND deleted=0 AND steam_id='' AND migrated=1", (gid,)):
            if not (ligne["title"] or "").strip() and ligne["content"].strip() == fusion:
                drop_note(con, ligne["id"])
                importe = True

    # Miroir (miroir, pas de reconstruction) : id disparu du fichier → ligne
    # supprimée. Un fichier absent n'a rien à supprimer.
    if existe:
        for rowid in vanished_rows(con, gid, [n["id"] for n in fichier]):
            drop_note(con, rowid)
            importe = True

    # Import : arbitrage par note, Steam gagne les égalités ; le placeholder
    # vide ne devient pas de ligne (les lignes ne servent qu'à ce qui se voit).
    for n in fichier:
        titre = (n.get("title") or "").strip()
        corps = bbcode_en_markdown(n["content"]).strip()
        if not titre and not corps:
            continue
        ligne = note_row(con, gid, n["id"])
        if ligne is None:
            create_note(con, gid, titre, corps, steam_id=n["id"],
                        modified=n.get("time_modified") or 0)
            importe = True
        elif (not ligne["deleted"] and (titre, corps) != (ligne["title"], ligne["content"])
              and (n.get("time_modified") or 0) >= ligne["modified"]):
            set_note_import(con, ligne["id"], titre, corps, n.get("time_modified") or 0)
            importe = True

    # Poussée : le carnet reconstruit garde l'ordre et l'histoire des entrées
    # existantes ; les lignes locales repartent avec un id neuf, les tombes
    # disparaissent, tout le reste (notes inconnues de Ludodex) est préservé.
    tombe = set(tombstoned_ids(con, gid))
    lignes = notes_of_game(con, gid)          # deleted=0 : les tombes n'y sont pas
    par_id = {l["steam_id"]: l for l in lignes if l["steam_id"]}
    sortie, a_lier, pousse = [], [], False
    for n in fichier:
        if n["id"] in tombe:
            pousse = True                     # à retirer du carnet
            continue
        ligne = par_id.get(n["id"])
        if ligne is None:
            sortie.append(n)                  # inconnue de Ludodex (placeholder) : préservée
            continue
        maj = dict(n)
        maj["title"] = ligne["title"]
        maj["content"] = markdown_en_bbcode(ligne["content"])
        maj["time_modified"] = ligne["modified"]
        if maj != n:
            pousse = True
        sortie.append(maj)
    if not existe:
        # Carnet absent : les lignes sync sont RECONSTRUITES (spec §Poussée :
        # locale + sync). Les sauter écrivait un carnet amputé, et le miroir de
        # la poussée suivante supprimait ces notes des DEUX côtés (revue C1).
        # L'histoire (ordinal/time_created) vivait dans le fichier parti : elle
        # repart à zéro, il n'y avait rien d'autre à conserver.
        for ligne in (l for l in lignes if l["steam_id"]):
            quand = ligne["modified"] or int(time.time())
            sortie.append({"id": ligne["steam_id"], "appid": appid,
                           "ordinal": len(sortie), "time_created": quand,
                           "time_modified": ligne["modified"],
                           "title": ligne["title"], "content": markdown_en_bbcode(ligne["content"])})
    for ligne in lignes:
        if ligne["steam_id"]:
            continue
        nid = nouvel_id()
        quand = ligne["modified"] or int(time.time())
        sortie.append({"id": nid, "appid": appid, "ordinal": len(sortie),
                       "time_created": quand, "time_modified": quand,
                       "title": ligne["title"], "content": markdown_en_bbcode(ligne["content"])})
        a_lier.append((ligne["id"], nid, quand))
        pousse = True
    if pousse:
        ecrire_notes_multi(appid, sortie)
        for rowid, nid, quand in a_lier:
            lier_steam_id(con, rowid, nid, quand)  # l'id n'est liquide qu'après écriture
        purge_tombstones(con, gid, [n["id"] for n in sortie])  # aucune tombe dans l'écrit
        return "pousser"
    # Sans poussée : oublier ce qui est tombé des DEUX côtés (carnet lu — un
    # carnet absent ne prouve rien et garde ses tombes en attente).
    purge_tombstones(con, gid, [n["id"] for n in fichier] if existe else None)
    return "importer" if importe else "rien"
