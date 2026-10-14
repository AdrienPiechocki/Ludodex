#!/usr/bin/env python3
"""picker.py — façade de compatibilité et entrée CLI.

La logique est rangée dans le paquet `core/` (db, launch, steam, sources, tags,
covers, stats, game_collections, importers, pick, cli, tracker, ra, games). Ce module
réexpose tous
les noms (y compris les helpers historiques) pour que `import picker as P`
(gui.py, suites de tests) continue de fonctionner à l'identique, y compris les
monkeypatches `P.<nom> = ...`.
"""
import sys
import types

from core import db as _mod_db
from core import launch as _mod_launch
from core import steam as _mod_steam
from core import sources as _mod_sources
from core import tags as _mod_tags
from core import covers as _mod_covers
from core import stats as _mod_stats
from core import game_collections as _mod_collections
from core import importers as _mod_importers
from core import pick as _mod_pick
from core import cli as _mod_cli
from core import tracker as _mod_tracker
from core import ra as _mod_ra
from core import games as _mod_games  # après covers : réexporte aussi `covers_dir`, sans écraser celui de covers

_OWNER_MODULES = (_mod_db, _mod_launch, _mod_steam, _mod_sources, _mod_tags, _mod_covers,
                  _mod_stats, _mod_collections, _mod_importers, _mod_pick, _mod_cli, _mod_tracker,
                  _mod_ra, _mod_games)

# Réexpose tous les noms de premier niveau (y compris les helpers _soulignés), comme
# le faisait le picker.py monolithique.
for _mod in _OWNER_MODULES:
    for _name, _value in vars(_mod).items():
        if not _name.startswith("__"):
            globals().setdefault(_name, _value)
del _mod, _name, _value


class _FacadeModule(types.ModuleType):
    """Répercute les affectations (ex. monkeypatch des tests) vers le module d'origine."""

    def __setattr__(self, name, value):
        for _owner in _OWNER_MODULES:
            if name in _owner.__dict__:
                setattr(_owner, name, value)
                break
        super().__setattr__(name, value)


sys.modules[__name__].__class__ = _FacadeModule


if __name__ == "__main__":
    _mod_cli.main()
