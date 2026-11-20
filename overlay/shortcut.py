"""shortcut.py — raccourci global via le portail XDG « GlobalShortcuts » (best effort).

Wayland interdit à une application de lire le clavier tant qu'elle n'a pas le
focus : le seul moyen standard est ce portail (KDE, GNOME, Hyprland). Sur les
compositeurs qui ne l'ont pas (sway, dwl, river… via xdg-desktop-portal-wlr), la
liaison échoue sans bruit — on lie alors une touche du compositeur à
`ludodex-overlay --toggle` (voir README). Ouvert, l'overlay capte lui-même
Maj+Tab pour se refermer."""
import secrets

from gi.repository import Gio, GLib

BUS = "org.freedesktop.portal.Desktop"
PATH = "/org/freedesktop/portal/desktop"
IFACE = "org.freedesktop.portal.GlobalShortcuts"
SHORTCUT_ID = "toggle-overlay"


class GlobalShortcut:
    def __init__(self, on_activated, trigger="SHIFT+Tab", description="Toggle the Ludodex overlay"):
        self.on_activated, self.trigger, self.description = on_activated, trigger, description
        self.bus = self.session = None
        self.ok = False

    def start(self):
        try:
            self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except GLib.Error:
            return
        sender = self.bus.get_unique_name().lstrip(":").replace(".", "_")
        self._sender = sender
        tok = "ld" + secrets.token_hex(4)
        self._request(f"{PATH}/request/{sender}/{tok}", self._on_session,
                      lambda: self.bus.call(
                          BUS, PATH, IFACE, "CreateSession",
                          GLib.Variant("(a{sv})", ({
                              "handle_token": GLib.Variant("s", tok),
                              "session_handle_token": GLib.Variant("s", "ld" + secrets.token_hex(4))},)),
                          None, Gio.DBusCallFlags.NONE, -1, None, self._call_done))

    def _request(self, path, handler, call):
        """S'abonne à la réponse de la requête AVANT de l'émettre (sinon on peut la manquer)."""
        sid = None

        def on_resp(_c, _s, _p, _i, _n, params):
            self.bus.signal_unsubscribe(sid)
            code, results = params.unpack()
            if code == 0:
                handler(results)
        sid = self.bus.signal_subscribe(BUS, "org.freedesktop.portal.Request", "Response",
                                        path, None, Gio.DBusSignalFlags.NONE, on_resp)
        call()

    def _call_done(self, bus, res):
        try:
            bus.call_finish(res)
        except GLib.Error:
            pass   # pas de portail / pas de GlobalShortcuts : repli sur la touche du compositeur

    def _on_session(self, results):
        self.session = results.get("session_handle")
        if not self.session:
            return
        self.bus.signal_subscribe(BUS, IFACE, "Activated", PATH, None, Gio.DBusSignalFlags.NONE,
                                  self._on_activated)
        tok = "ld" + secrets.token_hex(4)
        shortcuts = [(SHORTCUT_ID, {"description": GLib.Variant("s", self.description),
                                    "preferred_trigger": GLib.Variant("s", self.trigger)})]
        self._request(f"{PATH}/request/{self._sender}/{tok}", lambda _r: setattr(self, "ok", True),
                      lambda: self.bus.call(
                          BUS, PATH, IFACE, "BindShortcuts",
                          GLib.Variant("(oa(sa{sv})sa{sv})", (self.session, shortcuts, "", {
                              "handle_token": GLib.Variant("s", tok)})),
                          None, Gio.DBusCallFlags.NONE, -1, None, self._call_done))

    def _on_activated(self, _c, _s, _p, _i, _n, params):
        session, shortcut_id, _ts, _opts = params.unpack()
        if session == self.session and shortcut_id == SHORTCUT_ID:
            self.on_activated()
