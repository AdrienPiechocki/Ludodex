#!/usr/bin/env python3
"""platform.py — couche d'accès aux plateformes (chemins, lancement, commandes).

Chaque fonction accepte `env` / `home` injectés : les chemins se testent donc
depuis n'importe quelle machine. Le reste du code ne fait que consommer ces
fonctions. Ludodex est LINUX-ONLY : aucun registre, aucun `cmd start`, aucun
chemin Windows — les bibliothèques Epic et GOG viennent de Heroic.
"""
import os, pathlib, shlex, shutil, subprocess

# Nom du dossier de données de l'application ; `LEGACY_DIR_NAME` sert à reprendre
# automatiquement les données de l'ancien nom (Game Picker) une seule fois.
APP_DIR_NAME = "ludodex"
LEGACY_DIR_NAME = "gamepicker"


def _env(env):
    return os.environ if env is None else env


def _home(home):
    return pathlib.Path.home() if home is None else pathlib.Path(home)


def clean_path(value):
    """Normalise un chemin textuel venu d'un fichier de config : Steam écrit parfois
    les chemins avec des antislashs (échappés en `\\` dans les fichiers VDF). On les
    ramène à des « / », que `pathlib` comprend."""
    return pathlib.Path(str(value).replace("\\", "/"))


def _data_dir(name, env=None, home=None):
    """Dossier de données pour un nom donné."""
    return _home(home) / ".local" / "share" / name


def data_dir(env=None, home=None):
    """Dossier de données de l'application (base, couvertures, clé API)."""
    return _data_dir(APP_DIR_NAME, env, home)


def migrate_data_dir(env=None, home=None):
    """Reprend une seule fois le dossier de données historique (`gamepicker`) vers
    `ludodex`. Sans ancien dossier, ne fait rien ; si `ludodex` existe déjà, on ne
    touche à rien. Renvoie le dossier de données courant (non créé s'il n'y a rien
    à reprendre : l'ouverture de la base s'en charge)."""
    new = data_dir(env, home)
    if new.exists():
        return new
    old = _data_dir(LEGACY_DIR_NAME, env, home)
    if old.exists():
        new.parent.mkdir(parents=True, exist_ok=True)
        try:
            old.rename(new)          # même volume : déplacement atomique
        except OSError:
            shutil.copytree(old, new)  # repli : copie si le renommage échoue
    return new


def steam_roots(home=None):
    """Racines « steamapps » de Steam (jeux installés)."""
    home = _home(home)
    return [home / ".local/share/Steam/steamapps", home / ".steam/steam/steamapps"]


def itch_dbs(env=None, home=None):
    """Bases de l'application itch.io (jeux installés)."""
    home = _home(home)
    return [home / ".config/itch/db/butler.db",
            home / ".var/app/io.itch.itch/config/itch/db/butler.db"]


def lutris_dbs(env=None, home=None):
    """Bases Lutris (native, flatpak)."""
    home = _home(home)
    return [("native", home / ".local/share/lutris/pga.db"),
            ("flatpak", home / ".var/app/net.lutris.Lutris/data/lutris/pga.db")]


def flatpak_dirs(env=None, home=None):
    """Dossiers d'exports .desktop de Flatpak."""
    home = _home(home)
    return [pathlib.Path("/var/lib/flatpak/exports/share/applications"),
            home / ".local/share/flatpak/exports/share/applications"]


def retro_dirs(env=None, home=None):
    """Dossiers RetroArch : (drapeau flatpak, chemin)."""
    home = _home(home)
    return [(0, home / ".config/retroarch"),
            (1, home / ".var/app/org.libretro.RetroArch/config/retroarch")]


def heroic_dirs(env=None, home=None):
    """Dossiers de configuration de Heroic Games Launcher (natif + Flatpak)."""
    home = _home(home)
    return [home / ".config/heroic",
            home / ".var/app/com.heroicgameslauncher.hgl/config/heroic"]


def legendary_dirs(env=None, home=None):
    """Dossiers de config de Legendary, le moteur d'Epic de Heroic : ceux embarqués par
    Heroic + l'installation autonome."""
    home = _home(home)
    return [d / "legendaryConfig" / "legendary" for d in heroic_dirs(env, home)] + \
           [home / ".config/legendary"]


def uri_command(uri):
    """Commande ouvrant un URI (steam://, itch://) : le gestionnaire de schémas du bureau."""
    return ["xdg-open", uri]


def open_url_command(url):
    """Commande ouvrant une URL dans le navigateur."""
    return ["xdg-open", url]


def open_url(url):
    """Ouvre une URL dans le navigateur du bureau ; False si l'ouverture échoue."""
    try:
        subprocess.Popen(open_url_command(url), start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError:
        return False


def split_command(text):
    """Découpe une ligne de commande (mode POSIX : les guillemets protègent les
    espaces, les antislashs échappent)."""
    return shlex.split(text)


def join_command(argv):
    """Rassemble une ligne de commande pour l'affichage ; inverse de split_command."""
    return shlex.join(list(argv))
