#!/usr/bin/env sh
# build.sh — construit l'application autonome dans build/.
#
# Produit build/ludodex/ avec :
#   - ludodex      : interface graphique (GTK4/libadwaita) ;
#   - ludodex-cli  : ligne de commande ;
#   - _internal/   : Python, GTK4, libadwaita, icônes et traductions embarqués.
#
# Prérequis : PyInstaller (`python3 -m pip install pyinstaller`), GTK4 et
# PyGObject installés sur le système.
#
# Les intermédiaires de compilation vont dans .build-cache/ (ignorable).
set -eu
cd "$(dirname "$0")"
exec python3 -m PyInstaller ludodex.spec --noconfirm \
    --distpath build --workpath .build-cache "$@"
