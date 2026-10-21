//=============================================================================
// LudodexLogger.js
//=============================================================================
/*:
 * @plugindesc v1.7 Journal d'événements (ludodex.log) pour le tracker de succès Ludodex.
 * @author Ludodex
 *
 * @param logFile
 * @text Fichier de log
 * @desc Relatif au dossier du jeu (celui de index.html) ou chemin absolu.
 * @default ludodex.log
 *
 * @param dumpFile
 * @text Fichier de dump
 * @desc Liste id/nom de la base de données, réécrite à chaque lancement. Vide = désactivé.
 * @default ludodex-dump.txt
 *
 * @param logSwitches
 * @text Logger les interrupteurs
 * @type boolean
 * @default true
 *
 * @param switchIds
 * @text Interrupteurs suivis
 * @desc IDs ou plages, ex : 1-50,120,300-340. Vide = tous.
 * @default
 *
 * @param variableIds
 * @text Variables suivies
 * @desc IDs ou plages, ex : 10,25-30. Vide = aucune. "*" = toutes.
 * @default
 *
 * @param commonEventIds
 * @text Événements communs suivis
 * @desc IDs ou plages loggués en COMMON EVENT. Défaut : 310 (CREDITS). Vide = aucun.
 * @default 310
 *
 * @param bookEventIds
 * @text Événements communs = livres
 * @desc Loggués une seule fois (BOOK DISCOVERED). Défaut : livres de Fear & Hunger. Vide = aucun.
 * @default 3,5-13,16,19-22,24,30,60-63,78,93,139,142,146,155-160,183,184,187,228,232,245,247,263
 *
 * @param logCommonEvents
 * @text Logger les événements communs
 * @type boolean
 * @default false
 *
 * @param creditsEventIds
 * @text Événements communs = générique
 * @desc Événements communs marquant la fin d'une partie (générique). Écrit une ligne ENDING. Défaut : 310 (CREDITS). Vide = aucun.
 * @default 310
 *
 * @param throneSwitchIds
 * @text Interrupteurs = trône
 * @desc Interrupteurs activés en s'asseyant sur le Trône de l'Ascension (noté throne=1 dans ENDING). Défaut : 1839.
 * @default 1839
 *
 * @param endingTroops
 * @text Troupes = fin
 * @desc id=label,... Troupes du combat final associées à une fin (a, b, c...).
 * @default 122=d,123=d,124=d,125=d,126=c,130=a,155=b,169=b,170=b
 *
 * @param endingMaps
 * @text Cartes = fin
 * @desc id=label,... Cartes de scène de fin (utilisé si la troupe ne dit rien).
 * @default 86=e
 *
 * @param endingKeyActors
 * @text Personnages clés des fins
 * @desc id=nom,... Personnages dont la présence dans le groupe est notée dans la ligne ENDING.
 * @default 2=girl,6=legarde
 *
 * @help
 * Ce plugin ne lit AUCUN texte du jeu pour décider quoi logger : il écoute le
 * moteur (objets, ennemis, cartes, états...) et écrit les ID de la base de
 * données. Les succès Ludodex se basent sur ces ID, donc ils fonctionnent
 * quelle que soit la langue du jeu (patch FR compris). Le nom affiché après
 * le "|" est celui de la base de données chargée, à titre indicatif.
 *
 * Installation : copier ce fichier dans js/plugins/, puis l'ajouter en fin
 * de liste dans js/plugins.js :
 *   {"name":"LudodexLogger","status":true,"description":"","parameters":{}},
 *
 * Le fichier ludodex-dump.txt contient (catégorie, id, nom) pour toute la
 * base de données de CETTE installation : c'est lui qui sert à écrire les
 * succès propres à l'histoire du jeu.
 */

