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


def tags_summary(inclus, exclus):
    """Résumé des tags retenus, sous le titre « Tags » : « + Puzzle  − Roguelike ».

    Les tags sont toujours dépliés : ce résumé donne la portée du filtre d'un coup
    d'œil, là où il faudrait sinon parcourir la centaine de pastilles retenues par
    le catalogue pour savoir ce qu'elles excluent réellement.

    Renvoie du TEXTE PLAIN — c'est l'interface qui l'échappe, car le label du
    description interprète le BALISAGE GTK, et un « & » nu (tag « Action &
    Adventure ») invaliderait ce balisage. GTK ne signale rien dans ce cas : il
    affiche simplement plus rien du tout, ce qui passerait inaperçu.
    """
    inc = "  ".join("+ " + t for t in sorted(inclus, key=str.lower))
    exc = "  ".join("− " + t for t in sorted(exclus, key=str.lower))
    return "  ".join(x for x in (inc, exc) if x)


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


# ---------- carrousel (tirage animé) ----------
def roulette_strip(pool, winner_id, slots=45, rng=None, deja_visibles=()):
    """Bande de jaquettes du carrousel, composée à partir du BASSIN éligible `pool`.

    Le tirage pondérer a lieu AVANT l'animation : cette fonction ne fait que la mise
    en scène, en disposant les jaquettes pour que le gagnant `winner_id` atterrisse
    dans la case centrale après que tout le reste a défilé. Les jaquettes sont
    celles qui auraient pu sortir — jamais un décor emprunté ailleurs.

    Le gagnant y figure UNE seule fois, en `slots - 3` : il reste de la place pour
    des voisins visibles de part et d'autre une fois à l'arrêt. Les autres cases se
    cyclent en rebattant les cartes à chaque tour ; la longueur suit le bassin, au
    plus DEUX exemplaires par jeu — à bassin réduit, garder `slots` faisait revenir
    chaque jaquette en boucle (5 éligibles sur 45 cases, c'est 11 exemplaires de
    chacune : mesuré, et perçu comme des duplicatas). Plafonnée à `slots`, elle
    reste de 45 dès 23 jeux éligibles : le défilement des gros bassins n'a pas
    bougé. Pendant la rotation tout est flou et rapide : une jaquette vue deux fois
    ne s'y remarque pas.

    `deja_visibles` reçoit les ids déjà affichées en bout de bande. Un tirage
    n'ALLONGE la bande que — sans cette exclusion, le gagnant pourrait se trouver
    dans les jaquettes conservées, et `index_de` le lirait LÀ plutôt qu'à sa vraie
    place : la cible serait alors derrière le point de départ et l'animation
    partirait à rebours. Si le bassin est trop petit pour s'en passer, on dégrade
    en répétant plutôt qu'en vidant la bande.

    `rng` reçoit un `random.Random(graine)` pour rendre un défilement rejouable à
    l'identique (utile au débogage comme en test).
    """
    rng = rng or random
    jeux = [p[0] for p in pool]
    if not jeux:
        return []
    if len(jeux) == 1:  # un seul jeu possible : aucune bande n'a de sens
        return list(jeux)
    # La bande ne dépasse pas DEUX exemplaires par jeu. À bassin réduit, garder
    # `slots` faisait revenir chaque jaquette en boucle — 5 éligibles sur 45 cases,
    # soit 11 exemplaires chacune, mesuré et perçu comme des duplicatas. `2n − 1`
    # et non `2n` : le gagnant occupe une case à lui seul en plus du remplissage,
    # qui ne dispose plus que de `n − 1` autres jeux. Plafonnée à `slots`, la
    # longueur des gros bassins est inchangée — 38 jeux installés donnent 45 cases.
    slots = min(slots, 2 * len(jeux) - 1)
    gagnant = next((g for g in jeux if g["id"] == winner_id), jeux[0])
    autres = [g for g in jeux if g["id"] != gagnant["id"] and g["id"] not in deja_visibles]
    if not autres:  # bassin trop petit : on préfère répéter à garder une bande vide
        autres = [g for g in jeux if g["id"] != gagnant["id"]]
    rang = min(max(slots - 3, 0), slots - 1)
    bande, reste = [], None
    for i in range(slots):
        if i == rang:
            bande.append(gagnant)
            continue
        if not reste:  # donne épuisée : on rebat pour le tour suivant
            reste = autres[:]
            rng.shuffle(reste)
            # À la couture, la première carte de la nouvelle donne ne doit pas
            # être la dernière de l'ancienne : deux jaquettes identiques de suite
            # se liraient comme un ralentissement du défilement.
            if bande and len(reste) > 1 and reste[-1]["id"] == bande[-1]["id"]:
                reste[-1], reste[0] = reste[0], reste[-1]
        bande.append(reste.pop())
    return bande


