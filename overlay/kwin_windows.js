/*
 * Script KWin de l'overlay Ludodex : pousse l'état des fenêtres (taille, réduite,
 * actif) à l'overlay par D-Bus, à chaque changement. L'overlay en tire l'échelle
 * et sa mise en veille quand le jeu est réduit. Chargé et déchargé par l'overlay.
 */
var SERVICE = "io.github.AdrienPiechocki.Ludodex.Overlay";
var PATH = "/io/github/AdrienPiechocki/Ludodex/Overlay/Windows";
var IFACE = "io.github.AdrienPiechocki.Ludodex.Overlay.Windows";

function allWindows() {
    return workspace.windowList ? workspace.windowList() : workspace.clientList();
}

function onCurrentDesktop(w) {
    if (w.desktops !== undefined) {
        return w.desktops.length === 0 || w.desktops.indexOf(workspace.currentDesktop) >= 0;
    }
    return w.desktop === -1 || w.desktop === workspace.currentDesktop;
}

function overlap(a, b) {
    var x = Math.min(a.x + a.width, b.x + b.width) - Math.max(a.x, b.x);
    var y = Math.min(a.y + a.height, b.y + b.height) - Math.max(a.y, b.y);
    return x > 0 && y > 0 ? x * y : 0;
}

/* « Recouverte » : hors du bureau courant, ou une fenêtre d'un AUTRE processus, au-dessus dans
 * la pile, en cache une part notable (plus de 2 %). L'overlay est une couche de la fenêtre du jeu : il ne doit
 * pas se montrer par-dessus ce qui la recouvre. */
function covered(w, stack) {
    if (!onCurrentDesktop(w)) {
        return true;
    }
    var area = w.frameGeometry.width * w.frameGeometry.height;
    for (var i = stack.indexOf(w) + 1; i > 0 && i < stack.length; i++) {
        var o = stack[i];
        // les surfaces layer-shell (dont l'overlay lui-même, layer 9) comptent comme « normales » :
        // sans ce filtre l'overlay se recouvre lui-même et clignote.
        if (o.normalWindow && o.layer < 7 && !o.deleted && !o.minimized && o.pid !== w.pid && onCurrentDesktop(o)
            && overlap(w.frameGeometry, o.frameGeometry) * 50 > area) {
            return true;
        }
    }
    return false;
}

function snapshot(w, stack) {
    var g = w.clientGeometry || w.frameGeometry;
    return {
        pid: w.pid,
        title: String(w.caption),
        app_id: String(w.resourceClass || ""),
        x: Math.round(g.x),
        y: Math.round(g.y),
        width: Math.round(g.width),
        height: Math.round(g.height),
        minimized: !!w.minimized,
        active: !!w.active,
        covered: covered(w, stack)
    };
}

function push() {
    var out = [];
    var stack = workspace.stackingOrder || allWindows();
    allWindows().forEach(function (w) {
        if (w.normalWindow && !w.deleted) {
            out.push(snapshot(w, stack));
        }
    });
    callDBus(SERVICE, PATH, IFACE, "Update", JSON.stringify(out));
}

function watch(w) {
    ["frameGeometryChanged", "clientGeometryChanged", "minimizedChanged", "captionChanged",
     "activeChanged", "fullScreenChanged", "stackingOrderChanged"].forEach(function (sig) {
        if (w[sig] && w[sig].connect) {
            w[sig].connect(push);
        }
    });
}

["currentDesktopChanged"].forEach(function (sig) {
    if (workspace[sig] && workspace[sig].connect) {
        workspace[sig].connect(push);
    }
});
/* Ouvert, l'overlay prend le clavier et donc l'ACTIVATION : un jeu plein écran n'est plus actif,
 * KWin le descend sous le panneau et la barre des tâches apparaît. Tant que l'overlay est actif,
 * on garde donc au-dessus du panneau le plein écran qu'il vient de remplacer. */
var lastApp = workspace.activeWindow, raised = [];

function lower() {
    raised.forEach(function (w) {
        if (!w.deleted) {
            w.keepAbove = false;
        }
    });
    raised = [];
}

function activated(w) {
    if (w && w.layer >= 7) {
        if (lastApp && !lastApp.deleted && lastApp.fullScreen && !lastApp.keepAbove) {
            lastApp.keepAbove = true;
            raised.push(lastApp);
        }
    } else if (w && w.normalWindow) {
        lower();
        lastApp = w;
    }
}
["windowActivated", "clientActivated"].forEach(function (sig) {
    if (workspace[sig] && workspace[sig].connect) {
        workspace[sig].connect(activated);
    }
});
/* Fermé, l'overlay garde parfois l'activation : Python déclenche ce raccourci pour la rendre à
 * la dernière fenêtre « normale » active (le jeu). */
registerShortcut("LudodexRestoreFocus", "Ludodex overlay: give focus back to the game", "", function () {
    if (lastApp && !lastApp.deleted && !lastApp.minimized && !(workspace.activeWindow && workspace.activeWindow.layer < 7)) {
        workspace.activeWindow = lastApp;
    }
});
allWindows().forEach(watch);
workspace.windowAdded.connect(function (w) { watch(w); push(); });
workspace.windowRemoved.connect(push);
push();