(function () {
  'use strict';

  var fs, path;
  try { fs = require('fs'); path = require('path'); } catch (e) { return; }

  var params = PluginManager.parameters('LudodexLogger');
  var baseDir = (process.mainModule && process.mainModule.filename)
    ? path.dirname(process.mainModule.filename) : process.cwd();

  function resolvePath(p) { return path.isAbsolute(p) ? p : path.join(baseDir, p); }

  var logPath = resolvePath(String(params.logFile || 'ludodex.log'));
  var dumpName = params.dumpFile === undefined ? 'ludodex-dump.txt' : String(params.dumpFile);
  var dumpPath = dumpName ? resolvePath(dumpName) : '';
  var logSwitches = String(params.logSwitches) !== 'false';
  var logCommonEvents = String(params.logCommonEvents) === 'true';
  // True une fois une vraie partie commencée (nouvelle partie ou chargement
  // depuis le menu), faux à l'écran titre. Aucune autre mémoire persistante.
  var inSession = false;

  function parseRanges(str) {
    var out = [];
    String(str || '').split(',').forEach(function (part) {
      if (/^\s*\*\s*$/.test(part)) { out.push([0, 1e9]); return; }
      var m = /^\s*(\d+)\s*(?:-\s*(\d+))?\s*$/.exec(part);
      if (m) out.push([+m[1], m[2] ? +m[2] : +m[1]]);
    });
    return out;
  }
  function inRanges(ranges, id) {
    for (var i = 0; i < ranges.length; i++) {
      if (id >= ranges[i][0] && id <= ranges[i][1]) return true;
    }
    return false;
  }
  var switchRanges = parseRanges(params.switchIds);
  var variableRanges = parseRanges(params.variableIds);
  var commonRanges = parseRanges(params.commonEventIds === undefined ? '310' : params.commonEventIds);
  var bookRanges = parseRanges(params.bookEventIds === undefined
    ? '3,5-13,16,19-22,24,30,60-63,78,93,139,142,146,155-160,183,184,187,228,232,245,247,263'
    : params.bookEventIds);
  var creditsRanges = parseRanges(params.creditsEventIds === undefined ? '310' : params.creditsEventIds);
  var throneRanges = parseRanges(params.throneSwitchIds === undefined ? '1839' : params.throneSwitchIds);

  function parseIdLabelMap(str) {
    var out = {};
    String(str || '').split(',').forEach(function (part) {
      var m = /^\s*(\d+)\s*=\s*([A-Za-z0-9_]+)\s*$/.exec(part);
      if (m) out[+m[1]] = m[2];
    });
    return out;
  }
  var endingTroopLabels = parseIdLabelMap(params.endingTroops === undefined
    ? '122=d,123=d,124=d,125=d,126=c,130=a,155=b,169=b,170=b'
    : params.endingTroops);
  var endingMapLabels = parseIdLabelMap(params.endingMaps === undefined ? '86=e' : params.endingMaps);
  var endingKeyLabels = parseIdLabelMap(params.endingKeyActors === undefined ? '2=girl,6=legarde'
    : params.endingKeyActors);
  var throneFlag = 0;

  // ---------------------------------------------------------------- écriture
  function clean(s) {
    return String(s == null ? '' : s)
      .replace(/\\[A-Za-z]+\[\d*\]/g, '')
      .replace(/[\r\n\t|]+/g, ' ')
      .trim();
  }
  function write(line) {
    try { fs.appendFileSync(logPath, line + '\n', 'utf8'); } catch (e) {}
  }
  function emit(tag, kv, label) {
    // Rien n'est loggé avant une vraie partie (nouvelle partie ou chargement
    // depuis le menu) : évite les événements fantômes de l'écran titre.
    if (!inSession && tag !== 'SESSION START') return;
    write(tag + ': ' + kv + ' | ' + clean(label));
  }

  // Aucune mémoire persistante dans le plugin : Ludodex gère la progression.
  // Les tags "DISCOVERED" / "FIRST ..." sont donc loggués à chaque occurrence.
  // Log vidé à chaque lancement du jeu.
  try { fs.writeFileSync(logPath, '', 'utf8'); } catch (e) {}

  function emitFirst(tag, kv, label) { emit(tag, kv, label); }

  function dbName(list, id) {
    var o = list && list[id];
    return o && o.name ? o.name : '';
  }
  function wrap(obj, name, fn) {
    if (!obj || typeof obj[name] !== 'function') return;
    var orig = obj[name];
    obj[name] = function () { return fn.call(this, orig, arguments); };
  }
  function leaderId() {
    var l = $gameParty && $gameParty.leader ? $gameParty.leader() : null;
    return l && l.actorId ? l.actorId() : 0;
  }
  function partySize() { return $gameParty ? $gameParty.members().length : 0; }
  function itemKind(item) {
    if (!item) return null;
    if (DataManager.isItem(item)) return 'item';
    if (DataManager.isWeapon(item)) return 'weapon';
    if (DataManager.isArmor(item)) return 'armor';
    return null;
  }

  emit('SESSION START', 'plugin=1.7');

  // ------------------------------------------------------------------ objets
  // Changer d'équipement passe par gainItem : on le neutralise pour ne pas
  // compter un "objet obtenu" à chaque déséquipement.
  var suppressGain = 0;
  ['tradeItemWithParty', 'releaseUnequippableItems'].forEach(function (n) {
    wrap(Game_Actor.prototype, n, function (orig, args) {
      suppressGain++;
      try { return orig.apply(this, args); } finally { suppressGain--; }
    });
  });

  wrap(Game_Party.prototype, 'gainItem', function (orig, args) {
    var item = args[0];
    var kind = itemKind(item);
    var before = kind ? this.numItems(item) : 0;
    var r = orig.apply(this, args);
    if (kind && !suppressGain) {
      var gained = this.numItems(item) - before;
      if (gained > 0) {
        emit('ITEM ACQUIRED', kind + '=' + item.id + ' qty=' + gained
          + ' map=' + ($gameMap ? $gameMap.mapId() : 0), item.name);
        emitFirst('ITEM DISCOVERED', kind + '=' + item.id
          + ' map=' + ($gameMap ? $gameMap.mapId() : 0), item.name);
      }
    }
    return r;
  });

  wrap(Game_Battler.prototype, 'useItem', function (orig, args) {
    var item = args[0];
    var r = orig.apply(this, args);
    if (this.isActor && this.isActor() && item) {
      if (DataManager.isSkill(item)) {
        emit('SKILL USED', 'actor=' + this.actorId() + ' skill=' + item.id
          + ' map=' + ($gameMap ? $gameMap.mapId() : 0), item.name);
      } else if (DataManager.isItem(item)) {
        emit('ITEM USED', 'actor=' + this.actorId() + ' item=' + item.id
          + ' map=' + ($gameMap ? $gameMap.mapId() : 0), item.name);
      }
    }
    return r;
  });

  // ----------------------------------------------------------------- morts
  var dying = null;
  [Game_Battler, Game_Actor, Game_Enemy].forEach(function (cls) {
    if (!Object.prototype.hasOwnProperty.call(cls.prototype, 'die')) return;
    wrap(cls.prototype, 'die', function (orig, args) {
      if (dying === this) return orig.apply(this, args);
      dying = this;
      try {
        var r = orig.apply(this, args);
        if (this.isEnemy && this.isEnemy()) {
          var lbl = this.originalName ? this.originalName() : this.name();
          emit('ENEMY KILLED', 'enemy=' + this.enemyId(), lbl);
          emitFirst('ENEMY FIRST KILLED', 'enemy=' + this.enemyId(), lbl);
        } else if (this.isActor && this.isActor()) {
          emit('ACTOR DIED', 'actor=' + this.actorId(), this.name());
        }
        return r;
      } finally { dying = null; }
    });
  });

  wrap(Game_Battler.prototype, 'addNewState', function (orig, args) {
    var id = args[0];
    var r = orig.apply(this, args);
    if (this.isActor && this.isActor() && id !== this.deathStateId()) {
      emit('STATE ADDED', 'actor=' + this.actorId() + ' state=' + id
        + ' map=' + ($gameMap ? $gameMap.mapId() : 0), dbName($dataStates, id));
    }
    return r;
  });

  // ---------------------------------------------------------------- combats
  var lastTroop = 0;
  wrap(BattleManager, 'setup', function (orig, args) {
    var r = orig.apply(this, args);
    lastTroop = args[0];
    emit('BATTLE START', 'troop=' + lastTroop + ' party=' + partySize(), dbName($dataTroops, lastTroop));
    return r;
  });
  wrap(BattleManager, 'startBattle', function (orig, args) {
    var r = orig.apply(this, args);
    $gameTroop.members().forEach(function (e) {
      emitFirst('ENEMY DISCOVERED', 'enemy=' + e.enemyId(),
        e.originalName ? e.originalName() : e.name());
    });
    return r;
  });
  wrap(BattleManager, 'processVictory', function (orig, args) {
    emit('BATTLE WON', 'troop=' + lastTroop + ' party=' + partySize(), dbName($dataTroops, lastTroop));
    if (partySize() === 1) emit('SOLO VICTORY', 'troop=' + lastTroop, dbName($dataTroops, lastTroop));
    emitFirst('BATTLE FIRST WON', 'troop=' + lastTroop, dbName($dataTroops, lastTroop));
    return orig.apply(this, args);
  });
  wrap(BattleManager, 'processDefeat', function (orig, args) {
    emit('BATTLE LOST', 'troop=' + lastTroop + ' party=' + partySize(), dbName($dataTroops, lastTroop));
    return orig.apply(this, args);
  });
  wrap(BattleManager, 'onEscapeSuccess', function (orig, args) {
    emit('BATTLE ESCAPED', 'troop=' + lastTroop + ' party=' + partySize(), dbName($dataTroops, lastTroop));
    return orig.apply(this, args);
  });

  // ------------------------------------------------------------------ groupe
  wrap(Game_Party.prototype, 'addActor', function (orig, args) {
    var id = args[0];
    var had = this._actors.indexOf(id) >= 0;
    var r = orig.apply(this, args);
    if (!had && this._actors.indexOf(id) >= 0) {
      emit('PARTY JOIN', 'actor=' + id + ' map=' + ($gameMap ? $gameMap.mapId() : 0),
        dbName($dataActors, id));
    }
    return r;
  });
  wrap(Game_Party.prototype, 'removeActor', function (orig, args) {
    var id = args[0];
    var had = this._actors.indexOf(id) >= 0;
    var r = orig.apply(this, args);
    if (had && this._actors.indexOf(id) < 0) {
      emit('PARTY LEAVE', 'actor=' + id + ' map=' + ($gameMap ? $gameMap.mapId() : 0),
        dbName($dataActors, id));
    }
    return r;
  });

  // Game_Actor.setup apprend les compétences de départ : à ignorer.
  var settingUp = 0;
  wrap(Game_Actor.prototype, 'setup', function (orig, args) {
    settingUp++;
    try { return orig.apply(this, args); } finally { settingUp--; }
  });
  wrap(Game_Actor.prototype, 'learnSkill', function (orig, args) {
    var id = args[0];
    var had = this.isLearnedSkill(id);
    var r = orig.apply(this, args);
    if (!settingUp && !had && this.isLearnedSkill(id)) {
      emit('SKILL LEARNED', 'actor=' + this.actorId() + ' skill=' + id
        + ' map=' + ($gameMap ? $gameMap.mapId() : 0), dbName($dataSkills, id));
    }
    return r;
  });

  wrap(Game_Actor.prototype, 'changeEquip', function (orig, args) {
    var slotId = args[0], item = args[1];
    var before = this.equips()[slotId];
    var r = orig.apply(this, args);
    var kind = itemKind(item);
    if (kind && item !== before && !settingUp && this.equips()[slotId] === item) {
      emit('ITEM EQUIPPED', 'actor=' + this.actorId() + ' ' + kind + '=' + item.id, item.name);
    }
    return r;
  });

  // ------------------------------------------------------------------- cartes
  wrap(Game_Map.prototype, 'setup', function (orig, args) {
    var r = orig.apply(this, args);
    var id = args[0];
    var label = ($dataMap && $dataMap.displayName) || dbName($dataMapInfos, id);
    emit('LOCATION ENTERED', 'map=' + id, label);
    var ld = leaderId();
    if (ld) emitFirst('LEADER DISCOVERED', 'actor=' + ld, dbName($dataActors, ld));
    emitFirst('LOCATION DISCOVERED', 'map=' + id, label);
    return r;
  });

  // ------------------------------------------- interrupteurs, variables, events
  if (logSwitches) {
    wrap(Game_Switches.prototype, 'setValue', function (orig, args) {
      var id = args[0];
      var was = this.value(id);
      var r = orig.apply(this, args);
      var now = this.value(id);
      if (now && inRanges(throneRanges, id)) throneFlag = 1;
      if (was !== now && (!switchRanges.length || inRanges(switchRanges, id))) {
        emit(now ? 'SWITCH ON' : 'SWITCH OFF', 'switch=' + id + ' leader=' + leaderId(), $dataSystem.switches[id]);
      }
      return r;
    });
  }
  if (variableRanges.length) {
    wrap(Game_Variables.prototype, 'setValue', function (orig, args) {
      var id = args[0];
      var was = this.value(id);
      var r = orig.apply(this, args);
      var now = this.value(id);
      if (was !== now && inRanges(variableRanges, id)) {
        emit('VARIABLE SET', 'variable=' + id + ' value=' + now, $dataSystem.variables[id]);
      }
      return r;
    });
  }
  function logCommonEvent(id) {
    if (logCommonEvents || inRanges(commonRanges, id)) {
      emit('COMMON EVENT', 'event=' + id, dbName($dataCommonEvents, id));
    }
    if (inRanges(bookRanges, id)) {
      emitFirst('BOOK DISCOVERED', 'event=' + id, dbName($dataCommonEvents, id));
    }
    if (inRanges(creditsRanges, id)) {
      emitEnding();
    }
  }
  // Ligne émise au générique de fin : map, fin reconnue (troupe puis carte),
  // dernière troupe, passage par le trône, présence des personnages clés.
  function inPartyOf(id) {
    return $gameParty && $gameParty._actors && $gameParty._actors.indexOf(id) >= 0;
  }
  function emitEnding() {
    var mapId = $gameMap ? $gameMap.mapId() : 0;
    var label = endingTroopLabels[lastTroop] || endingMapLabels[mapId] || 'none';
    var kv = 'map=' + mapId + ' ending=' + label + ' last_troop=' + lastTroop
      + ' throne=' + (throneFlag ? 1 : 0);
    Object.keys(endingKeyLabels).forEach(function (id) {
      kv += ' ' + endingKeyLabels[id] + '=' + (inPartyOf(+id) ? 1 : 0);
    });
    kv += ' leader=' + leaderId();
    emit('ENDING', kv, dbName($dataMapInfos, mapId));
  }
  wrap(Game_Interpreter.prototype, 'command117', function (orig, args) {
    logCommonEvent(this._params[0]);
    return orig.apply(this, args);
  });
  // Les livres/objets lancent souvent leur événement commun via l'effet de l'objet.
  wrap(Game_Temp.prototype, 'reserveCommonEvent', function (orig, args) {
    logCommonEvent(args[0]);
    return orig.apply(this, args);
  });

  // ------------------------------------------------------------ cycle de jeu
  wrap(DataManager, 'setupNewGame', function (orig, args) {
    // Seule une nouvelle partie lancée depuis le menu compte : le jeu peut
    // aussi appeler setupNewGame de lui-même au démarrage (écran titre,
    // création de personnage), ce qui comptait deux parties par lancement.
    var fromTitle = typeof Scene_Title !== 'undefined' &&
      SceneManager._scene instanceof Scene_Title;
    var r = orig.apply(this, args);
    if (fromTitle) { inSession = true; emit('NEW GAME', 'new=1'); }
    return r;
  });
  wrap(DataManager, 'saveGame', function (orig, args) {
    var r = orig.apply(this, args);
    if (r) emit('GAME SAVED', 'slot=' + args[0]);
    return r;
  });
  wrap(DataManager, 'loadGame', function (orig, args) {
    var r = orig.apply(this, args);
    if (r) { inSession = true; emit('GAME LOADED', 'slot=' + args[0]); }
    return r;
  });
  // Retour au titre : fin de session de log (repart à zéro jusqu'à une
  // nouvelle partie ou un chargement).
  wrap(Scene_Title.prototype, 'start', function (orig, args) {
    inSession = false;
    return orig.apply(this, args);
  });
  // Retour au titre : depuis une carte (fin de partie via évènement, ou menu)
  // ou depuis le game over. "map" = dernière carte visitée : sert à repérer les fins.
  wrap(SceneManager, 'goto', function (orig, args) {
    var cur = this._scene;
    if (args[0] === Scene_Title && cur && !(cur instanceof Scene_Boot) && !(cur instanceof Scene_Title)) {
      var from = cur instanceof Scene_Gameover ? 'gameover' : (cur instanceof Scene_Map ? 'map' : 'other');
      emit('TITLE RETURN', 'from=' + from + ' map=' + ($gameMap ? $gameMap.mapId() : 0));
    }
    return orig.apply(this, args);
  });
  wrap(Scene_Gameover.prototype, 'start', function (orig, args) {
    emit('GAME OVER', 'reached=1');
    return orig.apply(this, args);
  });

  // -------------------------------------------------------------------- dump
  function writeDump() {
    if (!dumpPath) return;
    var out = [];
    function list(cat, arr) {
      if (!arr) return;
      for (var i = 1; i < arr.length; i++) {
        if (arr[i] && arr[i].name) out.push(cat + '\t' + i + '\t' + clean(arr[i].name));
      }
    }
    function names(cat, arr) {
      if (!arr) return;
      for (var i = 1; i < arr.length; i++) {
        if (arr[i]) out.push(cat + '\t' + i + '\t' + clean(arr[i]));
      }
    }
    out.push('game\t0\t' + clean($dataSystem.gameTitle));
    list('enemy', $dataEnemies);
    list('troop', $dataTroops);
    if ($dataTroops) {
      for (var t = 1; t < $dataTroops.length; t++) {
        var tr = $dataTroops[t];
        if (tr && tr.members && tr.members.length) {
          out.push('troopmembers\t' + t + '\t' + tr.members.map(function (m) { return m.enemyId; }).join(','));
        }
      }
    }
    list('item', $dataItems);
    list('weapon', $dataWeapons);
    list('armor', $dataArmors);
    list('skill', $dataSkills);
    list('state', $dataStates);
    list('actor', $dataActors);
    list('map', $dataMapInfos);
    list('commonevent', $dataCommonEvents);
    names('switch', $dataSystem.switches);
    names('variable', $dataSystem.variables);
    try { fs.writeFileSync(dumpPath, out.join('\n') + '\n', 'utf8'); } catch (e) {}
  }
  wrap(Scene_Boot.prototype, 'start', function (orig, args) {
    var r = orig.apply(this, args);
    writeDump();
    return r;
  });
})();