def roulette_rogne(longueur, rang, a_gauche=4, a_droite=2):
    """Bornes de la coupe après un tirage : ne garder que ce qui sert encore.

    Sans cette coupe, chaque « Encore un » allongerait la bande d'une quarantaine
    de jaquettes : au bout de vingt tirages, plus de 900 jaquettes et ~160 Mo de
    textures. Les jaquettes gardées sont les MÊMES widgets, donc leurs textures
    survivent et la coupe reste invisible à l'écran.

    `a_gauche` jaquettes avant le gagnant suffisent pour le recadrer (4 × 182 px
    = 728 px de marge, pour un cadre d'environ 1 090 px), `a_droite` après lui
    pour l'accompagner. Renvoie `(premier, dernier, rang_du_gagnant_après)` ;
    le tranche `[premier:dernier]` convient donc tel quel.

    L'invariant à préserver côté UI : l'offset doit être diminué de
    `premier * pas`, sans quoi la bande sauterait au moment de la coupe.
    """
    premier = max(0, rang - a_gauche)
    dernier = min(longueur, rang + a_droite + 1)
    return premier, dernier, rang - premier


def roulette_centre(rang, largeur, pas, largeur_jaquette):
    """Offset qui place la case `rang` au centre d'un cadre de `largeur`.

    Peut être NÉGATIF : la case demandée tombe alors en deçà du début de la
    bande, laissant du vide à gauche — c'est même la seule façon de centrer la
    case 0 d'une bande plus courte que le cadre (un seul jeu éligible), un
    plafond à zéro y laissant la jaquette collée au bord, à 460 px du centre.

    `largeur` entre dans le calcul : la cible n'est donc pas une valeur figée,
    elle doit être RELUE à chaque changement de largeur du cadre — voir
    `roulette_delta_largeur`.
    """
    return rang * pas + largeur_jaquette / 2 - largeur / 2


def roulette_delta_largeur(largeur_avant, largeur_apres):
    """Décalage d'offset provoqué par un changement de largeur du cadre.

    L'offset centré vaut `K − largeur/2` (voir `roulette_centre`) : quand le
    cadre change de `Δ`, la cible bouge de `−Δ/2` et l'offset doit suivre AU
    PAS, sinon le gagnant dérive d'autant.

    Mesuré sur la vraie fenêtre — à l'arrêt, offset = cible = 7107 pour une
    largeur de 1244. Agrandie à 1664, la cible tombe à 6897 mais l'offset ne
    bougeait pas : le gagnant dérivait de 210 px, soit exactement Δ/2. C'était
    le défaut « quand je redimensionne, la roulette n'est plus centrée ».

    Le décalage vaut pour l'offset ET pour les bornes de l'animation en vol
    (`depart` et `cible`) : si elles ne suivent pas, le tick suivant remet
    l'offset à l'ancienne valeur et le gagnant atterrit hors centre.
    """
    return -(largeur_apres - largeur_avant) / 2


def tirer_candidats(pool, tires=frozenset(), courant=None):
    """Jeux tirables ce tour-ci : tout le `pool`, moins ceux déjà montrés.

    Le bassin ne se vide JAMAIS. Épuiser le réservoir — ce que fait la boucle du
    CLI `pick()`, qui n'affiche qu'un jeu à la fois — y coûte rien, mais ici le
    bassin sert aussi à composer la BANDE VISIBLE du carrousel : le vider faisait
    dégénérer celle-ci, mesuré sur le tag « voxel » (4 jeux éligibles) — les
    longueurs se succédaient en 7, 12, 10, 1, 6, 9 cases au fil des « Autre »,
    la bande ne présentait que 3 jeux distincts sur 4, et un réservoir tombé à 1
    déclenchait le raccourci « un seul jeu éligible » sur un filtre qui en
    laissait quatre. C'est `tires`, l'ensemble des jeux déjà montrés, qui
    s'épuise — jamais `pool`.

    Règles, dans l'ordre :
    1. on écarte les jeux déjà montrés ;
    2. s'ils le sont TOUS, on entame un nouveau tour — en n'écartant QUE le
       gagnant sortant, de sorte que les quatre passent en revue avant toute
       répétition ;
    3. sinon, si le réservoir compte plus d'un jeu, on écarte aussi le gagnant
       sortant, pour ne jamais remettre le même deux fois de suite.

    Un seul jeu éligible reste toujours tirable : aucune règle ne s'y applique,
    faute d'alternative.

    Renvoie `(candidats, tires)` — `tires` DOIT être repris au tour suivant : la
    règle 2 le réinitialise, et c'est ce qui entame le tour suivant. Le lire par
    valeur, jamais en le vidant côté appelant, sinon il ne se réinitialise jamais
    et la règle 2 ne joue qu'une fois.
    """
    tires = set(tires)
    reste = [p for p in pool if p[0]["id"] not in tires]
    if not reste:                       # tout est passé en revue : on repart,
        tires = {courant} if courant is not None else set()  # sans le répéter
        reste = [p for p in pool if p[0]["id"] not in tires]
    elif len(reste) > 1 and courant is not None:
        sans_courant = [p for p in reste if p[0]["id"] != courant]
        if sans_courant:                # le seul autre jeu EST le sortant
            reste = sans_courant
    if not reste:                       # un seul jeu éligible, déjà montré et
        reste = list(pool)              # sortant : impossible de l'écarter
    return reste, tires


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
