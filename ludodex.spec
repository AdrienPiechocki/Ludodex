# -*- mode: python ; coding: utf-8 -*-
"""ludodex.spec — build PyInstaller de l'application Ludodex (interface GTK4).

Sortie : un dossier autonome `build/ludodex/` contenant l'exécutable `ludodex`
(interface graphique) et `ludodex-cli` (ligne de commande), avec Python, GTK4,
libadwaita et les traductions embarqués.

Construction :

    python3 -m PyInstaller ludodex.spec --noconfirm \
        --distpath build --workpath .build-cache

Notes :
- `hooksconfig` force la version 4.0 pour `Gtk` : sans cela le hook Gtk de
  PyInstaller retombe sur 3.0 et ne collecte ni le typelib ni les bibliothèques
  GTK4 (l'application planterait au démarrage).
- `lang/` est copié tel quel ; `i18n.py` lit les traductions depuis
  `sys._MEIPASS/lang` une fois l'application gelée.
- `share/icons/` embarque le logo de l'application (`local.ludodex`) au layout
  hicolor : `ui/theme.py` le cherche déjà dans `sys._MEIPASS/share/icons`.
- `icon` (ludodex.ico) n'est actif que sous Windows : l'EXE porte alors le logo
  dans l'Explorateur et la barre des tâches ; Linux l'ignore.
"""

import sys

# Traductions de l'application (i18n.py les lit à côté du bundle) + logo.
datas = [
    ("lang", "lang"),
    ("share/icons", "share/icons"),
]

# Version de Gtk à collecter (cf. note en tête de fichier) et jeux d'icônes /
# thèmes restreints : par défaut le hook Gtk embarque TOUS les thèmes d'icônes
# et curseurs du système (~400 Mo). L'application n'a besoin que d'Adwaita (icônes
# symboliques) et de hicolor (repli).
hooksconfig = {
    "gi": {
        "module-versions": {
            "Gtk": "4.0",
        },
        "icons": ["Adwaita", "hicolor"],
        "themes": ["Default"],
    },
}

# --- Interface graphique -----------------------------------------------------
a_gui = Analysis(
    ["gui.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig=hooksconfig,
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz_gui = PYZ(a_gui.pure)

exe_gui = EXE(
    pyz_gui,
    a_gui.scripts,
    [],
    exclude_binaries=True,
    name="ludodex",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    icon="share/ludodex.ico" if sys.platform == "win32" else None,  # logo (Windows)
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# --- Ligne de commande -------------------------------------------------------
a_cli = Analysis(
    ["picker.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig=hooksconfig,
    runtime_hooks=[],
    excludes=["gi"],  # le CLI n'utilise pas GTK : on garde l'exécutable léger
    noarchive=False,
    optimize=0,
)
pyz_cli = PYZ(a_cli.pure)

exe_cli = EXE(
    pyz_cli,
    a_cli.scripts,
    [],
    exclude_binaries=True,
    name="ludodex-cli",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    icon="share/ludodex.ico" if sys.platform == "win32" else None,  # logo (Windows)
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# --- Assemblage --------------------------------------------------------------
# Un seul dossier pour les deux exécutables : les bibliothèques GTK4 (lourdes)
# sont partagées, le CLI ne coûte presque rien.
coll = COLLECT(
    exe_gui,
    exe_cli,
    a_gui.binaries,
    a_gui.datas,
    a_cli.binaries,
    a_cli.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="ludodex",
)
