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

function snapshot(w) {
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
        active: !!w.active
    };
}

function push() {
    var out = [];
    allWindows().forEach(function (w) {
        if (w.normalWindow && !w.deleted) {
            out.push(snapshot(w));
        }
    });
    callDBus(SERVICE, PATH, IFACE, "Update", JSON.stringify(out));
}

function watch(w) {
    ["frameGeometryChanged", "clientGeometryChanged", "minimizedChanged", "captionChanged",
     "activeChanged", "fullScreenChanged"].forEach(function (sig) {
        if (w[sig] && w[sig].connect) {
            w[sig].connect(push);
        }
    });
}

allWindows().forEach(watch);
workspace.windowAdded.connect(function (w) { watch(w); push(); });
workspace.windowRemoved.connect(push);
push();
