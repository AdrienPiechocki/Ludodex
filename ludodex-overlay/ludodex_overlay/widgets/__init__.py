"""widgets/__init__.py — registre des widgets disponibles de l'overlay.

Ajouter un widget = créer la classe dans son module, l'exporter ici et
l'ajouter à `REGISTRE` ; le panneau (tâche 7) et le popover (tâche 8) s'en
servent automatiquement via le `schema`.
"""
from .base import Widget
from .horloge import Horloge

REGISTRE: "dict[str, type[Widget]]" = {"horloge": Horloge}


def options_defauts(spec: "type[Widget]") -> dict:
    """Valeurs d'usine du `schema` : booléens à False, scales au milieu de
    leur plage. Les défauts *persistés* (config.defauts) peuvent différer :
    le chargement merge défauts-schema puis config sauvegardée."""
    d = {}
    for reglage in spec.schema:
        if reglage["type"] == "bool":
            d[reglage["cle"]] = False
        elif reglage["type"] == "scale":
            d[reglage["cle"]] = (reglage.get("min", 0.0) + reglage.get("max", 1.0)) / 2
    return d
