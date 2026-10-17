"""config.py — configuration JSON globale de l'overlay.

Réglages globaux (tous jeux confondus) : écran (auto), et par widget {actif,
position en fractions, transparence, épinglage, options}. Un fichier absent,
illisible ou partiel retombe toujours sur les défauts : jamais de plantage au
démarrage.

Note : l'écran et la libération de la souris ne sont plus réglés ICI. L'écran
suit le jeu (aucun `set_monitor` : le composant place la surface layer-shell là
où est le pointeur/focus), et la souris est toujours libérée à l'ouverture de
l'overlay — le réglage « Libérer la souris des jeux » de Ludodex a été retiré
(le 2026-10-18) : borné, il laissait le jeu re-capturer aussitôt."""
import json, os, pathlib, typing


def defauts() -> dict:
    """Schéma complet avec les valeurs d'usine."""
    return {
        "widgets": {
            "horloge": {
                "actif": True,
                "pos": {"x": 0.03, "y": 0.06},
                "transparence": 0.0,
                "epingle": False,
                "options": {"format_24h": True, "secondes": False},
            }
        },
    }


def _fusionner(base: dict, dessus: dict) -> dict:
    """Fusion récursive : les valeurs présentes dans `dessus` gagnent SI LEUR
    TYPE correspond au défaut ; clés absentes, mal typées (`null` à la place
    d'un objet, chaîne à la place d'un nombre…) ou inconnues du schéma
    conservent le défaut — un JSON édité à la main ne plante jamais."""
    for cle, valeur in dessus.items():
        defaut = base.get(cle)
        if isinstance(defaut, dict):
            if isinstance(valeur, dict):
                _fusionner(defaut, valeur)
        elif _type_conforme(defaut, valeur):
            base[cle] = valeur
    return base


def _type_conforme(defaut, valeur) -> bool:
    """Un remplacement n'est accepté que du type du défaut : booléen strict,
    nombre entier ou réel (jamais booléen), chaîne… et jamais pour une clé
    absente des défauts (defaut None = clé étrangère, ignorée)."""
    if defaut is None:
        return False
    if isinstance(defaut, bool):
        return isinstance(valeur, bool)
    if isinstance(defaut, (int, float)):
        return isinstance(valeur, (int, float)) and not isinstance(valeur, bool)
    return isinstance(valeur, type(defaut))


def charger(chemin: typing.Union[str, pathlib.Path]) -> dict:
    """Configuration du fichier, ou les défauts (fichier manquant, illisible,
    JSON cassé ou structure étrangère — une liste au sommet n'est pas une config)."""
    try:
        brut = json.loads(pathlib.Path(chemin).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return defauts()
    if not isinstance(brut, dict):
        return defauts()
    return _fusionner(defauts(), brut)


def sauver(chemin: typing.Union[str, pathlib.Path], donnees: dict) -> None:
    """Écriture atomique (fichier temporaire puis remplacement) : un plantage
    au milieu d'une écriture ne peut pas corrompre la configuration."""
    cible = pathlib.Path(chemin)
    cible.parent.mkdir(parents=True, exist_ok=True)
    tmp = cible.with_name(cible.name + ".tmp")
    tmp.write_text(json.dumps(donnees, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, cible)


def chemin_defaut(env: typing.Optional[dict] = None) -> pathlib.Path:
    """`$XDG_CONFIG_HOME/ludodex-overlay/config.json`, sinon `~/.config/…`.

    Dans le sandbox flatpak, XDG_CONFIG_HOME pointe déjà vers
    `~/.var/app/<id>/config` : la config vit alors avec l'addon."""
    environ = os.environ if env is None else env
    racine = environ.get("XDG_CONFIG_HOME") or str(pathlib.Path.home() / ".config")
    return pathlib.Path(racine) / "ludodex-overlay" / "config.json"


def epingle_actif(conf: dict) -> bool:
    """True si au moins un widget **actif** est épinglé (mode « pinned »).

    Un widget épinglé puis désactivé ne compte plus : sans quoi ✕ Fermer
    l'y aurait envoyé à vide et l'overlay ne se serait plus jamais fermé
    (bug du 2026-10-18, cause racine en test)."""
    return any(e.get("actif") and e.get("epingle")
               for e in conf.get("widgets", {}).values())
