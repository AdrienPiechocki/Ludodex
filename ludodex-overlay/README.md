# Ludodex Overlay

Addon flatpak **séparé** (`io.github.AdrienPiechocki.LudodexOverlay`) : un
overlay façon Steam, affiché au-dessus du jeu pendant une session, démarré et
tué par Ludodex à chaque lancement de jeu.

## Comportement

- **Shift+Tab** (raccourci global via le portail `GlobalShortcuts`) ouvre et
  ferme l'overlay ; une fois fermé, seuls les widgets **épinglés** restent
  visibles, translucides et non interactifs (les clics traversent vers le jeu).
- L'overlay **ne prend jamais le focus clavier** : les jeux plein écran se
  minimiseraient d'eux-mêmes dès la perte de focus (constat du 2026-10-17,
  KWin active toute fenêtre layer-shell qui se carte — même en ON_DEMAND).
  Tout se pilote à la souris : **Shift+Tab** ou le bouton **✕ Fermer** du
  panneau referment l'overlay — l'application reste en tâche de fond pour la
  fin de session ; elle n'est jamais muette : si le raccourci refuse, une
  notification explique pourquoi puis l'overlay s'arrête.
- **Libération de la souris des jeux** : Ludodex injecte `libludodex-hook.so`
  dans le processus du jeu via `LD_PRELOAD` (le principe de l'overlay Steam).
  Quand l'overlay s'ouvre, le hook relâche la capture de souris **de l'intérieur
  du jeu** (SDL2 complet, SDL3 et X11 en partie) et empêche sa re-capture
  jusqu'à la fermeture — le jeu garde le focus, il ne se miniaturise jamais.
  Le jeu reprend la main tout seul à sa prochaine tentative de capture.
  Pendant que vous jouez (overlay fermé), le hook n'interfère pas du tout.
  ⚠ Jeux en ligne avec anti-cheat : une injection `LD_PRELOAD` peut être
  mal vue — désactivez « In-game overlay » dans Ludodex (Réglages → Général) :
  aucun hook n'est alors injecté et le jeu tourne normalement.
- Dans l'overlay : un panneau central (interrupteurs de widgets) et les
  widgets flottants, **déplaçables à la souris/doigt** avec aimantation
  douce aux bords — chaque bord et coin atteignable. Les réglages d'un
  widget (⚙) s'ouvrent **en ligne**, sous le widget : transparence
  (30 % d'opacité minimum), épinglage, options (24 h/12 h, secondes).
- **Langue** : l'overlay suit la langue définie dans Ludodex (Réglages →
  Général → Langue), transmise à chaque session par `--lang CODE` ;
  sans réglage explicite, la langue du système fait foi — comme Ludodex.
- Les réglages sont **globaux** (tous jeux) et persistés dans
  `~/.config/ludodex-overlay/config.json` (`$XDG_CONFIG_HOME` respecté ;
  dans le sandbox, `~/.var/app/io.github.AdrienPiechocki.LudodexOverlay/`).
- **Écran** : aucun réglage — l'overlay apparaît **là où est le jeu** (la
  surface layer-shell n'est liée à aucune sortie, le composant la place sur
  l'écran actif) ; les positions widgets restent des fractions, donc
  valables sur n'importe quel écran.
- **Fin de session** : l'overlay meurt avec le jeu (même rappel qui clôt la
  session de jeu, plus `atexit`). Aucun processus ni instance ne subsiste.
  Limite assumée (spec) : si la session se termine hors des chemins suivis
  par Ludodex (partie de moins de 5 s, lancement d'URI jamais vu, crash de
  Ludodex), l'overlay peut rester — **Shift+Tab** ou **✕ Fermer** le
  referment à la main.
- **Session X11** (hors périmètre v1) : sortie silencieuse immédiate, le jeu
  se lance normalement.
- **Addon absent** : Ludodex lance le jeu normalement, sans alerte.

## Installation

```sh
flatpak install --user build/ludodex-overlay.flatpak
```

L'addon est un flatpak à part : l'installer/désinstaller n'affecte pas
Ludodex. Ludodex le démarre automatiquement au lancement de chaque jeu
(interrupteur « Overlay in-game » dans Réglages → Général, activé par
défaut).

## Lancement manuel

```sh
flatpak run io.github.AdrienPiechocki.LudodexOverlay --demo   # voile + panneau immédiatement
flatpak run io.github.AdrienPiechocki.LudodexOverlay --game "Nom du jeu"
flatpak run io.github.AdrienPiechocki.LudodexOverlay --game "Nom" --lang fr          # langue forcée (sinon langue du système)
```

Au **premier** lancement (nouvelle app), KDE affiche une demande de
confirmation pour le raccourci global ; une fois validée, les suivants sont
automatiques. Sans validation sous 45 s, une notification annonce l'arrêt de
l'overlay.

## Reconstruction

```sh
sh ludodex-overlay/build.sh    # → build/ludodex-overlay.flatpak
```

Prérequis : `flatpak-builder`, flathub (org.gnome.Sdk 51 ; gtk4-layer-shell
est compilé depuis le manifeste `io.github.AdrienPiechocki.LudodexOverlay.yml`).

## Limites v1

- KDE Plasma **Wayland** uniquement (X11 : sortie silencieuse) ; composants
  GTK layer-shell attendus côté hôte.
- Un seul widget (l'horloge) ; le bouton « Ajouter une widget » est
  insensible en attendant la suite.
- La première liaison du raccourci exige une validation manuelle (portail
  KDE) ; aucun second essai automatique en cas de refus (notification puis
  sortie).

## Tests

```sh
python3 -m pytest -q    # depuis la racine du dépôt
```
