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
assert(type(Core.new) == "table" or type(Core.new_logger) == "function",
       "main.lua doit aussi exposer le constructeur du logger (glue)")

local fails, checks = 0, 0

-- eq compare la liste d'évènements attendue à l'obtenue ; chaque ligne
-- produite doit en plus être ASCII imprimable (contrainte du dépôt).
local function eq(label, actual, expected)
  checks = checks + 1
  local function tostr(x)
    return type(x) == "table" and table.concat(x, "\n") or tostring(x or "")
  end
  local a, e = tostr(actual), tostr(expected)
  if a ~= e then
    fails = fails + 1
    io.stderr:write(("FAIL %s\n  attendu: %q\n  obtenu:  %q\n"):format(label, e, a))
    return
  end
  if type(actual) == "table" then
    for _, line in ipairs(actual) do
      checks = checks + 1
      if line:match("[^%g ]") then
        fails = fails + 1
        io.stderr:write(("FAIL %s : ligne non-ASCII imprimable : %q\n"):format(label, line))
      end
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

-- ---------- glue : logger avec dépendances injectées ----------

-- État complet « tout hooké », sans nouveauté : le premier tick ne doit émettre
-- que la ligne READY (puis l'éventuelle zone, jamais un instantané d'inventaire).
local function etat_complet()
  return {
    upgrades = { dream_breaker = true }, aspects = { clear_mind = 1 },
    attire = { basic_vest = true }, zone = "dungeon",
    keys_major = 3, keys_small = 2, health_pieces = 4, memento = true,
    trials = { dungeon = "gold" }, bosses = {}, died = 2, ending = false,
  }
end

local function nouveau_logger(collect, settle)
  local lignes = {}
  local log = Core.new_logger({
    collect = collect,
    settle = settle,
    append = function(line)
      checks = checks + 1
      if line:match("[^%g ]") then
        fails = fails + 1
        io.stderr:write(("FAIL glue : ligne non-ASCII imprimable : %q\n"):format(line))
      end
      lignes[#lignes + 1] = line
    end,
  })
  return log, lignes
end

-- 9. premier tick avec cibles : READY en première ligne, puis zone, et rien d'autre
local log, lignes = nouveau_logger(function() return etat_complet() end)
log.tick()
eq("glue premier tick", lignes,
   { "LUDODEX LOGGER READY: hooked=12 | missing=none", "ZONE ENTERED: dungeon" })

-- 10. tick sans cibles (jeu pas encore chargé) : aucune ligne
log, lignes = nouveau_logger(function() return nil end)
log.tick()
eq("glue tick sans cibles", lignes, {})

-- 11. deux ticks avec un upgrade ajouté entre-temps : UPGRADE ACQUIRED uniquement
local possede = false
log, lignes = nouveau_logger(function()
  local s = etat_complet()
  s.upgrades = { dream_breaker = true, sun_greaves = possede or nil }
  return s
end)
log.tick()
local n1 = #lignes
possede = true
log.tick()
eq("glue upgrade entre deux ticks", { lignes[n1 + 1] },
   { "UPGRADE ACQUIRED: sun_greaves" })
checks = checks + 1
if #lignes ~= n1 + 1 then
  fails = fails + 1
  io.stderr:write(("FAIL glue upgrade : %d lignes après tick 2, %d attendues\n")
                  :format(#lignes, n1 + 1))
end

-- 12. fenêtre de stabilisation (settle) : un changement de zone déclenche un
-- chargement qui ré-applique inventaire et essais — pendant la fenêtre,
-- seules les lignes de zone sortent ; le merge conserve la croissance, donc
-- une nouveauté après la fenêtre n'émet QUE la vraie nouveauté.
local phases12 = {
  { zone = "dungeon", upgrades = { dream_breaker = true } },
  { zone = "theatre", upgrades = { dream_breaker = true, sun_greaves = true } },
  { zone = "theatre", upgrades = { dream_breaker = true, sun_greaves = true,
                                   gold_leaf = true } },
  { zone = "theatre", upgrades = { dream_breaker = true, sun_greaves = true,
                                   gold_leaf = true, tower_key = true } },
}
local i12 = 0
log, lignes = nouveau_logger(function()
  i12 = i12 + 1
  return phases12[i12]
end, 2)
log.tick()
local r12 = #lignes
log.tick()
log.tick()
log.tick()
eq("settle : changement de zone d'abord", { lignes[r12 + 1] },
   { "ZONE ENTERED: theatre" })
eq("settle : nouveauté après fenêtre (les ticks 2-3 étaient pour l'inventaire)",
   { lignes[r12 + 2] }, { "UPGRADE ACQUIRED: tower_key" })
checks = checks + 1
if #lignes ~= r12 + 2 then
  fails = fails + 1
  io.stderr:write(("FAIL settle : %d lignes au total, %d attendues\n")
                  :format(#lignes, r12 + 2))
end

-- 13. état dégradé (mort : inventaire momentanément absent ou vidé) puis
-- restauré : aucune ré-émision ; une vraie nouveauté émet toujours.
local phases13 = {
  { zone = "dungeon", upgrades = { dream_breaker = true }, keys_major = 2 },
  { zone = "dungeon", upgrades = { dream_breaker = true }, keys_major = 2 },
  { zone = "dungeon", keys_major = 2 },                 -- upgrades absents
  { zone = "dungeon", upgrades = { dream_breaker = true }, keys_major = 2 },
  { zone = "dungeon", upgrades = {}, keys_major = 2 },  -- upgrades vidés
  { zone = "dungeon", upgrades = { dream_breaker = true, sun_greaves = true },
    keys_major = 2 },
}
local i13 = 0
log, lignes = nouveau_logger(function()
  i13 = i13 + 1
  return phases13[i13]
end)
for _ = 1, 6 do log.tick() end
eq("merge : dégradation sans ré-émision", lignes,
   { "LUDODEX LOGGER READY: hooked=3 | missing=aspects,attire,keys_small,health_pieces,memento,trials,bosses,died,ending",
     "ZONE ENTERED: dungeon", "UPGRADE ACQUIRED: sun_greaves" })

if fails > 0 then
  io.stderr:write(("%d/%d assertions en échec\n"):format(fails, checks))
  os.exit(1)
end
print(("OK : %d assertions"):format(checks))
