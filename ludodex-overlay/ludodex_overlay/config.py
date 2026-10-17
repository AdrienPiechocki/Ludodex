"""config.py — configuration JSON globale de l'overlay.

Réglages globaux (tous jeux confondus) : écran, et par widget {actif, position
en fractions, transparence, épinglage, options}. Un fichier absent, illisible
ou partiel retombe toujours sur les défauts : jamais de plantage au démarrage.
"""
import json, os, pathlib, typing


def defauts() -> dict:
    """Schéma complet avec les valeurs d'usine."""
    return {
        "ecran": "auto",
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
    """Fusion récursive : les valeurs présentes dans `dessus` gagnent,
    les clés absentes de `dessus` conservent leur défaut."""
    for cle, valeur in dessus.items():
        if isinstance(valeur, dict) and isinstance(base.get(cle), dict):
            _fusionner(base[cle], valeur)
        else:
            base[cle] = valeur
    return base


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


def choix_sortie(regle: str, dispo: typing.List[str]) -> typing.Optional[str]:
    """Écran demandé : `auto` → None (le composant choisit), sinon le nom de
    la sortie si elle existe encore sur la machine, sinon None (retour auto)."""
    if regle == "auto" or regle not in dispo:
        return None
    return regle
