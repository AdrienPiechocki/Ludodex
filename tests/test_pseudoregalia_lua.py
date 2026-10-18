#!/usr/bin/env python3
"""tests/test_pseudoregalia_lua.py — le cœur Lua du logger Pseudoregalia, testé hors jeu.

Le harnais tests/lua/test_logger.lua charge le même main.lua que celui
qu'UE4SS chargera en jeu (avec LUDODEX_TEST) et vérifie diff_events() sur des
états bidon : transitions uniquement, compteurs absolus, lignes ASCII.
"""
import pathlib
import subprocess

RACINE = pathlib.Path(__file__).resolve().parent.parent


def test_coeur_lua():
    """Le harnais lua5.4 passe intégralement."""
    r = subprocess.run(["lua5.4", "tests/lua/test_logger.lua"],
                       cwd=RACINE, capture_output=True, text=True)
    assert r.returncode == 0, f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}"
