# Ludodex

A GTK4 / libadwaita game library manager that imports your games (Steam, itch.io, Flatpak, Lutris, RetroArch…), picks a **random game to play** based on your mood and the time available, and keeps a **retrospective** of your play sessions — all local, with a Markdown note per game and user-defined achievements tracked from game logs.

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

### Achievements
- bring your own achievements for **modded games**: pick a game on the left, then attach a **YAML file** and the game's **log file** to it;
- each achievement tests the **log lines** with regular expressions: `match` (one pattern), `any` (at least one), or `all` (every pattern), with an optional `count` (e.g. "defeat 50 bandits");
- the log is read **incrementally** (each line is counted once); a truncated or rewritten log is detected and re-scanned from the start;
- per-game panel with progress (`x/N`), unlock dates, a rescan button and a tracker dialog; a ready-to-adapt example lives in `examples/openmw-achievements.yaml`.

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
| `tracker` | Achievements from a game log: `list`, `attach <game> <yaml> <log>`, `status`, `scan`, `clear` |

Examples:

```sh
python3 picker.py import
python3 picker.py pick -t 30 -m calm -c "To finish"
python3 picker.py stats
python3 picker.py note "Hollow Knight" "- [x] beat the final boss"
python3 picker.py tracker attach "OpenMW" examples/openmw-achievements.yaml ~/.local/share/openmw/openmw.log
python3 picker.py tracker status "OpenMW"
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
              launch, stats, pick, game_collections, tracker, cli
ui/           GTK4 interface: main, theme, pickview, library, retrospec,
              notes, markdown, achievements, settingsui
lang/         translations (en = base, fr)
examples/     sample files (e.g. openmw-achievements.yaml)
build.sh      standalone PyInstaller build (ludodex.spec)
```

## License

MIT — © 2026 AdrienPiechocki.