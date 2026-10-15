#!/usr/bin/env python3
"""sources.py — plateformes d'import (activation/désactivation, filtre SQL)."""
from .db import get_setting, set_setting

# ---------- plateformes à importer ----------
IMPORT_SOURCES = [("steam", "Steam"), ("itch", "Itch"), ("flatpak", "Flatpak"),
                  ("lutris", "Lutris"), ("retroarch", "RetroArch"),
                  ("heroic", "Heroic"), ("epic", "Epic Games")]
_IMPORT_KEYS = {src for src, _ in IMPORT_SOURCES}


def off_sources(con):
    """Clés des plateformes d'import désactivées (règle « import_off »)."""
    val = get_setting(con, "import_off")
    return {s.strip() for s in val.split(",") if s.strip()} if val else set()


def set_import_off(con, source, off):
    """Marque une plateforme comme (non) importée. Réversible : aucun jeu supprimé."""
    offs = off_sources(con)
    if off:
        offs.add(source)
    else:
        offs.discard(source)
    set_setting(con, "import_off", ",".join(sorted(offs)))


def source_enabled(con, source):
    """Vrai si la plateforme est encore importée (toutes le sont par défaut)."""
    return source not in off_sources(con)


def source_cond(con, alias=""):
    """Fragment SQL qui exclut les plateformes désactivées ; '' si tout est activé.
    Les clés sont filtrées sur une liste connue (_IMPORT_KEYS) : aucune injection."""
    offs = sorted(s for s in off_sources(con) if s in _IMPORT_KEYS)
    if not offs:
        return ""
    col = f"{alias}.source" if alias else "source"
    return f"{col} NOT IN ({', '.join(repr(s) for s in offs)})"
