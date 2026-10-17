"""i18n.py — traductions de l'addon (même schéma que celui de Ludodex).

La langue de base est l'anglais : les textes du code sont les clés. Chaque
langue est un fichier `lang/<code>.json` à côté de ce script ; une clé absente
s'affiche en anglais, jamais d'erreur. Les {champs} sont remplacés par
tr(..., champ=valeur).
"""
import json, os, pathlib

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
    """`fr_FR.UTF-8@euro` -> `fr` ; sert à normaliser les variables POSIX."""
    return raw.split(".")[0].split("@")[0].split("_")[0].lower()


def detect():
    """Langue du système si un fichier existe pour elle ; sinon la langue de base.

    Lue dans les variables POSIX (`LC_ALL`, `LC_MESSAGES`, `LANG`, `LANGUAGE`,
    par ordre de priorité)."""
    codes = {c for c, _ in available()}
    for var in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        for part in os.environ.get(var, "").split(":"):
            if _code(part) in codes:
                return _code(part)
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


set_lang(detect())
