"""overlay — overlay en jeu de Ludodex (processus séparé, gtk-layer-shell).

L'overlay est une surface Wayland « layer-shell » dessinée PAR-DESSUS le jeu par un
processus à part : rien n'est injecté dans le jeu (ni LD_PRELOAD, ni hook), le
compositeur fait seul la superposition. Voir README.md, section « Overlay »."""
APP_ID = "io.github.AdrienPiechocki.Ludodex.Overlay"
