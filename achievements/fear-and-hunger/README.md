# LudodexLogger for Fear & Hunger

An RPG Maker MV plugin that writes a plain-text event log (`ludodex.log`) which
[Ludodex](https://github.com/AdrienPiechocki/Ludodex) reads to track
achievements in **Fear & Hunger**.

The plugin never reads any in-game text. It listens to the game engine and
writes **database IDs**, so the achievements work the same on the original game
and on translated versions (French patch included).

## Contents

| File | Purpose |
| --- | --- |
| `LudodexLogger.js` | The plugin |
| `fear-and-hunger-achievements.yaml` | Achievement definitions for Ludodex |

## Requirements

- Fear & Hunger (RPG Maker MV / NW.js version), with the translation patch
  already applied if you use one
- Ludodex with the Achievements module

## Installation

1. **Apply the translation patch first.** Patches often replace the whole `www`
   folder, which would overwrite `js/plugins.js` and remove the plugin.
2. Copy `LudodexLogger.js` into the game's plugin folder:

   ```
   <game folder>/www/js/plugins/LudodexLogger.js
   ```

3. Open `<game folder>/www/js/plugins.js` and add this line at the end of the
   list, just before the closing `];`:

   ```js
   {"name":"LudodexLogger","status":true,"description":"","parameters":{}},
   ```

   The file name and the `"name"` value must match exactly:
   `LudodexLogger`.

4. Launch the game once and reach the title screen. Two files appear next to
   `index.html` (usually in the `www` folder):

   - `ludodex.log`: the event log
   - `ludodex-dump.txt`: every ID and name in your installation's database

   If `ludodex.log` is missing, see [Troubleshooting](#troubleshooting).

## Connecting it to Ludodex

Attach the YAML and the log to the game:

```
python3 picker.py tracker attach "Fear & Hunger" fear-and-hunger-achievements.yaml /path/to/www/ludodex.log
python3 picker.py tracker status "Fear & Hunger"
```

You can also do it from the **Achievements** page of the interface: pick the
game on the left, then attach the YAML file and the log file. Ludodex re-reads
the log automatically while you play.

## What gets logged

Each line has the form `TAG: key=value ... | Name`. Only the part before the
`|` is meant for matching.

| Tag | Meaning |
| --- | --- |
| `SESSION START` | The game was launched |
| `NEW GAME` / `GAME SAVED` / `GAME LOADED` / `GAME OVER` | Game lifecycle |
| `BATTLE START` / `BATTLE WON` / `BATTLE LOST` / `BATTLE ESCAPED` | Combat, with `party=<size> troop=<id>` |
| `BATTLE FIRST WON` | Victory against a given troop (logged at every victory) |
| `ENEMY KILLED` | `enemy=<id>` |
| `ENEMY FIRST KILLED` | Kill of a given enemy type (logged at every kill) |
| `ENEMY DISCOVERED` | Encounter with an enemy (logged at every encounter) |
| `ACTOR DIED` | `actor=<id>` |
| `PARTY JOIN` / `PARTY LEAVE` | Party changes, with `actor=<id> map=<id>` |
| `ITEM ACQUIRED` | `item=<id>`, `weapon=<id>` or `armor=<id>`, with `qty=` and `map=<id>` |
| `ITEM DISCOVERED` | An item is obtained (logged at every pickup), with `map=<id>` |
| `ITEM EQUIPPED` | `actor=<id> weapon/armor=<id>` |
| `ITEM USED` / `SKILL USED` | Used by a party member, with `map=<id>` |
| `SKILL LEARNED` | A party member learned a skill, with `map=<id>` |
| `STATE ADDED` | A status effect was applied to a party member, with `map=<id>` |
| `LOCATION ENTERED` | `map=<id>`, every time |
| `LOCATION DISCOVERED` | Visit to a map (logged at every visit) |
| `TITLE RETURN` | `from=map/gameover/other map=<last map id>`: useful to detect endings |
| `SWITCH ON` / `SWITCH OFF` | Game switches (useful to detect endings and story flags) |
| `ENDING` | Emitted when the credits roll (ending reached): `map=<id> ending=<label> last_troop=<id> throne=0/1 girl=0/1 legarde=0/1 leader=<id>` |
| `VARIABLE SET` | Only for variables listed in the parameters |
| `COMMON EVENT` | Only if enabled in the parameters |

The plugin keeps no memory: every tag is logged at each occurrence, and the
log is emptied at every game launch. Nothing is logged before a real New Game
or Load from the title screen (the title menu itself is silent). Your
achievement progress lives in Ludodex's own database — Ludodex takes care of
it, not the plugin.

The intro maps (10 and 73, the character selection) grant the starting kit:
they emit party joins/leaves, skills, states and items like real gameplay
would. The tags above carry `map=<id>` so achievement matches can exclude
them, e.g. `'SKILL LEARNED: (?!.*map=(10|73) )'` or
`'PARTY JOIN: actor=3 (?!map=(10|73) )'`.

Swapping equipment is ignored, so unequipping an item does not count as
finding it.

## Plugin parameters

All parameters are optional. Defaults apply when they are missing. To change
one, edit the `"parameters"` object in `plugins.js`, for example:

```js
{"name":"LudodexLogger","status":true,"description":"","parameters":{"switchIds":"1-300","variableIds":"10,25-30"}},
```

| Parameter | Default | Description |
| --- | --- | --- |
| `logFile` | `ludodex.log` | Log path, relative to the folder of `index.html` or absolute |
| `dumpFile` | `ludodex-dump.txt` | Database dump, rewritten at every launch. Set to empty to disable |
| `logSwitches` | `true` | Log switch changes |
| `switchIds` | *(all)* | Restrict to IDs or ranges, e.g. `1-50,120` |
| `variableIds` | *(none)* | IDs or ranges of variables to log (`*` = all, noisy) |
| `logCommonEvents` | `false` | Log common event calls (noisy) |
| `creditsEventIds` | `310` | Common event IDs that mean "credits rolled": writes an `ENDING` line |
| `throneSwitchIds` | `1839` | Switch IDs that mean "sat on the Throne of Ascension" (noted `throne=1` in `ENDING`) |
| `endingTroops` | *(F&H)* | `id=label,...` final-battle troops tied to an ending (`130=a`, `126=c`...) |
| `endingMaps` | `86=e` | `id=label,...` ending-scene maps, used when the troop says nothing |
| `endingKeyActors` | `2=girl,6=legarde` | `id=label,...` actors whose presence in the party is noted in `ENDING` |

## Writing your own achievements

`ludodex-dump.txt` is tab-separated: `category`, `id`, `name` (the `troopmembers` category lists the enemy IDs of each troop, handy to find boss fights). For example,
to find a boss, search the `enemy` lines for its name, then match its ID:

```yaml
- name: Boss slayer
  description: Defeat the boss.
  match: 'ENEMY KILLED: enemy=42 '
```

The `id` field is optional: Ludodex derives one from the name (« Boss slayer »
becomes `boss-slayer`). Add an explicit `id` only if you want a stable key that
survives a name change.

Keep the trailing space after the ID so that `enemy=4` does not also match
`enemy=42`.

IDs can differ between the original game and a translation patch, so always
take them from the `ludodex-dump.txt` generated by **your** installation.

## Troubleshooting

**No `ludodex.log` appears.**
- Check that `plugins.js` still contains the `LudodexLogger` line (a game or
  patch update may have overwritten it) and that it has a trailing comma.
- Check that the file is named exactly `LudodexLogger.js`.
- Search for the log: `find <game folder> -name ludodex.log`.
- Run the game with the NW.js developer tools (F8, or F12 in test builds) and
  look for errors in the console.

**Running under Wine/Proton on Linux.** The log is written inside the game
folder, so point Ludodex at that folder's native Linux path. Avoid setting
`logFile` to a Windows-style absolute path.

**The log is empty or events are missing.** Some events only fire during real
gameplay (battles, map transfers). Start or load a game and play a little.

**Reset progress.** The log is emptied at every launch, so just use the reset
button in the Ludodex Achievements page.

## Uninstall

Remove the `LudodexLogger` line from `plugins.js` and delete
`LudodexLogger.js`, `ludodex.log` and `ludodex-dump.txt`.
