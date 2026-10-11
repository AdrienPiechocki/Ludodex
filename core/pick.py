#!/usr/bin/env python3
"""pick.py — tirage pondéré d'un jeu selon l'humeur, le temps et les filtres."""
import math, random, time

from i18n import tr
from .platform import open_url
from .db import get_setting, set_setting
from .sources import source_cond
from .launch import launch
from .stats import status_label
from .game_collections import collection_game_ids

STATUS_WEIGHT = {"en_cours": 3.0, "backlog": 1.0, "pause": 0.5, "abandonne": 0, "termine": 0}


# ---------- tirage ----------
def excluded_tags(con):
    """Tags exclus du tirage (règle « exclude_tags »)."""
    val = get_setting(con, "exclude_tags")
    return {t.strip() for t in val.split(",") if t.strip()} if val else set()


def set_excluded_tags(con, tags):
    """Mémorise les tags à écarter du tirage (réversible : aucun jeu modifié)."""
    set_setting(con, "exclude_tags", ",".join(sorted({t.strip() for t in tags if t.strip()}, key=str.lower)))


def weight(g, minutes, mood, now, mood_mode="any", include_not_installed=False, exclude=None):
    if g["hidden"] or (not g["installed"] and not include_not_installed):
        return 0
    w = STATUS_WEIGHT.get(g["status"], 0)
    if w == 0:
        return 0
    if minutes is not None and g["session_length"] > minutes:
        return 0
    tags = {t.strip().lower() for t in (g["tags"] + "," + g["store_tags"]).split(",") if t.strip()}
    excluded = {t.strip().lower() for t in (exclude or ()) if t.strip()}
    if excluded & tags:  # un tag exclu écarte le jeu d'office (filtre dur)
        return 0
    moods = [mood] if isinstance(mood, str) else list(mood or [])
    if moods:  # les tags filtrent : « au moins un » (any) ou « tous » (all)
        hits = sum(1 for m in moods if m.lower() in tags)
        if hits == 0 or (mood_mode == "all" and hits < len(moods)):
            return 0
        w *= 2.5 ** hits
    days = (now - g["last_played"]) / 86400 if g["last_played"] else 365
    if g["status"] == "en_cours":
        w *= 1 + math.exp(-days / 14)          # jeu récent = on garde l'élan
    else:
        w *= 1 + math.log1p(min(days, 365)) / 3  # backlog délaissé = remonte doucement
    return w


def store_page(g):
    """URL de la fiche magasin d'un jeu ('' si aucune).

    Steam via l'appid, Flatpak via Flathub, sinon le `store_url` (lutris.net, itch.io).
    """
    if g["source"] == "steam" and g["ext_id"]:
        return f"https://store.steampowered.com/app/{g['ext_id']}/"
    if g["source"] == "flatpak" and g["ext_id"]:
        return f"https://flathub.org/apps/{g['ext_id']}"
    return g["store_url"] or ""


def pick_pool(con, minutes, mood, collection=None, mood_mode="any", exclude=None):
    """Bassin de jeux éligibles : (installés, possédés non installés).
    Exclut les plateformes d'import désactivées (données conservées)
    et tout jeu portant un tag de `exclude`."""
    if collection is None:
        allowed = None
    elif isinstance(collection, (set, list, tuple)):  # plusieurs collections : union de leurs jeux
        allowed = set()
        for cid in collection:
            allowed |= collection_game_ids(con, cid)
    else:
        allowed = collection_game_ids(con, collection)
    sql = "SELECT * FROM games"
    cond = source_cond(con)
    if cond:
        sql += " WHERE " + cond

    def build(include_not_installed):
        pool = [(g, weight(g, minutes, mood, time.time(), mood_mode, include_not_installed, exclude))
                for g in con.execute(sql)]
        return [(g, w) for g, w in pool if w > 0 and (allowed is None or g["id"] in allowed)]

    installed = build(False)
    owned = [(g, w) for g, w in build(True) if not g["installed"]]
    return installed, owned


def pick(con, minutes, mood, collection=None, mood_mode="any", exclude=None):
    if exclude is None:
        exclude = excluded_tags(con)
    installed, owned = pick_pool(con, minutes, mood, collection, mood_mode, exclude)
    if not installed and not owned:
        return print(tr("No eligible game (filters too strict, or library empty)."))
    stage = "installed" if installed else "owned"
    pool = list(installed) if stage == "installed" else list(owned)
    current = None
    while True:  # aucune limite : on boucle jusqu'a accord, sans jamais lancer d'office
        if not pool:  # reservoir epuise : installes passes -> on propose les possedes, sinon on recycle
            if stage == "installed" and owned:
                stage = "owned"
            pool = list(owned) if stage == "owned" else list(installed)
            if current and len(pool) > 1:  # eviter la repetition immediate
                pool = [p for p in pool if p[0]["id"] != current["id"]]
        g = random.choices([p[0] for p in pool], [p[1] for p in pool])[0]
        print(f"\n→ {g['name']}  [{status_label(g['status'])}]" + ("" if g["installed"] else "  " + tr("(not installed)")))
        if input(tr("Play? [Y/n] ")).strip().lower() in ("", "o", "y"):
            if g["installed"]:
                return launch(con, g)
            url = store_page(g)
            if url and open_url(url):
                return print(tr("Opened store page: {url}", url=url))
            return print(tr("No store page for this game."))
        current = g
        pool = [p for p in pool if p[0]["id"] != g["id"]]
