#!/usr/bin/env python3
"""tests/test_settingsui.py — persistance des interrupteurs de réglage.

Bug du 2026-10-18 : « In-game overlay » revenait au défaut au redémarrage.
Cause : le handler lisait `row.active`, attribut non exposé en introspection
Python sur `Adw.SwitchRow` → AttributeError avalée par PyGObject →
l'écriture n'avait JAMAIS lieu. Le bon accès est `row.get_active()`.

On rejoue le bug de bout en bout : le VRAI handler de `SettingsMixin` est appelé
avec un `con` sqlite en mémoire et une ligne qui n'expose que `get_active()`
(comme `Adw.SwitchRow`). Si la clé n'est pas écrite en base, c'est le bug.
"""
import sqlite3

from ui import settingsui


class _Ligne:
    """Réplique d'une ligne de réglage : `Adw.SwitchRow` n'expose QUE
    `get_active()` en introspection Python — PAS l'attribut `active` (le bug
    d'origine : `row.active` levait une AttributeError avalée par PyGObject)."""

    def __init__(self, actif):
        self._actif = actif

    def get_active(self):
        return self._actif


def _settingsui(con):
    m = settingsui.SettingsMixin.__new__(settingsui.SettingsMixin)
    m.con = con
    return m


def _con():
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    con.commit()
    return con


def test_overlay_toggle_persiste_vraiment_en_base():
    """Éteindre l'interrupteur écrit « 0 », allumer réécrit « 1 ». Sans
    `get_active()`, `row.active` levait une AttributeError silencieuse et la
    clé n'était JAMAIS écrite (retour au défaut au redémarrage)."""
    con = _con()
    m = _settingsui(con)
    m._on_overlay_toggle(_Ligne(False), None)
    assert con.execute("SELECT value FROM settings WHERE key='overlay'").fetchone()[0] == "0"
    m._on_overlay_toggle(_Ligne(True), None)
    assert con.execute("SELECT value FROM settings WHERE key='overlay'").fetchone()[0] == "1"
