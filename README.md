# Ludodex

A GTK4 / libadwaita game library manager that imports your games (Steam, itch.io, Flatpak, Lutris, RetroArch…), picks a **random game to play** based on your mood and the time available, and keeps a **retrospective** of your play sessions — all local, with a Markdown note per game.

## Features

### Pick — the draw
Selects a game to play right now, weighted by:
- **status** (`In progress` is strongly favored, `To play` comes next);
- **moods**: each mood is a tag (`-m calm -m short`);
- **time available** (`-t 20` for 20 minutes), compared to each game's useful session length;
- **excluded tags** (hard filter, reversible);
- **freshness**: a recently played game keeps its momentum, a neglected backlog slowly rises.

Launch the picked game directly, or launch from the library.

### Library
- imports installed games (Steam, itch.io, Flatpak, Lutris, RetroArch) and manual additions;
- statuses (`In progress`, `To play`, `Paused`, `Dropped`, `Finished`) with automatic pause after 28 days without a session;
- personal tags + store tags (Steam genres, itch.io keywords, Lutris genres), fetchable with the `tags` command;
- useful session length per game, SteamGridDB covers (optional API key, custom cover supported);
- manual and automatic collections (per platform), filters, hidden / not installed games.

### Retrospective
- **weekly**, **monthly** and **yearly** timelines (one column per day or per month), each independently toggleable in Settings;
- successive sessions of the same game are merged (≤ 5 min apart), plus a 0–15 min threshold to hide short sessions;
- duration shown for every session; day/month names follow the app's language.

### Notes
- one **Markdown note per game**, with a permanent live preview (clickable checkboxes, internal links, Markdown help);
- the line under the cursor shows the raw source, Obsidian-style;
- adjustable text size (8–40 pt), auto-save, "only noted games" filter.

### Play time tracking
Every launch through Ludodex opens a **session**; its duration is recorded when it closes. Steam history (minutes played, last launch) is imported locally with `backfill`, no API needed. Both the interface and the CLI show total play time, the timelines, and rankings.

## Installation

### Linux

Dependencies: Python 3, PyGObject, GTK 4, libadwaita and Cairo (Debian/Ubuntu: `python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1`).

```sh
git clone <repo-url> ludodex
cd ludodex
python3 gui.py          # graphical interface
python3 picker.py --help  # command line
```

### Windows

Built around [MSYS2](https://www.msys2.org/) (GTK4, libadwaita, PyGObject):
- `ludodex.bat` launches the interface (`pythonw.exe`);
- `ludodex-cli.bat` launches the command line.

### Standalone build (PyInstaller)

`./build.sh` produces `build/ludodex/` with `ludodex` (interface), `ludodex-cli` (CLI) and `_internal/` (embedded Python, GTK4, libadwaita and translations). See `ludodex.spec`; on Windows the `ludodex.bat` / `ludodex-cli.bat` launchers assume MSYS2.

## Usage

```
python3 picker.py <command> [options]
```

| Command | Effect |
|---|---|
| `import` | Imports games from all enabled platforms |
| `pick` | Draw: `-t` minutes, `-m` moods, `-c` collection(s), `-x` excluded tags, `--mood-mode any/all` |
| `set` | Status, tags, session length, cover (`--status`, `--tags`, `--min`, `--cover`, `--clear-cover`) |
| `add` | Game outside platforms (launch command) |
| `rename` | Rename (survives re-imports; `""` restores the original name) |
| `note` | Shows a note, or replaces it (`""` clears it) |
| `exe` | Shows/replaces the launch command (`""` restores the default) |
| `list` | Lists games |
| `lang` | Shows or changes the language (no argument: current language) |
| `stats` | Week + month + year retrospective, then ranking by play time |
| `backfill` | Imports Steam history (local sessions, no API) |
| `hide` / `unhide` | Hides or brings back a game |
| `col` | Collections: `list`, `add`, `rename`, `del`, `show`, `add-game`, `remove-game` |
| `tags` | Fetches store tags (`-n` limit, `--retry`) |
| `diag` | Game diagnostic (name, status, tags, launch…) |
| `covers` | SteamGridDB covers (`-n`, `--retry`, `--status`) |
| `cover` | Forces a game's SteamGridDB ID |

Examples:

```sh
python3 picker.py import
python3 picker.py pick -t 30 -m calm -c "To finish"
python3 picker.py stats
python3 picker.py note "Hollow Knight" "- [x] beat the final boss"
```

## Data

The local database is a SQLite file:
- Linux: `~/.local/share/ludodex/games.db`
- Windows: `%LOCALAPPDATA%\ludodex\games.db`

The data folder also holds the covers and the SteamGridDB API key. A legacy `gamepicker` folder is picked up automatically (once). Backup = copy `games.db`.

## Languages

The base language is English: code strings are the keys, each language is a `lang/<code>.json` file (see `lang/fr.json`). To add a language, drop in a `lang/<code>.json` — nothing else to change. The language is detected at startup and can be changed in Settings or via the `lang` command.

## Project structure

```
gui.py        graphical entry (re-exports Win/App, run python3 gui.py)
picker.py     facade: re-exports core/* (used by gui and the CLI) and the CLI entry
i18n.py       translation engine (tr, detection, set_lang)
core/         logic: db, platform, sources, importers, steam, tags, covers,
              launch, stats, pick, game_collections, cli
ui/           GTK4 interface: main, theme, pickview, library, retrospec,
              notes, markdown, settingsui
lang/         translations (en = base, fr)
build.sh      standalone PyInstaller build (ludodex.spec)
```

## License

MIT — © 2026 AdrienPiechocki.