#!/usr/bin/env python3
"""i18n.py — traductions de l'application (à placer dans le même dossier que picker.py et gui.py).

La langue de base est l'anglais : les textes du code sont les clés. Chaque langue est un fichier
lang/<code>.json (à côté de ce script) qui associe une clé anglaise à sa traduction ; une clé absente
s'affiche en anglais, jamais d'erreur. Le champ "_name" donne le nom de la langue dans le sélecteur.
Pour ajouter une langue : déposer lang/<code>.json (par exemple lang/de.json), rien d'autre à modifier.
Les {champs} sont remplacés par tr(..., champ=valeur).
"""
import json, locale, os, pathlib

BASE = "en"
LANG_DIR = pathlib.Path(__file__).resolve().parent / "lang"
LANG = BASE
_cache = {}


def _load(code):
    """Table d'une langue ({} si le fichier manque ou est illisible)."""
    if code not in _cache:
        try:
            data = json.loads((LANG_DIR / f"{code}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        _cache[code] = data if isinstance(data, dict) else {}
    return _cache[code]


def available():
    """[(code, nom affiché)] des langues trouvées dans lang/ ; la langue de base y figure toujours."""
    codes = {BASE}
    if LANG_DIR.is_dir():
        codes |= {p.stem for p in LANG_DIR.glob("*.json")}
    return [(c, str(_load(c).get("_name") or c)) for c in sorted(codes, key=lambda c: (c != BASE, c))]


def _code(raw):
    """`fr_FR.UTF-8@euro` -> `fr` ; sert à la fois aux variables POSIX et à la locale Windows."""
    return raw.split(".")[0].split("@")[0].split("_")[0].lower()


def detect():
    """Langue du système si un fichier existe pour elle ; sinon la langue de base.

    Windows n'a ni `LANG` ni `LC_*` : on retombe sur la locale de l'utilisateur,
    que la bibliothèque standard sait lire (`locale.getwindowslocale`)."""
    codes = {c for c, _ in available()}
    for var in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        for part in os.environ.get(var, "").split(":"):
            if _code(part) in codes:
                return _code(part)
    if hasattr(locale, "getwindowslocale"):  # Windows : langue de l'interface utilisateur
        try:
            brut = locale.getwindowslocale()
        except (ValueError, locale.Error):   # locale exotique : jamais bloquant au démarrage
            brut = ""
        code = _code(brut or "")
        if code in codes:
            return code
    return BASE


def set_lang(code):
    global LANG
    LANG = code if code in dict(available()) else BASE


def get_lang():
    return LANG


def tr(text, **fields):
    out = _load(LANG).get(text)
    if not isinstance(out, str):
        out = text
    if not fields:
        return out
    try:
        return out.format(**fields)
    except (KeyError, IndexError, ValueError):  # traduction mal formée : on retombe sur le texte de base
        return text.format(**fields)


try:  # noms de jours/mois selon la locale système (le CLI n'est pas lancé par GTK)
    locale.setlocale(locale.LC_TIME, "")
except locale.Error:
    pass


set_lang(detect())
