"""config.py — réglages de l'overlay (JSON) et formats purs (sans GTK, testables).

Fichier : <dossier de données de Ludodex>/overlay.json. Chargement tolérant : un
fichier absent, illisible ou aux valeurs absurdes retombe sur les défauts, sans
jamais lever — l'overlay ne doit pas empêcher un jeu de se lancer."""
import copy, json, os, pathlib, tempfile

from core.platform import data_dir

WIDGETS = ("clock", "achievements", "notes")
CLOCK_MODES = ("clock", "session", "stopwatch", "countdown")

DEFAULTS = {
    "monitor": "",        # nom de sortie (ex. « DP-1 ») ; vide = celle que choisit le compositeur
    "dim": 0.35,          # assombrissement du fond quand l'overlay est ouvert
    "scale_with_game": True,    # l'overlay suit la taille de la fenêtre du jeu (échelle = hauteur / référence)
    "only_when_focused": True,  # affiché seulement quand la fenêtre du jeu a le focus (si le compositeur le dit)
    "reference_height": 1080,   # hauteur de fenêtre pour laquelle l'échelle vaut 1
    "widgets": {
        # x, y : position en FRACTION de l'espace libre (0 = bord gauche/haut, 1 = bord droit/bas),
        # donc indépendante de la résolution et toujours à l'écran.
        "clock": {"enabled": True, "pinned": True, "x": 0.98, "y": 0.02, "opacity": 0.85,
                  "mode": "clock", "hour24": True, "seconds": False, "countdown_min": 30,
                  "size": 28},
        "achievements": {"enabled": True, "pinned": True, "x": 0.5, "y": 0.04, "opacity": 0.95,
                         "duration": 6},
        "notes": {"enabled": True, "pinned": False, "x": 0.98, "y": 0.5, "opacity": 0.92,
                  "width": 380, "height": 320, "size": 13},
    },
}

LIMITS = {"opacity": (0.2, 1.0), "x": (0.0, 1.0), "y": (0.0, 1.0), "dim": (0.0, 0.8),
          "countdown_min": (1, 600), "size": (8, 120), "duration": (2, 30),
          "width": (200, 1200), "height": (120, 1000), "reference_height": (360, 4320)}


def config_path():
    return data_dir() / "overlay.json"


def _clean(key, value, default):
    """`value` ramené au type et aux bornes de `default` ; `default` si inutilisable."""
    if isinstance(default, bool):
        return value if isinstance(value, bool) else default
    if isinstance(default, (int, float)):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return default
        lo, hi = LIMITS.get(key, (value, value))
        value = min(max(value, lo), hi)
        return int(round(value)) if isinstance(default, int) else float(value)
    if isinstance(default, str):
        if not isinstance(value, str):
            return default
        return value if key != "mode" or value in CLOCK_MODES else default
    return default


def normalize(raw):
    """Configuration complète et valide à partir de n'importe quel JSON."""
    raw = raw if isinstance(raw, dict) else {}
    out = copy.deepcopy(DEFAULTS)
    for k in ("monitor", "dim", "scale_with_game", "only_when_focused", "reference_height"):
        out[k] = _clean(k, raw.get(k), DEFAULTS[k])
    given = raw.get("widgets") if isinstance(raw.get("widgets"), dict) else {}
    for name, defaults in DEFAULTS["widgets"].items():
        w = given.get(name) if isinstance(given.get(name), dict) else {}
        for k, d in defaults.items():
            out["widgets"][name][k] = _clean(k, w.get(k), d)
    return out


def load(path=None):
    try:
        with open(path or config_path(), encoding="utf-8") as f:
            return normalize(json.load(f))
    except (OSError, ValueError):
        return normalize(None)


def save(cfg, path=None):
    """Écriture atomique (un plantage en cours d'écriture ne tronque pas le fichier)."""
    path = pathlib.Path(path or config_path())
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".overlay-", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
        os.replace(tmp, path)
        return True
    except OSError:
        return False


# ---------- formats (sans locale : prévisibles, testables) ----------
def clock_text(now, hour24=True, seconds=False):
    """Heure de `now` (datetime) : « 14:05 », « 14:05:09 », « 2:05 PM »."""
    if hour24:
        return now.strftime("%H:%M:%S" if seconds else "%H:%M")
    h = now.hour % 12 or 12
    s = f"{h}:{now.minute:02d}" + (f":{now.second:02d}" if seconds else "")
    return s + (" PM" if now.hour >= 12 else " AM")


def duration_text(sec, seconds=True):
    """Durée en « h:mm:ss » (ou « mm:ss » sous l'heure) ; sans secondes : « h:mm »."""
    sec = max(0, int(sec))
    h, rest = divmod(sec, 3600)
    m, s = divmod(rest, 60)
    if not seconds:
        return f"{h}:{m:02d}"
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def place(frac_x, frac_y, area_w, area_h, w, h):
    """Coin haut-gauche (px) d'un widget w×h : la fraction répartit l'espace libre,
    donc le widget ne sort jamais de l'écran (même si l'écran change de taille)."""
    return (round(min(max(frac_x, 0), 1) * max(0, area_w - w)),
            round(min(max(frac_y, 0), 1) * max(0, area_h - h)))


def fraction(px, py, area_w, area_h, w, h):
    """Réciproque de `place` : fractions (bornées à 0..1) d'une position en pixels."""
    fx = px / (area_w - w) if area_w > w else 0.0
    fy = py / (area_h - h) if area_h > h else 0.0
    return min(max(fx, 0.0), 1.0), min(max(fy, 0.0), 1.0)
