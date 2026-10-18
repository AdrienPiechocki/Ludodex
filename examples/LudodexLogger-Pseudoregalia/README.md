# LudodexLogger for Pseudoregalia

A UE4SS + Lua logger for *Pseudoregalia*. It watches the player pawn, the save
component and a few blueprint hooks, and appends one line per gameplay event to
`ludodex.log`. Ludodex's **Achievements** module then reads that log against
[`pseudoregalia-achievements.yaml`](../pseudoregalia-achievements.yaml)
(50 achievements: abilities, zones, bosses & ending, keys & health pieces,
outfits, time trials, deaths, 100 %).

One line = one self-contained event, appended, LF endings, ASCII only. The mod
emits **transitions only** (a new value emits, an improved one stays silent).

## Requirements

- **UE4SS** installed for *Pseudoregalia* — tested with **3.0.1 Beta #0** running
  under Proton (docs: <https://docs.ue4ss.com/>).
- Under Proton, add the Steam launch option `WINEDLLOVERRIDES="dwmapi=n,b"`.
  Without it, Proton loads its builtin `dwmapi` instead of the UE4SS proxy and
  the mod never starts (no error, no log).

## Install

1. Extract this archive anywhere.
2. Copy the `Mods/LudodexLogger` folder into `<game>/pseudoregalia/Binaries/Win64/ue4ss/Mods/`.
3. Add this line to `ue4ss/Mods/mods.txt`:
   ```
   LudodexLogger : 1
   ```

## First steps

1. Launch the game and enter a zone, then quit.
2. Check `<game>/pseudoregalia/Binaries/Win64/ludodex.log`. The first lines
   should be diagnostics, then events:
   ```
   LUDODEX BOOT: ...
   LUDODEX HOOK: ok|KO ...
   LUDODEX LOGGER READY: hooked=10 | missing=...
   ZONE ENTERED: ZONE_Dungeon
   UPGRADE ACQUIRED: attack
   ```
   `print()` is mute under Proton, so every diagnostic is written to the log
   itself. `missing=` lists the events your game version does not expose.
3. In Ludodex, attach `pseudoregalia-achievements.yaml` and this `ludodex.log`
   to the game's Achievements module. Each later session is read incrementally:
   playing the game unlocks achievements.

## Line formats

```
UPGRADE ACQUIRED: <id>                 powerup flags on the pawn (obtained*? lowercased)
ZONE ENTERED: <map>                    map package names (ZONE_Dungeon, Zone_Caves, ...)
ATTIRE UNLOCKED: <id>                  entries of save.unlockedOutfits
KEY ACQUIRED: type=major|small | n=<count>/<5|7>
HEALTH PIECE: n=<count>/16
TIME TRIAL COMPLETE: <map> | rank=bronze|silver|gold|done
BOSS DEFEATED: <map>                   a sealed-arena encounter deactivated after a kill
ENDING REACHED: x1
PLAYER DIED: x1
LUDODEX LOGGER READY: hooked=<n> | missing=<events absent from your game version>
```

The exact id tables (upgrades, maps, outfits) and their sources are documented
in the header of `pseudoregalia-achievements.yaml`.

## Limits

- **Transitions only**: one line per new value. A time trial's rank is the rank
  of its *first* completion; improving it later stays silent.
- The endgame families (time trials, outfits, health pieces, Solar Wind, the
  Princess fight, the ending) use line formats that are implemented and
  unit-tested but not yet seen in a real session — a first full playthrough
  confirms them.
- Aspects and Mementos are not covered: the game exposes no readable collection
  source for them.
- One save slot is read per tick (`currentSave`); the logger follows whichever
  slot the game has loaded.

## Source

This folder is the source of the mod; the zip next to it
(`LudodexLogger-Pseudoregalia.zip`) is generated from it:

```bash
cd examples && rm -f LudodexLogger-Pseudoregalia.zip && \
zip -r LudodexLogger-Pseudoregalia.zip LudodexLogger-Pseudoregalia -x "*.DS_Store"
```
