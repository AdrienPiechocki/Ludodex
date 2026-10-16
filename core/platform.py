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
        subprocess.Popen(host_argv(open_url_command(url)), start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError:
        return False


# Variables d'affichage et de locale qu'un processus HÔTE doit recevoir
# explicitement quand l'environnement transmis n'est pas celui de la session.
PASSE = ("DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "XDG_SESSION_TYPE",
         "LANG", "LC_ALL", "LC_MESSAGES")


def in_flatpak(env=None):
    """True si le code tourne dans un bac à sable Flatpak (`FLATPAK_ID` posé par `flatpak run`)."""
    return bool(_env(env).get("FLATPAK_ID"))


def host_argv(argv, env_vars=None, env=None):
    """Commande exécutant `argv` sur l'HÔTE — un bac à sable ne peut pas lancer les
    binaires hôtes (`steam`, `xdg-open`, `lutris`…) lui-même.

    Hors Flatpak : `argv` inchangé. Dedans : `flatpak-spawn --host` reçoit un `sh -c`
    qui dépouille d'abord l'environnement du sandbox — les `FLATPAK_*` de `flatpak run`,
    et surtout `XDG_DATA_HOME` / `XDG_CONFIG_HOME` / … redirigés vers `~/.var/app/…` :
    sans ce retrait, `xdg-open` lirait le mauvais `mimeapps.list` et les jeux
    rangeraient leurs sauvegardes chez Ludodex — puis ré-exporte l'affichage (`PASSE`)
    et les variables d'en-tête de la commande, et `exec` l'argv d'origine.
    `XDG_RUNTIME_DIR`, lui, reste : son chemin est le même des deux côtés."""
    if not in_flatpak(env):
        return list(argv)
    e = _env(env)
    avant = {k: e[k] for k in PASSE if k in e}
    exports = " ".join(f"export {k}={shlex.quote(str(v))};" for k, v in
                       {**avant, **(env_vars or {})}.items())
    script = ('for v in $(env | cut -d= -f1 | grep "^FLATPAK_"); do unset "$v"; done; '
              'unset XDG_DATA_HOME XDG_CONFIG_HOME XDG_CACHE_HOME XDG_DATA_DIRS XDG_CONFIG_DIRS; '
              + (exports + " " if exports else "") + 'exec "$@"')
    return ["flatpak-spawn", "--host", "sh", "-c", script, "sh", *argv]


# Scan des processus de l'HÔTE, exécuté par `sh -c` derrière flatpak-spawn.
# Les motifs arrivent par l'env LUDEX_MOTIFS et JAMAIS par l'argv (du scan comme
# de grep) : leur présence dans un cmdline serait vue par le scan lui-même, qui
# se détecterait alors toujours (le bug qui a failli laisser les sessions URI
# ouvertes à vie). Deux passes à coût constant : cmdline via grep (motifs sur
# stdin), puis cwd/exe via un seul ls + un seul grep (motifs en -e, inoffensifs
# ici : cette passe ne lit aucun cmdline). La casse est pliée des deux côtés,
# comme le scan natif.
_SCAN_SCRIPT = r'''
[ -n "$LUDEX_MOTIFS" ] || exit 0
mots="$LUDEX_MOTIFS"
oIFS=$IFS
IFS='
'
set +f
a=$(printf '%s\n' "$mots" | grep -Fal -i -f - /proc/[0-9]*/cmdline 2>/dev/null)
[ "$?" -gt 1 ] && exit 2
set -f
for f in $a; do
  p=${f#/proc/}
  echo "${p%/cmdline}"
done
set --
for m in $mots; do
  [ -n "$m" ] || continue
  set -- "$@" -e "$m"
done
set +f
b=$(ls -ld /proc/[0-9]*/cwd /proc/[0-9]*/exe 2>/dev/null | grep -Fi "$@" -)
[ "$?" -gt 1 ] && exit 2
set -f
for line in $b; do
  case $line in *" -> "*) ;; *) continue;; esac
  nm=${line% -> *}
  nm=${nm##* }
  case $nm in
    /proc/[0-9]*/*) p=${nm#/proc/}; p=${p%/*}; echo "$p";;
  esac
done
IFS=$oIFS
exit 0
'''


def host_pids_sous(motifs):
    """PIDs de l'HÔTE dont cmdline, cwd ou exe contiennent l'un des motifs.

    Dans le bac à sable, /proc ne montre que nos propres processus (noms de PID
    isolés) : le scan des processus de jeu est délégué à l'hôte, une passe `sh`
    par sonde (toutes les 2 s pendant l'attente). Un échec — hôte injoignable —
    renvoie la liste vide : le repli « jamais vu, donc rien n'est clôturé de
    façon inventée » s'applique alors.

    Précondition de `flatpak-spawn --host` : le processus appelant doit avoir un
    cwd existant côté hôte (sinon le portail échoue au chdir)."""
    if not motifs:
        return []
    # env -0 sur l'FD d'entrée (un enregistrement NAME=VALUE terminé par NUL ;
    # les motifs, eux, sont joints par saut de ligne) : aucun motif dans une
    # argv visible — le client flatpak-spawn est un processus du sandbox visible
    # du /proc hôte, son argv y serait un match permanent du scan sur lui-même.
    blob = ("LUDEX_MOTIFS=" + "\n".join(motifs) + "\0").encode()
    try:
        brut = subprocess.run(["flatpak-spawn", "--host", "--env-fd=0", "sh", "-c",
                               _SCAN_SCRIPT, "sh"],
                              input=blob, capture_output=True, timeout=15).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return sorted({int(x) for x in brut.decode("utf-8", "ignore").split()
                   if x.isdigit()})


def split_command(text):
    """Découpe une ligne de commande (mode POSIX : les guillemets protègent les
    espaces, les antislashs échappent)."""
    return shlex.split(text)


def join_command(argv):
    """Rassemble une ligne de commande pour l'affichage ; inverse de split_command."""
    return shlex.join(list(argv))
