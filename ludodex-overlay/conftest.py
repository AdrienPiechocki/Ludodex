"""Ajoute le dossier du paquet au chemin d'import.

`ludodex-overlay` contient un tiret : il n'est pas importable depuis la racine
du dépôt. pytest charge automatiquement ce conftest avant les tests du dossier.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
