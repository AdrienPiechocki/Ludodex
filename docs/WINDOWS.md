# Ludodex on Windows

## Why "missing packages" can't be fixed with `pip install`

Ludodex has only **three** Python dependencies: `gi` (PyGObject), `cairo` (PyCairo)
and `yaml` (PyYAML).

- `PyYAML` installs from pip without any trouble;
- **`gi` and `cairo` have no Windows wheels on PyPI.** `pip install pygobject`
  can only compile from source, which requires the GTK 4, GLib and GObject
  Introspection libraries to be installed already. Without them the install
  fails — and an `import gi` fails, which is exactly the initial symptom.

GTK 4, libadwaita and PyGObject therefore have to come from **one** of these
two places. Ludodex is wired for the first one.

## Route A — MSYS2 (recommended)

GTK 4 + libadwaita + PyGObject + PyCairo + PyYAML + the Adwaita icons, all in
a single package suite, and the repo launchers are already ready.

1. Install [MSYS2](https://www.msys2.org/) (default location `C:\msys64`).
2. In the repo folder, run:

   ```bat
   setup-windows.bat
   ```

   This script calls `pacman` with the full package list
   (`mingw-w64-ucrt-x86_64-python`, `-python-gobject`, `-python-cairo`,
   `-python-yaml`, `-gtk4`, `-libadwaita`, `-adwaita-icon-theme`,
   `-hicolor-icon-theme`). You can also install them by hand from the
   MSYS2 terminal `C:\msys64\ucrt64.exe`.

3. Smoke-check it in one go:

   ```bat
   ludodex-cli.bat --help
   ludodex.bat
   ```

`ludodex.bat` and `ludodex-cli.bat` look for `ucrt64` and fall back to
`mingw64`: both MSYS2 environments work.

> **For the CLI alone** (no GUI), you only need `python` and
> `python-yaml`: `pacman -S --needed --noconfirm mingw-w64-ucrt-x86_64-python mingw-w64-ucrt-x86_64-python-yaml`

## Route B — standard Python + gvsbuild

To keep a python.org Python (useful if you develop other things with that
interpreter): the prebuilt GTK binaries from [gvsbuild](https://github.com/wingtk/gvsbuild)
come with their PyGObject/PyCairo wheels.

```powershell
# 1. GTK binaries + icon theme (unzip the latest release archive into C:\gtk)
$env:Path = "C:\gtk\bin;" + $env:Path

# 2. venv REQUIRED (gvsbuild wheels target the interpreter they were built for)
py -m venv .venv
.venv\Scripts\activate.ps1

# 3. PyGObject/PyCairo wheels shipped with the archive
pip install --force-reinstall (Resolve-Path C:\gtk\wheels\PyGObject*.whl)
pip install --force-reinstall (Resolve-Path C:\gtk\wheels\pycairo*.whl)

# 4. pip dependencies
pip install -r requirements.txt
python gui.py
```

Two classic traps on this route, fixed by the venv and by `--force-reinstall`:

- `error: Cannot uninstall pycairo … no RECORD file` → a pycairo is lying
  around in the system interpreter; the venv avoids it;
- `ModuleNotFoundError: No module named 'gi'` although the wheel is installed →
  the GTK DLLs are not on the PATH: `$env:Path = "C:\gtk\bin;" + $env:Path`.

## Standalone build (PyInstaller)

```bat
build.bat
```

produces `build\ludodex\` with `ludodex.exe` (GUI), `ludodex-cli.exe` (CLI)
and `_internal\` (embedded Python, GTK 4, libadwaita, icons and translations) —
the same content as `build.sh` on Linux. A bundle built on Linux contains
`.so` files: it is **useless** on Windows, and vice versa.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `ModuleNotFoundError: No module named 'gi'` | PyGObject missing from the interpreter being run | Route A: `setup-windows.bat`. Route B: the gvsbuild wheels, inside a venv |
| `ModuleNotFoundError: No module named 'yaml'` | PyYAML missing | Route A: `-python-yaml` ; route B: `pip install -r requirements.txt` |
| `DLL load failed` at startup | GTK DLLs not on the PATH | Route A: launch through `ludodex.bat` (it sets up PATH); route B: add `C:\gtk\bin` |
| `This application failed to start because no Qt…` / empty window | GTK can't find its icons | Install `adwaita-icon-theme` (route A) |
| Grey "image missing" squares instead of button icons (sort, filter, gear, tabs) | Without `XDG_DATA_DIRS` — a variable that doesn't exist on Windows — GTK can't find the symbolic theme that is actually installed | The app repairs itself at startup (`assurer_icones()`: adds the bundle and MSYS2 paths, then switches to Adwaita); otherwise install `adwaita-icon-theme` (route A) |
| `cmd` ignores a command from a `.bat` | LF line endings | `.gitattributes` forces `*.bat` to CRLF; regenerate with `git checkout -- *.bat` |
| The interface always starts in English | older versions | `i18n.detect()` now reads the Windows locale (`locale.getwindowslocale`) |

## What is already portable

`core/platform.py` concentrates **all** of the OS dependency and is tested
with injected parameters: `%LOCALAPPDATA%` paths, Steam and GOG registry,
`os.startfile` for URLs, `cmd /c start` for launch URIs (`steam://`,
`itch://`…), non-POSIX command splitting (`shlex` keeps backslashes),
`CREATE_NEW_PROCESS_GROUP`. Linux sources (Flatpak, Lutris) are neutralized:
their databases are simply not found on Windows, so no game is imported
from them.

All core file reads go through explicit UTF-8: game names come from
Steam/Heroic/Epic files in UTF-8, while the default Windows code page is
cp1252.
