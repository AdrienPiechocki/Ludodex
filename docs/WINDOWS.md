# Ludodex sous Windows

## Pourquoi « il manque les paquets » ne se règle pas par `pip install`

Ludodex n'a que **trois** dépendances Python : `gi` (PyGObject), `cairo` (PyCairo)
et `yaml` (PyYAML).

- `PyYAML` s'installe par pip, sans problème ;
- **`gi` et `cairo` n'ont aucun wheel Windows sur PyPI.** `pip install pygobject`
  ne peut que compiler depuis les sources, ce qui exige les bibliothèques GTK 4,
  GLib et GObject Introspection déjà installées. Sans elles, l'installation
  échoue — et un `import gi` échoue, ce qui est exactement le symptôme initial.

Il faut donc faire venir GTK 4, libadwaita et PyGObject d'**un** de ces deux
endroits. Ludodex est câblé pour le premier.

## Route A — MSYS2 (recommandée)

GTK 4 + libadwaita + PyGObject + PyCairo + PyYAML + les icônes Adwaita, tout
dans une seule suite de paquets, et les lanceurs du dépôt sont déjà prêts.

1. Installe [MSYS2](https://www.msys2.org/) (emplacement par défaut `C:\msys64`).
2. Dans le dossier du dépôt, lance :

   ```bat
   setup-windows.bat
   ```

   Ce script appelle `pacman` avec la liste complète des paquets
   (`mingw-w64-ucrt-x86_64-python`, `-python-gobject`, `-python-cairo`,
   `-python-yaml`, `-gtk4`, `-libadwaita`, `-adwaita-icon-theme`,
   `-hicolor-icon-theme`). Tu peux aussi les installer à la main depuis le
   terminal MSYS2 `C:\msys64\ucrt64.exe`.

3. Vérifie d'un trait :

   ```bat
   ludodex-cli.bat --help
   ludodex.bat
   ```

`ludodex.bat` et `ludodex-cli.bat` cherchent `ucrt64` puis retombent sur
`mingw64` : les deux environnements MSYS2 fonctionnent.

> **Pour la CLI seule** (sans interface graphique), il ne faut que `python` et
> `python-yaml` : `pacman -S --needed --noconfirm mingw-w64-ucrt-x86_64-python mingw-w64-ucrt-x86_64-python-yaml`

## Route B — Python standard + gvsbuild

Pour garder un Python de python.org (utile si tu develops ailleurs dans cet
interpréteur) : les binaires GTK précompilés de [gvsbuild](https://github.com/wingtk/gvsbuild)
viennent avec leurs wheels PyGObject/PyCairo.

```powershell
# 1. binaires GTK + thème d'icônes (dézippe l'archive de la dernière release dans C:\gtk)
$env:Path = "C:\gtk\bin;" + $env:Path

# 2. venv OBLIGATOIRE (les wheels gvsbuild ciblent l'interpréteur de compilation)
py -m venv .venv
.venv\Scripts\activate.ps1

# 3. wheels PyGObject/PyCairo livrés avec l'archive
pip install --force-reinstall (Resolve-Path C:\gtk\wheels\PyGObject*.whl)
pip install --force-reinstall (Resolve-Path C:\gtk\wheels\pycairo*.whl)

# 4. dépendances pip
pip install -r requirements.txt
python gui.py
```

Deux pièges classiques de cette route, résolus par le venv et par `--force-reinstall` :

- `error: Cannot uninstall pycairo … no RECORD file` → un pycairo traîne dans
  l'interpréteur système ; le venv l'évite ;
- `ModuleNotFoundError: No module named 'gi'` alors que le wheel est installé →
  les DLL GTK ne sont pas dans le PATH : `$env:Path = "C:\gtk\bin;" + $env:Path`.

## Build autonome (PyInstaller)

```bat
build.bat
```

produit `build\ludodex\` avec `ludodex.exe` (interface), `ludodex-cli.exe` (CLI)
et `_internal\` (Python, GTK 4, libadwaita, icônes et traductions embarqués) —
le même contenu que `build.sh` sous Linux. Un binaire construit sous Linux
contient des `.so` : il ne sert **rien** sous Windows, et inversement.

## Dépannage

| Symptôme | Cause | Solution |
|---|---|---|
| `ModuleNotFoundError: No module named 'gi'` | PyGObject absent de l'interpréteur lancé | Route A : `setup-windows.bat`. Route B : les wheels gvsbuild, dans un venv |
| `ModuleNotFoundError: No module named 'yaml'` | PyYAML absent | Route A : `-python-yaml` ; route B : `pip install -r requirements.txt` |
| `DLL load failed` au démarrage | DLL GTK hors PATH | Route A : lancer par `ludodex.bat` (il prépare le PATH) ; route B : ajouter `C:\gtk\bin` |
| `This application failed to start because no Qt…` / fenêtre vide | GTK ne trouve pas ses icônes | Installer `adwaita-icon-theme` (route A) |
| `cmd` ignore une commande du `.bat` | fin de ligne LF | `.gitattributes` force `*.bat` en CRLF ; regénère avec `git checkout -- *.bat` |
| L'interface démarre toujours en anglais | anciennes versions | `i18n.detect()` lit maintenant la locale Windows (`locale.getwindowslocale`) |

## Ce qui est déjà portable

`core/platform.py` concentre **toute** la dépendance à l'OS et se teste avec des
paramètres injectés : chemins `%LOCALAPPDATA%`, registre Steam et GOG,
`os.startfile` pour les URLs, `cmd /c start` pour les URI de lancement
(`steam://`, `itch://`…), découpage de commande non POSIX (`shlex` conserve les
antislashs), `CREATE_NEW_PROCESS_GROUP`. Les sources Linux (Flatpak, Lutris)
sont neutralisées : leurs bases de données sont simplement introuvables sous
Windows, donc aucun jeu n'en est importé.

Les lectures de fichiers du cœur passent toutes par UTF-8 explicite : les noms
de jeux viennent de fichiers Steam/Heroic/Epic en UTF-8, alors que la page de
codes Windows par défaut est cp1252.
