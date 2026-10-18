#!/usr/bin/env lua5.4
-- tests/lua/test_logger.lua — harnais du cœur pur du logger Pseudoregalia.
--
-- Teste diff_events() hors du jeu, avec un état Lua bidon, via le même
-- main.lua que celui chargé par UE4SS (LUDODEX_TEST court-circuite le glue).
-- Lancer depuis la racine du dépôt : lua5.4 tests/lua/test_logger.lua
LUDODEX_TEST = true

local MOD = "examples/LudodexLogger-Pseudoregalia/Mods/LudodexLogger/Scripts/main.lua"
local chunk, err = loadfile(MOD)
assert(chunk, "impossible de charger " .. MOD .. " : " .. tostring(err))
local Core = chunk()
assert(type(Core) == "table" and type(Core.diff_events) == "function",
       "main.lua doit renvoyer { diff_events = fn } quand LUDODEX_TEST est défini")

local fails, checks = 0, 0

-- eq compare la liste d'évènements attendue à l'obtenue ; chaque ligne
-- produite doit en plus être ASCII imprimable (contrainte du dépôt).
local function eq(label, actual, expected)
  checks = checks + 1
  local a = type(actual) == "table" and table.concat(actual, "\n") or tostring(actual)
  local e = table.concat(expected or {}, "\n")
  if a ~= e then
    fails = fails + 1
    io.stderr:write(("FAIL %s\n  attendu: %q\n  obtenu:  %q\n"):format(label, e, a))
    return
  end
  for _, line in ipairs(actual or {}) do
    checks = checks + 1
    if line:match("[^%g ]") then
      fails = fails + 1
      io.stderr:write(("FAIL %s : ligne non-ASCII imprimable : %q\n"):format(label, line))
    end
  end
end

local D = Core.diff_events

-- 1. upgrade ajouté
eq("upgrade ajouté", D({}, { upgrades = { sun_greaves = true } }),
   { "UPGRADE ACQUIRED: sun_greaves" })

-- 2. zone : nil → castle_sansa émet ; zone inchangée n'émet rien
eq("zone première", D({}, { zone = "castle_sansa" }),
   { "ZONE ENTERED: castle_sansa" })
eq("zone inchangée", D({ zone = "castle_sansa" }, { zone = "castle_sansa" }), {})

-- 3. remise à zéro (nouvelle partie) : les baisses n'émettent rien
eq("reset sauvegarde",
   D({ keys_major = 5, upgrades = { sun_greaves = true } },
     { keys_major = 0, upgrades = {} }), {})

-- 4. aspect 1 → 2 (total par type depuis ASPECT_TOTALS : clear_mind = 3)
eq("aspect", D({ aspects = { clear_mind = 1 } }, { aspects = { clear_mind = 2 } }),
   { "ASPECT ACQUIRED: clear_mind | n=2/3" })

-- 5. clés et fragments de vie (compteurs absolus, ordre : clés puis fragments)
eq("cles et fragments",
   D({ keys_major = 2, health_pieces = 6 }, { keys_major = 3, health_pieces = 7 }),
   { "KEY ACQUIRED: type=major | n=3/5", "HEALTH PIECE: n=7/16" })

-- 6. Time Trial : premier rang observé émet ; améliorer le rang n'émet rien
eq("trial", D({}, { trials = { dungeon = "bronze" } }),
   { "TIME TRIAL COMPLETE: dungeon | rank=bronze" })
eq("trial rang amélioré", D({ trials = { dungeon = "bronze" } },
                            { trials = { dungeon = "gold" } }), {})

-- 7. mort, fin de jeu, memento
eq("mort", D({ died = 1 }, { died = 2 }), { "PLAYER DIED: x1" })
eq("fin", D({ ending = false }, { ending = true }), { "ENDING REACHED: x1" })
eq("memento", D({ memento = false }, { memento = true }), { "MEMENTO FOUND: x1" })

-- 8. cible absente ce tick (objet non chargé) : jamais traitée comme vidée
eq("cible absente", D({ upgrades = { sun_greaves = true } }, {}), {})
eq("cible absente (curr)", D({}, { upgrades = nil }), {})

if fails > 0 then
  io.stderr:write(("%d/%d assertions en échec\n"):format(fails, checks))
  os.exit(1)
end
print(("OK : %d assertions"):format(checks))
