#!/usr/bin/env python3
"""tests/test_pseudoregalia_yaml.py — le YAML des succès Pseudoregalia, rejoué
sur le fixture réel (tests/fixtures/pseudoregalia-log-sample.log) avec le moteur
vrai (core.tracker : load_tracker + scan, schéma match|any|all/count/hidden)."""
import pathlib
import sqlite3

import yaml

from core import db, tracker

RACINE = pathlib.Path(__file__).resolve().parent.parent
YAML = RACINE / "examples" / "pseudoregalia-achievements.yaml"
FIXTURE = RACINE / "tests" / "fixtures" / "pseudoregalia-log-sample.log"

# Jeu exact des succès que le fixture réel doit débloquer : une à quelques lignes
# par famille observée (zones, abilities, morts, clés, boss) — les familles
# provisoires (tenues, trials, fin) ne peuvent pas tomber sur 9 lignes.
DEBLOQUES_FIXTURE = {
    "first-steps", "dilapidated-dungeon", "castle-sansa",   # ZONE ENTERED ×2
    "dream-breaker", "slide", "map",                       # UPGRADE ×3
    "first-fall",                                          # PLAYER DIED
    "strong-eyes",                                         # BOSS DEFEATED
    "a-key",                                               # KEY ACQUIRED (small)
}

# Garde-fou contre une suppression accidentelle : chaque famille livrée, ses ids.
FAMILLES = {
    "abilities": {
        "dream-breaker", "slide", "sun-greaves", "indignation", "sunsetter",
        "strikebreak", "cling-gem", "ascendant-light", "soul-cutter",
        "solar-wind", "map", "full-move-set",
    },
    "zones": {
        "first-steps", "dilapidated-dungeon", "castle-sansa", "sansa-keep",
        "empty-bailey", "the-underbelly", "listless-library", "twilight-theatre",
        "tower-remains", "distorted-memory", "realm-walker",
    },
    "boss-fin": {"strong-eyes", "the-princess", "free-the-soul"},
    "secrets": {
        "a-key", "key-ring", "skeleton-key", "five-of-a-kind",
        "pip-collector", "heart-of-gold",
    },
    "tenues": {"fresh-threads", "wardrobe"},
    "trials": {
        "against-the-clock",
        "dilapidated-dungeon-trial", "castle-sansa-trial", "sansa-keep-trial", "empty-bailey-trial",
        "the-underbelly-trial", "listless-library-trial", "twilight-theatre-trial",
        "tower-remains-trial",
        "time-lord", "silver-streak", "golden-girl",
    },
    "morts": {"first-fall", "masochist"},
    "100%": {"castle-cleansed", "pseudoregalia-master"},
}


def test_yaml_charge():
    """Le YAML se charge sans exception : >= 50 définitions, toutes avec icône."""
    defs = tracker.load_tracker(YAML)
    assert len(defs) >= 50, f"{len(defs)} définitions seulement"
    sans_icone = [d["id"] for d in defs if not d["icon"]]
    assert not sans_icone, f"sans icône : {sans_icone}"


def test_rejeu_complet():
    """Le fixture réel débloque exactement la liste attendue (jeu du moteur vrai)."""
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(db.SCHEMA)
    tracker.set_tracker(con, 1, YAML, FIXTURE)
    rows = tracker.scan(con, 1)
    done = {r["id"] for r in rows if r["done"]}
    assert done == DEBLOQUES_FIXTURE


def test_pas_de_count_sur_etat():
    """count est réservé aux lignes PLAYER DIED (contrat du moteur)."""
    data = yaml.safe_load(YAML.read_text(encoding="utf-8"))
    for a in data["achievements"]:
        if "count" not in a:
            continue
        pats = [a.get("match", "")] + a.get("all", []) + a.get("any", [])
        assert any("PLAYER DIED" in p for p in pats), a["name"]


def test_familles_completes():
    """Présence des ids attendus par famille (pas de perte silencieuse)."""
    ids = {d["id"] for d in tracker.load_tracker(YAML)}
    for famille, attendus in FAMILLES.items():
        manquants = attendus - ids
        assert not manquants, f"{famille} : {sorted(manquants)}"


def test_succes_caches_marques():
    """Les éléments spoilants (boss, fin) sont hidden."""
    by_id = {d["id"]: d for d in tracker.load_tracker(YAML)}
    for ach_id in ("strong-eyes", "the-princess", "free-the-soul"):
        assert by_id[ach_id]["hidden"], ach_id
