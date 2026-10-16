#!/usr/bin/env python3
"""tests/test_host.py — bac à sable Flatpak : détection et commande hôte."""
from core import platform

SB = {"FLATPAK_ID": "io.github.AdrienPiechocki.Ludodex",
      "XDG_DATA_HOME": "/home/u/.var/app/x/data",
      "WAYLAND_DISPLAY": "wayland-0", "XDG_RUNTIME_DIR": "/run/user/1000"}


def test_in_flatpak_reconnait_le_sandbox():
    assert platform.in_flatpak(env=SB)
    assert not platform.in_flatpak(env={})


def test_host_argv_hors_flatpak_sans_changement():
    assert platform.host_argv(["steam", "steam://rungameid/1"], env={}) == \
        ["steam", "steam://rungameid/1"]


def test_host_argv_dans_flatpak_passe_par_l_hote():
    argv = platform.host_argv(["xdg-open", "steam://run/1"], env=SB)
    assert argv[:5] == ["flatpak-spawn", "--host", "sh", "-c", argv[4]]
    assert argv[5] == "sh"
    assert argv[6:] == ["xdg-open", "steam://run/1"]
    script = argv[4]
    assert "FLATPAK_" in script                      # environnement du sandbox dépouillé
    assert "unset XDG_DATA_HOME XDG_CONFIG_HOME XDG_CACHE_HOME XDG_DATA_DIRS XDG_CONFIG_DIRS;" in script
    assert "XDG_RUNTIME_DIR" not in script.split("unset", 1)[1].split(";", 1)[0]  # jamais unset
    assert 'exec "$@"' in script


def test_host_argv_transmet_les_variables_personnalisees_et_l_affichage():
    script = platform.host_argv(["jeu"], env_vars={"MA_VAR": "a b"}, env=SB)[4]
    assert "export MA_VAR='a b';" in script          # shlex.quote
    assert "export WAYLAND_DISPLAY=wayland-0;" in script
    assert "export XDG_RUNTIME_DIR=/run/user/1000;" in script
