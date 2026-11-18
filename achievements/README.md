# Community achievements

One folder per game: `achievements/<id>/` holds the YAML file, the logger mod that produces the
log (if the game needs one) and an optional `README.md`. `index.json` is **generated** — never
edit it by hand:

```sh
python3 tools/build_index.py          # validate every YAML and rewrite index.json
python3 tools/build_index.py --check  # what CI runs
python3 picker.py repo validate achievements/<id>/<id>-achievements.yaml   # one file, with warnings
```

Players use them with `python3 picker.py repo list | info <id> | install <id> [--mod] [--game <name> --log <file>] | update`,
or from the achievements page (*Set up a tracker → Community*). Files are checked against their
SHA-256 and parsed before being installed. `LUDODEX_REPO` points the app at another repository
(an `https://…/achievements/` URL or a local folder), e.g. a fork or a friend's collection.

## Adding a game

1. Create `achievements/<id>/<id>-achievements.yaml` (lowercase id, no spaces).
2. Start the file with the optional `game:` block, then the `achievements:` list.
3. Put the logger mod next to it; every file except `README.md` is listed as a downloadable extra.
4. Run `python3 tools/build_index.py`, commit the YAML **and** the regenerated `index.json`.

```yaml
game:
  name: "Voices of the Void"
  aliases: ["VotV"]          # matched against library names
  steam_appid: 1234          # optional; exact match for Steam games
  version: "1.0"
  language: en
  author: you
  mod: "LudodexLogger-VotV.zip"
  log: "where the log file lives"

achievements:
  - name: First contact
    description: Receive the first signal.
    match: 'SIGNAL RECEIVED'
```

## Rules

Each achievement has **one** main rule, plus optional modifiers. Patterns are Python regular
expressions searched line by line (a trailing space in a pattern is significant).

| Rule | Unlocks when… |
|---|---|
| `match: <regex>` | a line matches (with `count: N`: N lines match) |
| `any: [<regex>, …]` | a line matches at least one pattern (`count: N`: N such lines) |
| `all: [<regex>, …]` | every pattern has appeared somewhere in the log |
| `sequence: [<regex>, …]` | the patterns appear **in order** (a step only counts after the previous one) |
| `distinct: <regex>` + `count: N` | N **different** values were captured by the group (named group `v`, else group 1) |
| `sum: <regex>` + `count: N` | the numbers captured by the group add up to ≥ N (`1 234`, `1,234`, `1.234,5` are understood) |
| `max: <regex>` + `count: N` | a captured number reached ≥ N |
| `all_of: [<id>, …]` | all the listed achievements are unlocked (meta-achievement) |
| `any_of: [<id>, …]` + `count: N` | at least N (default 1) of the listed achievements are unlocked |

Modifiers (all rules on log lines): `exclude: <regex>|[…]` ignores matching lines,
`reset_on: <regex>|[…]` resets the progress ("without dying", "in a row"),
`requires: [<id>, …]` only counts once those achievements are unlocked, `case_insensitive: true`,
and `hidden: true` for secret achievements. An `id:` is optional (default: slug of the name) but
is required to be referenced by `requires` / `all_of` / `any_of` when the slug would be awkward.

```yaml
  - name: Explorer
    description: Visit 10 different towns.
    distinct: 'Entered town (?P<v>\w+)'
    count: 10
  - name: Millionaire
    description: Earn 1,000,000 gold in total.
    sum: 'Gold earned: ([\d ,.]+)'
    count: 1000000
  - name: Pacifist run, chapter 1
    description: Beat the first boss without dying.
    sequence: ['CHAPTER 1 START', 'BOSS 1 DEFEATED']
    reset_on: 'PLAYER DIED'
  - name: Completionist
    description: Unlock all three of the above.
    all_of: [explorer, millionaire, pacifist-run-chapter-1]
```
