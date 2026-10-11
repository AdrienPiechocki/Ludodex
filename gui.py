#!/usr/bin/env python3
"""gui.py — façade de compatibilité et entrée graphique.

L'interface GTK4/libadwaita est rangée dans le paquet `ui/` (theme, pickview,
library, retrospec, settingsui, main). Ce module réexpose `Win`/`App` pour que
`import gui` (suites de tests) continue de fonctionner, et `python3 gui.py`
lance l'application.
"""
import sys

from ui.main import Win, App
from ui.theme import STATUTS, DUREES, TILE_W, TILE_H, COVER_PX, CSS, _install_css, _css_done


if __name__ == "__main__":
    sys.exit(App().run(sys.argv))
