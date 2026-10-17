# Ludodex Overlay

Addon flatpak **séparé** (`io.github.AdrienPiechocki.LudodexOverlay`) : un
overlay façon Steam, affiché au-dessus du jeu pendant une session, démarré et
tué par Ludodex à chaque lancement de jeu.

## Comportement

- **Shift+Tab** (raccourci global via le portail `GlobalShortcuts`) ouvre et
  ferme l'overlay ; une fois fermé, seuls les widgets **épinglés** restent
  visibles, translucides et non interactifs (les clics traversent vers le jeu).
- **Échap** (en mode overlay) referme l'overlay ; le bouton **⏻ Quitter**
  termine l'application — elle n'est jamais muette : si le raccourci refuse,
  une notification explique pourquoi puis l'overlay s'arrête.
- Dans l'overlay : un panneau central (interrupteurs de widgets, réglages,
  écran) et les widgets flottants, **déplaçables à la souris/doigt** avec
  aimantation douxe aux bords. Réglages par widget : transparence (30 %
  d'opacité minimum), épinglage, options (24 h/12 h, secondes).
- Les réglages sont **globaux** (tous jeux) et persistés dans
  `~/.config/ludodex-overlay/config.json` (`$XDG_CONFIG_HOME` respecté ;
  dans le sandbox, `~/.var/app/io.github.AdrienPiechocki.LudodexOverlay/`).
- **Écran** : « Auto » par défaut, ou sortie choisie par son nom (DP-1,
  eDP-2…), persistée ; les positions widgets restent des fractions, donc
  valables sur n'importe quel écran.
- **Fin de session** : l'overlay meurt avec le jeu (même rappel qui clôt la
  session de jeu, plus `atexit`). Aucun processus ni instance ne subsiste.
  Limite assumée (spec) : si la session se termine hors des chemins suivis
  par Ludodex (partie de moins de 5 s, lancement d'URI jamais vu, crash de
  Ludodex), l'overlay peut rester — le bouton **⏻ Quitter** le referme.
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
