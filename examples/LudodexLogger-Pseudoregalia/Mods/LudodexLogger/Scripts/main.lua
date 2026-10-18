-- main.lua — LudodexLogger pour Pseudoregalia (mod UE4SS).
--
-- Écrit une ligne ASCII par changement d'état du joueur dans ludodex.log
-- (à côté de UE4SS.log, dans pseudoregalia/Binaries/Win64/), pour le module
-- Achievements de Ludodex. Contrat d'émission : transitions uniquement (jamais
-- d'instantané d'état), compteurs en valeurs absolues, jamais de ligne non
-- ASCII. Le glue UE4SS (plus bas) ne fait que collecter l'état et appeler
-- diff_events(); tout le cœur est pur, testé par tests/lua/test_logger.lua.
--
-- Arbitrage des events (Task 3, règle d'acceptation du plan) :
--   · Provisoires — source collectée et prouvée lisible au dump, bascule
--     attendue au fil d'une vraie partie (jamais de farm de validation) :
--     HEALTH PIECE (hpPieces), ATTIRE UNLOCKED (unlockedOutfits),
--     TIME TRIAL COMPLETE (bestTime/medalTier), ENDING REACHED
--     (completedSave?). Leur format reste à confirmer sur la première
--     occurrence réelle.
--   · Abandonnés — aucune source lisible (re-scope acté) : ASPECT ACQUIRED,
--     MEMENTO FOUND — émetteurs retirés, READY ne les compte plus.
--     BOSS DEFEATED était le troisième « missing » : hook production validé
--     le 2026-10-18 (encounter activate → deactivate = victoire exclusive).

-- Totaux utilisés dans les lignes « n=<count>/<total> » (wiki du jeu).
local KEY_TOTALS = { major = 5, small = 7 }
local HEALTH_PIECE_TOTAL = 16

-- ---------- helpers de formatage (une fonction par type de ligne) ----------

local function fmt_upgrade(id)       return "UPGRADE ACQUIRED: " .. id end
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
--   { upgrades={id=true}, attire={id=true}, zone="id",
--     keys_major=0, keys_small=0, health_pieces=0,
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
-- s'ajoutera ci-dessous. Le harnais de test récupère le cœur via la table retournée.
local M = { diff_events = diff_events }

-- ---------- glue : logger à dépendances injectées ----------
--
-- deps.collect() -> état courant ou nil (aucune cible lisible ce tick) ;
-- deps.append(line) écrit une ligne. Champs du schéma d'état, pour le READY.
local STATE_FIELDS = { "upgrades", "attire", "zone", "keys_major",
                       "keys_small", "health_pieces", "trials",
                       "bosses", "died", "ending" }

local function ready_line(curr)
  local present, missing = 0, {}
  for _, f in ipairs(STATE_FIELDS) do
    if curr[f] ~= nil then
      present = present + 1
    else
      missing[#missing + 1] = f
    end
  end
  local manquants = #missing == 0 and "none" or table.concat(missing, ",")
  return ("LUDODEX LOGGER READY: hooked=%d | missing=%s"):format(present, manquants)
end

function M.new_logger(deps)
  local settle_n = deps.settle or 0
  local log, prev, started, settle_left = {}, nil, false, 0

  -- Mémoire d'état monotone : un champ nil (cible absente ce tick) garde sa
  -- dernière valeur connue ; ensembles en union, compteurs au maximum,
  -- essais et booléens collants. Un état dégradé (tick de mort où l'inventaire
  -- se vide) ne doit jamais reprogrammer une ré-émision au retour.
  local function merge_state(old, curr)
    local out = {}
    for k, v in pairs(curr) do out[k] = v end
    for k, v in pairs(old) do
      if out[k] == nil then out[k] = v end
    end
    local SETS = { "upgrades", "attire", "bosses" }
    for _, f in ipairs(SETS) do
      if out[f] ~= nil then
        local u = {}
        for k in pairs(old[f] or {}) do u[k] = true end
        for k in pairs(out[f]) do u[k] = true end
        out[f] = u
      end
    end
    if out.trials ~= nil then
      local t = {}
      for k, r in pairs(out.trials) do t[k] = r end
      for k in pairs(old.trials or {}) do          -- premier rang observé gagne
        if t[k] == nil then t[k] = old.trials[k] end
      end
      out.trials = t
    end
    for _, f in ipairs({ "keys_major", "keys_small", "health_pieces", "died" }) do
      if out[f] ~= nil then out[f] = math.max(old[f] or 0, out[f]) end
    end
    for _, f in ipairs({ "ending" }) do
      if out[f] ~= nil then out[f] = (old[f] == true) or out[f] end
    end
    return out
  end

  function log.tick()
    local curr = deps.collect()
    if curr == nil then return end          -- cibles absentes : prev inchangé
    if not started then
      -- Premier tick avec état : READY, puis la zone seule (arriver dans une
      -- zone est un évènement même au démarrage) ; tout le reste est baseline
      -- silencieuse — jamais d'instantané d'inventaire (contrat d'émission).
      -- La fenêtre de stabilisation s'ouvre aussi : ce tick vient d'un
      -- chargement dont l'inventaire se stabilise encore.
      started = true
      settle_left = settle_n
      deps.append(ready_line(curr))
      for _, line in ipairs(diff_events({}, curr)) do
        if line:find("^ZONE ENTERED:") then deps.append(line) end
      end
      prev = merge_state({}, curr)
      return
    end
    local merged = merge_state(prev, curr)
    local zone_changed = curr.zone ~= nil and curr.zone ~= prev.zone
    if zone_changed then settle_left = settle_n end
    local in_window = settle_left > 0
    if in_window then settle_left = settle_left - 1 end
    -- Fenêtre de stabilisation : après un changement de zone, le jeu
    -- ré-applique inventaire/essais sur plusieurs ticks — seules les lignes
    -- de zone sortent ; le merge a conservé la croissance pour la suite.
    for _, line in ipairs(diff_events(prev, merged)) do
      if not in_window or line:find("^ZONE ENTERED:") then deps.append(line) end
    end
    prev = merged
  end
  return log
end

if LUDODEX_TEST then return M end

-- ---------- glue UE4SS (uniquement sous Proton) ----------
--
-- Collecte de l'état : noms de classes/propriétés figés après les sessions de
-- dump du 2026-10-18. Tout est lu en pcall : une cible absente donne un champ
-- nil = « pas d'info ce tick », jamais un faux évènement (diff_events n'émet
-- jamais sur nil).

local function fmt_any(val)
  if type(val) ~= "userdata" then return tostring(val) end
  local ok3, s = pcall(function() return val:ToString() end)
  if ok3 and type(s) == "string" then return s end
  local ok3b, n = pcall(function() return val:GetFullName() end)  -- UObject
  if ok3b and type(n) == "string" then return n end
  local ok4, t = pcall(function() return val:type() end)
  if ok4 then return "<" .. tostring(t) .. ">" end
  return tostring(val)
end

-- Lecture d'une propriété : directe, ou :get() si UE4SS renvoie un wrapper.
local function read_val(obj, name)
  local ok, v = pcall(function() return obj[name] end)
  if not ok or v == nil then return nil end
  if type(v) ~= "userdata" then return v end
  local ok2, raw = pcall(function() return v:get() end)
  return ok2 and raw or v
end

-- booléens : le dump ne distingue pas true (booléen) de "true" (chaîne).
local function is_true(v) return v == true or v == "true" end
local function as_num(v) return tonumber(v) end

-- Piège UE4SS 3.0.1 : FindFirstOf sans correspondance ne retourne PAS nil
-- mais un objet vide (auto_construct_object(nullptr) « to enable chaining »,
-- LuaUObject.cpp — source officielle) ; tout appel de méthode dessus
-- (GetClass/GetFullName) déréférence un pointeur nul = crash natif que le
-- pcall ne voit jamais. Seul garde : :IsValid() — null-safe, simple test de
-- pointeur (LuaUObject.hpp). Les accès de propriété restent safe-chaining.
local function ref_ok(o) return o ~= nil and o:IsValid() end

local function player_pawn()
  local pc = FindFirstOf("PlayerController")
  if ref_ok(pc) then
    local okp, p = pcall(function() return pc.Pawn end)
    if okp and p ~= nil then return p end
  end
  return nil
end

-- Pouvoirs : les bools obtained*? du pawn (13 au dump, dont certains sont
-- peut-être des défauts de départ — la baseline du 1er tick les avale, seule
-- une apparition est émise ; arbitrage sur fixture en Task 5).
-- obtainedSlideJump? ajouté hors dump : wiki (Internal Name "SlideJump" pour
-- Solar Wind) + mod Archipelago (~solar → SlideJump) ; prop absente au dump →
-- si elle n'existe pas côté pawn, la sonde est simplement muette.
local OBTAINED_UPGRADES = {
  "obtainedAttack?", "obtainedAirKick?", "obtainedChargeAttack?",
  "obtainedGuard?", "obtainedJump?", "obtainedLight?", "obtainedMap?",
  "obtainedPlunge?", "obtainedPowerBoost?", "obtainedProjectile?",
  "obtainedSlide?", "obtainedSlideJump?", "obtainedSprint?",
  "obtainedWallRide?",
}

local RANK_BY_TIER = { [1] = "bronze", [2] = "silver", [3] = "gold" }

-- Mémoire de session partagée avec les hooks (appels Lua sérialisés par
-- UE4SS) : compteur de morts et drapeaux de boss vus en direct.
local deaths = 0
local hp_prev = nil
local boss_seen = {}
local enc_active = {}   -- rencontres armées ce session (activate → deactivate)

-- Cartes sans gameplay : l'écran-titre expose un aperçu d'état (sans save)
-- dont la baseline avalerait puis ré-émirait l'état réel au chargement.
local NO_GAMEPLAY_ZONES = { TitleScreen = true }

local function collect_state()
  local pawn = player_pawn()
  if pawn == nil then return nil end          -- pas en jeu : aucun état
  -- zone d'abord : hors carte ou écran-titre → aucun état, la baseline
  -- attend la première vraie zone (avec la save chargée).
  local okn, full = pcall(function() return pawn:GetFullName() end)
  local zone = okn and type(full) == "string"
               and full:match("/Game/Maps/([^%.]+)") or nil
  if zone == nil or NO_GAMEPLAY_ZONES[zone] then return nil end
  local gi = FindFirstOf("GameInstance")
  if not ref_ok(gi) then return nil end
  local oksave, save = pcall(function() return gi.currentSave end)
  local saveobj = oksave and save or nil

  local st = {}
  st.zone = zone

  local upgrades = {}
  for _, name in ipairs(OBTAINED_UPGRADES) do
    if is_true(read_val(pawn, name)) then
      upgrades[name:match("^obtained(.+)%?$"):lower()] = true
    end
  end
  st.upgrades = upgrades

  -- morts : le hook éventuel a déjà compté (hp_prev=0) ; sinon transition
  -- HP > 0 -> 0 sur le composant de vie du joueur (limite 1 Hz assumée,
  -- le hook de mort en double couverture sans double comptage).
  local okh, hp = pcall(function() return pawn.BP_HpHitable end)
  local cur = okh and hp ~= nil and as_num(read_val(hp, "CurrentHp")) or nil
  if cur ~= nil then
    if hp_prev ~= nil and hp_prev > 0 and cur <= 0 then deaths = deaths + 1 end
    hp_prev = cur
  end
  st.died = deaths

  if saveobj == nil then return st end         -- avant 1re sauvegarde
  st.health_pieces = as_num(read_val(saveobj, "hpPieces"))
  st.ending = is_true(read_val(saveobj, "completedSave?"))

  -- tenues : noms du tableau unlockedOutfits (FName ou chaîne)
  local outfits = {}
  local okarr, arr = pcall(function() return saveobj.unlockedOutfits end)
  if okarr and arr ~= nil then
    pcall(function()
      arr:ForEach(function(_, e)
        local okg, raw = pcall(function() return e:get() end)
        local nm = fmt_any(okg and raw or e)
        if nm ~= "" then outfits[nm] = true end
      end)
    end)
  end
  st.attire = outfits

  -- clés : 5 majeures (bools de save) + compteur de petites
  local major = 0
  for i = 1, 5 do
    if is_true(read_val(saveobj, "key" .. i .. "Save")) then major = major + 1 end
  end
  st.keys_major = major
  st.keys_small = as_num(read_val(saveobj, "NumberOfKeys"))

  -- Time Trials : instance par zone, rang via medalTier (tier 0 avec
  -- bestTime = fait sans médaille ; mappage complet à confirmer sur fixture)
  local trials = {}
  local okt, tlist = pcall(FindAllOf, "BP_TimeTrial_C")
  if okt and tlist ~= nil then
    for i, t in ipairs(tlist) do
      local best = as_num(read_val(t, "bestTime"))
      if best ~= nil and best > 0 then
        local tier = as_num(read_val(t, "medalTier")) or 0
        local okf, tfull = pcall(function() return t:GetFullName() end)
        local zmap = okf and tfull:match("/Game/Maps/([^%.]+)") or nil
        trials[zmap or ("trial" .. i)] = RANK_BY_TIER[tier] or "done"
      end
    end
  end
  st.trials = trials

  if next(boss_seen) ~= nil then st.bosses = boss_seen end
  return st
end

-- Déclaré avant les hooks de production qui en dépendent (upvalue) ; défini
-- plus bas, une fois LOG_PATH résolu (piège réel : appel avant définition a
-- fait crasher les ticks %30 en session du 2026-10-18).
local append_line

-- Mode dump (découverte) : scan des classes du jeu + propriétés des objets
-- clés, dans ludodex.log. Déclenché à l'entrée en zone, au changement de zone
-- puis toutes les 2 min — désactivé en production (le hook de mort s'y
-- référence aussi : déclaration avant les hooks).
local LUDODEX_DUMP = false
local DUMP_CLASSES = {
  "GameStateBase", "PlayerController", "PlayerState", "GameInstance",
  "Character",   -- le pawn du joueur
}

-- Hooks de production : uniquement là où aucun polling ne suffit (mort du
-- joueur en double couverture du HP, mort de boss — sans polling possible).
-- Un seul point d'entrée : le composant de vie, lu par le chemin de self
-- (la classe de boss ne charge qu'à l'approche → retry ci-dessous).
-- Nom complet de l'objet passé à un hook (self = wrapper UE4SS).
local function hook_name(self)
  local ok, o = pcall(function() return self:get() end)
  o = ok and o or self
  local okn, n = pcall(function() return o:GetFullName() end)
  return okn and type(n) == "string" and n or nil
end

local PROD_HOOKS = {
  { "/Game/Blueprints/BP_HpHitable.BP_HpHitable_C:BPI_CombatDeath",
    function(self)
      local n = hook_name(self)
      if n == nil then return end
      if n:find("BP_PlayerGoatMain_C", 1, true) then
        deaths = deaths + 1
        hp_prev = 0          -- désarme le comptage par sondage HP
      elseif LUDODEX_DUMP then
        -- découverte : toute mort non-joueur est tracée. Le boss lui-même
        -- n'a PAS de BP_HpHitable (validé 2026-10-18 : 0 HPDEATH sur kill,
        -- sa mort passe par l'encounter manager ci-dessous).
        append_line("DEBUG HPDEATH: " .. n)
      end
    end },
  -- Boss : l'arène scellée ne se désactive jamais à la mort du joueur
  -- (recharge silencieuse, validé 2026-10-18 : mort → 0 encDeact, kill →
  -- encDeact + KEY ACQUIRED la même seconde) → deactivate après activate =
  -- victoire. Id = zone du manager (une rencontre = un boss ; arbitrage
  -- définitif sur fixture Task 5).
  { "/Game/Blueprints/LevelActors/BP_EncounterManager.BP_EncounterManager_C:BPI_ActivateEvent",
    function(self)
      local n = hook_name(self)
      if n ~= nil then enc_active[n] = true end
    end },
  { "/Game/Blueprints/LevelActors/BP_EncounterManager.BP_EncounterManager_C:BPI_DeactivateEvent",
    function(self)
      local n = hook_name(self)
      if n ~= nil and enc_active[n] then
        enc_active[n] = nil
        local zone = n:match("/Game/Maps/([^%.]+)")
        boss_seen[zone or "unknown"] = true
      end
    end },
}

local function maybe_register_prod_hooks()
  for _, h in ipairs(PROD_HOOKS) do
    if not h.registered then
      local ok, err = pcall(function()
        RegisterHook(h[1], h[2])
      end)
      if ok then
        h.registered = true
        append_line("LUDODEX HOOK: ok")
      elseif not h.failed_once then
        -- en prod, un échec silencieux rendrait un succès impossible à prouver
        h.failed_once = true
        append_line("LUDODEX HOOK: KO " .. tostring(err):sub(1, 70))
      end
    end
  end
end

local LOG_PATH = nil

-- print() d'UE4SS alimente la console (souvent muette sous Proton) : le fichier
-- fait foi, le print reste en bonus. La première erreur seule est répétée.
local warned = false
local function warn(msg)
  if warned then return end
  warned = true
  print("[LudodexLogger] " .. msg .. "\n")
  if LOG_PATH ~= nil then
    -- écrit directement (append_line appellerait warn en boucle si io casse)
    local f = io.open(LOG_PATH, "a")
    if f ~= nil then
      f:write("LUDODEX WARN: " .. msg .. "\n")
      f:close()
    end
  end
end

append_line = function(line)
  if LOG_PATH == nil then return end
  local f, err = io.open(LOG_PATH, "a")
  if f == nil then
    warn("io.open KO sur " .. tostring(LOG_PATH) .. " : " .. tostring(err))
    return
  end
  f:write(line, "\n")
  f:close()
end

-- Valeur « lisible » d'une propriété d'un objet (chaîne de lectures possibles
-- en pcall : accès direct → :get() → fmt_any).
local function fmt_prop_value(obj, fname)
  local ok, v = pcall(function() return obj[fname] end)
  if not ok then return "?err" end
  if type(v) ~= "userdata" then return tostring(v) end
  local ok2, raw = pcall(function() return v:get() end)
  return fmt_any(ok2 and raw or v)
end

-- Éléments d'une propriété TArray : noms, objets ou structs, au choix.
local function dump_array(obj, fname, label)
  local oka, arr = pcall(function() return obj[fname] end)
  if not oka or arr == nil then
    append_line(("DEBUG ARR: %s | lecture KO"):format(label))
    return
  end
  local okn, n = pcall(function() return arr:GetArrayNum() end)
  if not okn then
    append_line(("DEBUG ARR: %s | %s (pas un TArray?)"):format(label, fmt_any(arr)))
    return
  end
  append_line(("DEBUG ARR: %s | %d élément(s)"):format(label, n))
  for i = 1, math.min(n, 24) do
    local okv, v = pcall(function()
      local e = arr[i]
      local okg, raw = pcall(function() return e:get() end)
      return okg and raw or e
    end)
    append_line(("DEBUG ARR:   [%d] = %s"):format(i, okv and fmt_any(v) or "?"))
  end
end

-- Schéma + valeurs d'un TArray de structs user-defined : noms de champs via
-- la classe UScriptStruct (ForEachProperty), valeurs via __index par nom.
local function dump_struct_array(obj, fname, label)
  local refl = obj:Reflection()
  local okp, prop = pcall(function() return refl:GetProperty(fname) end)
  if not okp or prop == nil then
    append_line(("DEBUG STRUCT: %s | propriété introuvable"):format(label))
    return
  end
  local oki, inner = pcall(function() return prop:GetInner() end)
  local okg, structc = false, nil
  if oki and inner ~= nil then
    okg, structc = pcall(function() return inner:GetStruct() end)
  end
  if not okg or structc == nil then
    append_line(("DEBUG STRUCT: %s | pas un tableau de structs"):format(label))
    return
  end
  local names = {}
  pcall(function()
    structc:ForEachProperty(function(p)
      names[#names + 1] = p:GetFName():ToString()
    end)
  end)
  append_line(("DEBUG STRUCT: %s | champs={%s}"):format(label, table.concat(names, ", ")))
  local oka, arr = pcall(function() return obj[fname] end)
  if not oka or arr == nil then return end
  pcall(function()
    arr:ForEach(function(i, elem)
      local oke, s = pcall(function() return elem:get() end)
      if not oke or s == nil then return end
      local parts = {}
      for _, n in ipairs(names) do
        local okv, v = pcall(function() return s[n] end)
        parts[#parts + 1] = n .. "=" .. (okv and fmt_any(v) or "?")
        -- champ tableau imbriqué (ex: jsutNames) : éléments lisibles
        if okv and v ~= nil then
          local okn2, num = pcall(function() return v:GetArrayNum() end)
          if okn2 then
            local items = {}
            pcall(function()
              v:ForEach(function(_, e)
                local oke2, raw2 = pcall(function() return e:get() end)
                items[#items + 1] = fmt_any(oke2 and raw2 or e)
                if #items >= 40 then return true end
              end)
            end)
            append_line(("DEBUG STRUCT:   %s[%d]={%s}"):format(n, num, table.concat(items, ",")))
          end
        end
      end
      append_line(("DEBUG STRUCT:   [%d] {%s}"):format(i, table.concat(parts, ", ")))
    end)
  end)
end

-- Fonctions BP d'une classe : candidats à RegisterHook (événements de
-- collecte et compagnie). Instance vivante uniquement : StaticFindObject
-- sur un nom de classe sans instance a crashé nativement le jeu le
-- 2026-10-18 (pcall impuissant face au crash C++).
local function dump_functions(cname, label)
  local obj = FindFirstOf(cname)
  local cls = nil
  if ref_ok(obj) then
    local okc, c = pcall(function() return obj:GetClass() end)
    if okc then cls = c end
  end
  if cls == nil then
    append_line(("DEBUG FUNCS: %s | sans instance (skip)"):format(label))
    return
  end
  local names = {}
  pcall(function()
    cls:ForEachFunction(function(f)
      local okn, n = pcall(function() return f:GetFName():ToString() end)
      if okn and #names < 600 then names[#names + 1] = n end
    end)
  end)
  append_line(("DEBUG FUNCS: %s (n=%d): %s"):format(label, #names, table.concat(names, ",")))
end

-- (dump uniquement) Sonde RegisterHook : ces fonctions sont-elles des
-- événements de collecte ? Le callback s'exécute sur le game thread, écrit
-- fichier directement. Inscription unique, seulement une fois le monde chargé
-- (les classes BP n'existent pas au boot).
-- Format du nom vérifié dans le source UE4SS (LuaMod.cpp) : StaticFindObject
-- sur le nom complet de l'UFunction, soit <Package>.<Classe>_<C>:<Func>.
local HOOK_CANDIDATES = {
  { "/Game/Blueprints/GameData/MV_GameInstance.MV_GameInstance_C:checkIfEventAvailable", "event" },
  { "/Game/Blueprints/GameData/MV_GameInstance.MV_GameInstance_C:onCinematicEventEnd", "cinematic" },
  { "/Game/Blueprints/GameData/MV_GameInstance.MV_GameInstance_C:Try Run Cinematic Event", "cineRun" },
  { "/Game/Blueprints/GameData/MV_GameInstance.MV_GameInstance_C:saveTimeToSlot", "trialSave" },
  { "/Game/Blueprints/GameData/MV_GameInstance.MV_GameInstance_C:addHealthPiece", "hpPiece" },
  { "/Game/Blueprints/GameData/MV_GameInstance.MV_GameInstance_C:updateImportantKey", "key" },
  { "/Game/Blueprints/GameData/MV_GameInstance.MV_GameInstance_C:getNewUpgradeSkill", "upgrade" },
  { "/Game/ThirdPerson/Player/BP_PlayerGoatMain.BP_PlayerGoatMain_C:tryAddOutfitToUnlockedList", "outfit" },
  { "/Game/Blueprints/LevelActors/BP_EncounterManager.BP_EncounterManager_C:BPI_ActivateEvent", "encounter" },
  -- état de l'encounter au moment précis de la désactivation : si les
  -- compteurs du manager discriminant victoire (ennemis tous morts) et fuite
  -- ou mort du joueur (ennemis encore en vie), le signal boss est ici.
  { "/Game/Blueprints/LevelActors/BP_EncounterManager.BP_EncounterManager_C:BPI_DeactivateEvent",
    "encDeact",
    function(obj)
      local parts = {}
      for _, p in ipairs({ "numberOfEnemies", "relevantEnemies", "ready?" }) do
        local ok, v = pcall(function() return obj[p] end)
        parts[#parts + 1] = p .. "=" .. (ok and tostring(v) or "?")
      end
      append_line("ENC STATE: " .. table.concat(parts, " "))
    end },
  { "/Game/Blueprints/LevelActors/BP_EncounterManager.BP_EncounterManager_C:BPI_EventCheckEnemyStart", "encCheck" },
  { "/Game/Blueprints/LevelActors/TimeTrial/BP_TimeTrial.BP_TimeTrial_C:manageResult", "trial" },
  -- convention BPI_* prouvée par la liste de TimeTrial : mort du joueur
  -- (composant de vie) — le boss n'a PAS de BP_HpHitable (session 2026-10-18 :
  -- 0 HPDEATH sur kill), sa mort passe par l'encounter manager ci-dessus.
  { "/Game/Blueprints/BP_HpHitable.BP_HpHitable_C:BPI_CombatDeath", "death" },
}
local hook_fires = 0

local function register_dump_hooks()
  for _, c in ipairs(HOOK_CANDIDATES) do
    if not c.registered then
      local ok, err = pcall(function()
        RegisterHook(c[1], function(self, ...)
          if hook_fires >= 300 then return end
          hook_fires = hook_fires + 1
          local parts = {}
          for i = 1, select("#", ...) do
            local a = select(i, ...)
            local okg, v = pcall(function() return a:get() end)
            if okg then
              -- param imbriqué éventuel (wrapper sur wrapper)
              local okg2, v2 = pcall(function() return v:get() end)
              v = okg2 and v2 or v
            else
              v = a
            end
            parts[#parts + 1] = fmt_any(v)
          end
          -- self est un wrapper : dérouiller avant GetFullName
          local okg, obj = pcall(function() return self:get() end)
          obj = okg and obj or self
          local okn, name = pcall(function() return obj:GetFullName() end)
          append_line(("HOOK FIRED: %s | self=%s | args={%s}"):format(
            c[2], okn and name or fmt_any(obj), table.concat(parts, ",")))
          if c[3] ~= nil then pcall(c[3], obj) end
        end)
      end)
      if ok then
        c.registered = true
        append_line(("DEBUG HOOK: %s | ok"):format(c[2]))
      else
        -- classe encore non chargée (ex: boss hors zone) : on réessaiera
        append_line(("DEBUG HOOK: %s | err: %s"):format(c[2], tostring(err):sub(1, 90)))
      end
    end
  end
end

-- Toutes les propriétés (réfléchies) d'un objet, valeurs incluses.
local function dump_props(obj, label)
  local okc, class = pcall(function() return obj:GetClass() end)
  if not okc or class == nil then
    append_line(("DEBUG PROPS: %s | class KO: %s"):format(label, tostring(class)))
    return
  end
  local okn, cname = pcall(function() return class:GetFullName() end)
  append_line(("DEBUG PROPS: %s | %s"):format(label, okn and cname or "?"))
  local okf, err = pcall(function()
    class:ForEachProperty(function(prop)
      local okp, fname = pcall(function() return prop:GetFName():ToString() end)
      if not okp then return end
      local okn2, pname = pcall(function() return prop:GetFullName() end)
      append_line(("DEBUG PROP:   %s :: %s = %s")
                  :format(fname, okn2 and pname or "?", fmt_prop_value(obj, fname)))
    end)
  end)
  if not okf then
    append_line(("DEBUG PROPS: %s | foreach KO: %s"):format(label, tostring(err)))
  end
end

-- Mots-clés de l'état de jeu qu'on cherche (classes encore inconnues).
local DUMP_KEYWORDS = { "save", "zone", "upgrade", "trial",
                        "encounter", "attire", "boss", "ending", "mv_" }

local function dump_class_matches(full)
  local low = full:lower()
  for _, kw in ipairs(DUMP_KEYWORDS) do
    if low:find(kw, 1, true) then return true end
  end
  return false
end

-- Signature de l'état chargé (comptes + 1er objet par classe) : son changement
-- signale un début de partie ou un changement de zone → nouveau scan.
local function dump_sig()
  local parts = {}
  for _, c in ipairs(DUMP_CLASSES) do
    local objs = FindAllOf(c) or {}
    parts[#parts + 1] = c .. "=" .. #objs
    if #objs > 0 then
      local ok, full = pcall(function() return objs[1]:GetFullName() end)
      parts[#parts + 1] = ok and full or "?"
    end
  end
  return table.concat(parts, "|")
end

local dump_ticks = 0
local dump_last_sig = nil
local dump_stable = 0   -- ticks consécutifs de sig identique
local dump_done = false -- dump de fond déjà fait pour cette sig

local function deep_dump()
  for _, spec in ipairs({
    { "GameInstance", "GameInstance" }, { "PlayerController", "PlayerController" },
    { "PlayerState", "PlayerState" }, { "GameStateBase", "GameStateBase" },
  }) do
    local obj = FindFirstOf(spec[1])
    if ref_ok(obj) then dump_props(obj, spec[2]) end
  end
  -- Pawn du joueur via le contrôleur : FindFirstOf("Character") peut tomber
  -- sur un NPC/ennemi (une zone en héberge plusieurs).
  local pawn = nil
  local pc = FindFirstOf("PlayerController")
  if ref_ok(pc) then
    local okp, p = pcall(function() return pc.Pawn end)
    if okp and p ~= nil then pawn = p end
  end
  if pawn == nil then
    local fc = FindFirstOf("Character")
    if ref_ok(fc) then pawn = fc end
  end
  if pawn ~= nil then dump_props(pawn, "Pawn") end
  -- composant de vie du joueur : proxy candidat pour PLAYER DIED
  if pawn ~= nil then
    local okh, hp = pcall(function() return pawn.BP_HpHitable end)
    if okh and hp ~= nil then dump_props(hp, "Pawn.BP_HpHitable") end
  end
  -- Cibles du state persistant : objet de sauvegarde (via GameInstance),
  -- composant de sauvegarde, données de zone, Time Trials, encounters de boss.
  local gi = FindFirstOf("GameInstance")
  if ref_ok(gi) then
    local oks, save = pcall(function() return gi.currentSave end)
    if oks and save ~= nil then
      dump_props(save, "currentSave")
      -- tenues : noms lisibles ; zones/pièces : structs (schéma + valeurs)
      dump_array(save, "unlockedOutfits", "save.unlockedOutfits")
      for _, fname in ipairs({ "SavedZoneData", "savedZoneArray", "allRoomData" }) do
        dump_struct_array(save, fname, "save." .. fname)
      end
    end
    dump_struct_array(gi, "All Room Data", "gi.All Room Data")
    dump_array(gi, "allZoneData", "gi.allZoneData")
    -- fonctions BP des classes clé : candidats RegisterHook (collectes…).
    -- Uniquement des classes déjà vues tourner sans crash : ForEachFunction
    -- / FindFirstOf sur ZoneDataObject_C a tué le jeu 2 fois (classe
    -- data-holder, chaîne de fonctions native corrompue ?).
    for _, spec in ipairs({
      { "BP_PlayerGoatMain_C", "Pawn" }, { "MV_GameInstance_C", "GameInstance" },
      { "MVMain_Save_C", "Save" }, { "saveComponent_C", "saveComponent" },
      { "BP_UpgradeBase_C", "UpgradeBase" }, { "BP_EncounterManager_C", "Encounter" },
      { "BP_TimeTrial_C", "TimeTrial" }, { "BP_TransitionZone_C", "TransitionZone" },
    }) do
      dump_functions(spec[1], spec[2])
    end
    register_dump_hooks()
  end
  for _, spec in ipairs({ { "saveComponent_C", "saveComponent" },
                          { "BP_TimeTrial_C", "TimeTrial" },
                          { "BP_EncounterManager_C", "EncounterManager" } }) do
    local objs = FindAllOf(spec[1]) or {}
    for i = 1, math.min(#objs, 4) do
      dump_props(objs[i], spec[2] .. "#" .. i)
    end
    if #objs == 0 then
      append_line(("DEBUG NONE: %s absent de la zone"):format(spec[1]))
    end
  end
  -- Diagnostic boss : instance (le niveau la charge quoi qu'il arrive),
  -- ses propriétés (y a-t-il un BP_HpHitable dessus ?) et ses fonctions
  -- (BPI_CombatDeath est-il implémenté ?), plus la liste des composants
  -- de vie du donjon et de leurs propriétaires.
  local boss_objs = FindAllOf("BP_PrincessBoss_C") or {}
  append_line(("DEBUG BOSS: %d instance(s)"):format(#boss_objs))
  if #boss_objs > 0 then
    dump_props(boss_objs[1], "Boss")
    dump_functions("BP_PrincessBoss_C", "Boss")
  end
  for _, c in ipairs(FindAllOf("BP_HpHitable_C") or {}) do
    local ok, n = pcall(function() return c:GetFullName() end)
    append_line("DEBUG HPINST: " .. (ok and n or "?"))
  end
  -- scan global : toutes les classes chargées dont le nom parle du state
  local seen = 0
  ForEachUObject(function(o)
    if seen >= 500 then return end
    local ok, isclass = pcall(function() return o:IsAnyClass() end)
    if ok and isclass then
      local ok2, full = pcall(function() return o:GetFullName() end)
      if ok2 and type(full) == "string" and dump_class_matches(full) then
        seen = seen + 1
        append_line("DEBUG CLASS: " .. full)
      end
    end
  end)
  append_line(("DEBUG CLASS SCAN: %d"):format(seen))
end

local function dump_tick()
  dump_ticks = dump_ticks + 1
  local sig = dump_sig()
  if sig ~= dump_last_sig then
    dump_last_sig = sig
    dump_stable = 0
    dump_done = false
    append_line("DEBUG STATE: " .. sig)
  else
    dump_stable = dump_stable + 1
  end
  -- Balayage profond : uniquement en jeu (au moins un Character = niveau
  -- jouable, pas le boot/titre — attention au parsage : la valeur est un
  -- compte, "Character=1" en sous-chaîne ne matcherait jamais un niveau avec
  -- NPCs (Character=7). ForEachUObject depuis LoopAsync à froid a crashé le
  -- jeu le 2026-10-18 : le tableau d'objets ne se balaye ni hors du game
  -- thread, ni pendant l'initialisation du moteur.
  local n_chars = tonumber(sig:match("Character=(%d+)") or "0") or 0
  local en_jeu = n_chars >= 1
  -- Différé : au changement de zone, l'ancien monde est encore en
  -- destruction quand le nouveau pawn apparaît (GC pas encore fait).
  -- Introspecter un objet mourant (dump sur BP_EncounterManager du donjon
  -- pendant le swap vers LowerCastle) a tué le jeu le 2026-10-18 — fin du
  -- log pile après UpgradeBase, le pcall ne protège pas d'un crash natif.
  -- On attend 12 ticks de sig stable (~12 s à 1 Hz) : swap + GC faits.
  local pret = en_jeu and dump_stable >= 12
  if pret and (not dump_done or dump_ticks % 120 == 1) then
    dump_done = true
    append_line("DEBUG DEEP: enqueue")   -- côté LoopAsync (écriture prouvée)
    ExecuteInGameThread(function()
      append_line("DEBUG DEEP: start")   -- callback exécuté ?
      local ok, err = pcall(deep_dump)
      append_line(ok and "DEBUG DEEP: ok"
                  or ("DEBUG DEEP: KO " .. tostring(err)))
    end)
  end
end

local logger = M.new_logger({
  collect = collect_state,
  append = append_line,
  settle = 5,   -- stabilisation au chargement (5 s à 1 Hz)
})

local tick_n = 0
local function tick()
  -- une erreur de tick tuerait la boucle LoopAsync en silence : on la montre.
  local ok, err = pcall(function()
    tick_n = tick_n + 1
    if tick_n % 30 == 0 then maybe_register_prod_hooks() end
    if LUDODEX_DUMP then dump_tick() end
    logger.tick()
  end)
  if not ok then warn("tick KO : " .. tostring(err)) end
end

-- Le chunk EST le point d'entrée : les mods officiels UE4SS n'ont pas de
-- function main() (vérifié sur ConsoleEnablerMod et BPModLoaderMod — tout est
-- top-level). GetModDirectory n'existe pas non plus dans ce build (3.0.1 Beta
-- #0, vérifié dans le DLL) : le chemin de ludodex.log se résout via
-- IterateGameDirectories, en relatif sur <Win64>/ue4ss en secours (CWD de ce
-- type, preuve : BPModLoader ouvre 'Mods/...' en relatif).
local function resolve_log_path()
  local cands = {}
  local ok, dirs = pcall(IterateGameDirectories)
  if ok and type(dirs) == "table" and dirs.Game ~= nil
     and dirs.Game.Binaries ~= nil and dirs.Game.Binaries.Win64 ~= nil then
    local ok2, abs = pcall(function() return dirs.Game.Binaries.Win64.__absolute_path end)
    if ok2 and type(abs) == "string" then
      cands[#cands + 1] = abs .. "\\ludodex.log"
    end
  end
  cands[#cands + 1] = "..\\ludodex.log"   -- CWD = <Win64>/ue4ss (cf. BPModLoader)
  cands[#cands + 1] = "ludodex.log"       -- CWD = <Win64> si jamais
  -- On ne retient que créable : vérifie io.open ET le chemin dès le boot.
  for _, path in ipairs(cands) do
    local okf, f = pcall(io.open, path, "a")
    if okf and f ~= nil then
      f:close()
      return path
    end
  end
  return cands[1] or "..\\ludodex.log"
end

LOG_PATH = resolve_log_path()
if LUDODEX_DUMP then
  append_line(("LUDODEX BOOT: log=%s"):format(tostring(LOG_PATH)))
end
LoopAsync(1000, tick)
