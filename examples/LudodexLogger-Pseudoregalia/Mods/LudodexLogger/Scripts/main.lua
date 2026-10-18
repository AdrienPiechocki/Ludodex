-- main.lua — LudodexLogger pour Pseudoregalia (mod UE4SS).
--
-- Écrit une ligne ASCII par changement d'état du joueur dans ludodex.log
-- (à côté de UE4SS.log, dans pseudoregalia/Binaries/Win64/), pour le module
-- Achievements de Ludodex. Contrat d'émission : transitions uniquement (jamais
-- d'instantané d'état), compteurs en valeurs absolues, jamais de ligne non
-- ASCII. Le glue UE4SS (plus bas) ne fait que collecter l'état et appeler
-- diff_events(); tout le cœur est pur, testé par tests/lua/test_logger.lua.

-- Totaux utilisés dans les lignes « n=<count>/<total> » (wiki du jeu).
local ASPECT_TOTALS = { clear_mind = 3, good_graces = 2, empathy = 2 }  -- défaut : 1
local KEY_TOTALS = { major = 5, small = 7 }
local HEALTH_PIECE_TOTAL = 16

local function aspect_total(id)
  return ASPECT_TOTALS[id] or 1
end

-- ---------- helpers de formatage (une fonction par type de ligne) ----------

local function fmt_upgrade(id)       return "UPGRADE ACQUIRED: " .. id end
local function fmt_aspect(id, n)     return ("ASPECT ACQUIRED: %s | n=%d/%d")
                                               :format(id, n, aspect_total(id)) end
local function fmt_attire(id)        return "ATTIRE UNLOCKED: " .. id end
local function fmt_zone(id)          return "ZONE ENTERED: " .. id end
local function fmt_key(kind, n)      return ("KEY ACQUIRED: type=%s | n=%d/%d")
                                               :format(kind, n, KEY_TOTALS[kind]) end
local function fmt_health(n)         return ("HEALTH PIECE: n=%d/%d")
                                               :format(n, HEALTH_PIECE_TOTAL) end
local function fmt_trial(zone, rank) return ("TIME TRIAL COMPLETE: %s | rank=%s")
                                               :format(zone, rank) end
local function fmt_boss(id)          return "BOSS DEFEATED: " .. id end

-- Clés d'une table, triées : itération déterministe quoi que soit l'ordre
-- imposé par la collecte UE4SS.
local function sorted_keys(t)
  local keys = {}
  for k in pairs(t) do keys[#keys + 1] = k end
  table.sort(keys)
  return keys
end

-- ---------- cœur : diff_events(prev, curr) -> {ligne, ...} ----------
--
-- Forme d'état (champs tous optionnels ; un champ nil vise « cible absente ce
-- tick » et n'émet JAMAIS, surtout pas « inventaire vidé ») :
--   { upgrades={id=true}, aspects={id=count}, attire={id=true}, zone="id",
--     keys_major=0, keys_small=0, health_pieces=0, memento=false,
--     trials={zone="bronze|silver|gold|done"}, bosses={id=true}, died=0,
--     ending=false }
-- Règles : émission sur augmentation uniquement (les baisses sont un reset de
-- sauvegarde) ; zone sur tout changement ; ending sur false→true ; le premier
-- rang observé d'un Time Trial gagne (améliorer le rang ne ré-émet pas).
local function diff_events(prev, curr)
  local out = {}
  prev, curr = prev or {}, curr or {}
  local function add(line) out[#out + 1] = line end

  -- ensembles (upgrades, attire, bosses) : nouvel id seulement
  local function diff_set(field, fmt)
    if curr[field] == nil then return end
    local seen = prev[field] or {}
    for _, id in ipairs(sorted_keys(curr[field])) do
      if not seen[id] then add(fmt(id)) end
    end
  end

  diff_set("upgrades", fmt_upgrade)
  diff_set("attire", fmt_attire)

  -- aspects : compteur par type, augmentation seulement
  if curr.aspects ~= nil then
    local seen = prev.aspects or {}
    for _, id in ipairs(sorted_keys(curr.aspects)) do
      local n, before = curr.aspects[id], seen[id] or 0
      if n > before then add(fmt_aspect(id, n)) end
    end
  end

  -- zone : tout changement est un évènement (y compris la première entrée)
  if curr.zone ~= nil and curr.zone ~= prev.zone then
    add(fmt_zone(curr.zone))
  end

  -- compteurs absolus (clés, fragments, morts) : augmentation seulement
  local function diff_counter(field, fmt)
    if curr[field] == nil then return end
    if curr[field] > (prev[field] or 0) then add(fmt(curr[field])) end
  end

  diff_counter("keys_major", function(n) return fmt_key("major", n) end)
  diff_counter("keys_small", function(n) return fmt_key("small", n) end)
  diff_counter("health_pieces", fmt_health)
  diff_counter("died", function() return "PLAYER DIED: x1" end)

  if curr.memento ~= nil and curr.memento and not prev.memento then
    add("MEMENTO FOUND: x1")
  end

  -- Time Trials : premier rang observé gagne
  if curr.trials ~= nil then
    local seen = prev.trials or {}
    for _, zone in ipairs(sorted_keys(curr.trials)) do
      if seen[zone] == nil then add(fmt_trial(zone, curr.trials[zone])) end
    end
  end

  diff_set("bosses", fmt_boss)

  if curr.ending ~= nil and curr.ending and not prev.ending then
    add("ENDING REACHED: x1")
  end

  return out
end

-- Sous Proton, UE4SS exécute ce chunk sans harnais : le glue de la Task 2
-- s'ajoutera ici. Le harnais de test récupère le cœur via la table retournée.
if LUDODEX_TEST then
  return { diff_events = diff_events }
end
