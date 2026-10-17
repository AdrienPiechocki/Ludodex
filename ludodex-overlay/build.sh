#!/usr/bin/env sh
# build.sh — construit l'addon flatpak et le bundle build/ludodex-overlay.flatpak.
#
# Prérequis : flatpak-builder, flathub pour les dépendances de build
# (org.gnome.Sdk 51 ; gtk4-layer-shell est compilé depuis le manifeste).
set -eu
cd "$(dirname "$0")/.."
# Les .pyc locaux (pytest, py_compile) ne doivent pas partir dans l'appli :
find ludodex-overlay -name __pycache__ -type d -prune -exec rm -rf {} +
# --repo : sans lui, flatpak-builder n'exporte aucun dépôt et build-bundle échoue.
flatpak-builder --force-clean --user --install-deps-from=flathub \
    --repo=build/overlay-repo build/overlay-builder io.github.AdrienPiechocki.LudodexOverlay.yml
flatpak build-bundle build/overlay-repo build/ludodex-overlay.flatpak \
    io.github.AdrienPiechocki.LudodexOverlay
