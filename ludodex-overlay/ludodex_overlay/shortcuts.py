"""shortcuts.py — portail GlobalShortcuts : recettes D-Bus figées au spike.

Tout ce fichier vient du spike validé sur KDE Plasma / xdg-desktop-portal-kde :
les signatures ci-dessous sont celles que le daemon implémente RÉELLEMENT,
pas celles de la spec (écarts : session_handle en `s` et non `o`, pas de
`targets`, BindShortcuts en struct `(id, options)`).

Pièges mémorisés :
- CreateSession exige `handle_token` ET `session_handle_token` — sans le
  second, une assertion plante le portail (#1314/#2037) ;
- le format de touche est celui de la shortcuts-spec : `SHIFT+Tab`
  (modificateurs en majuscules, touche xkb) — `targets` n'existe pas et le
  portail l'ignorait silencieusement ;
- l'OUT de CreateSession est la poignée de REQUÊTE, la session se lit dans le
  `Response(0)` de cette requête (champ `session_handle`).
"""
from gi.repository import Gio, GLib

from .i18n import tr

TOKEN_REQUETE = "ludodexoverlay"   # handle_token de CreateSession
TOKEN_SESSION = "ludodexsession"   # session_handle_token de CreateSession
TOKEN_BIND = "ludodexbind"         # handle_token de BindShortcuts

VEILLE_SECONDES = 45   # dialogue KDE jamais validé → échec, notification, exit

BUS = "org.freedesktop.portal.Desktop"
CHEMIN = "/org/freedesktop/portal/desktop"
IFACE = "org.freedesktop.portal.GlobalShortcuts"
IFACE_REQUETE = "org.freedesktop.portal.Request"


def opts_create() -> GLib.Variant:
    """Options de CreateSession — type `(a{sv})`, **les deux jetons obligatoires**."""
    return GLib.Variant("(a{sv})", ({
        "handle_token": GLib.Variant("s", TOKEN_REQUETE),
        "session_handle_token": GLib.Variant("s", TOKEN_SESSION),
    },))


def session_depuis_reponse(req: str, resultats: dict,
                           token_session: str = TOKEN_SESSION) -> str:
    """Chemin de session d'une réponse de requête : le `session_handle` du
    résultat s'il est là, sinon dérivation `…/request/<expéditeur>/<jeton>`
    → `…/session/<expéditeur>/<jeton_session>` (défense anti-course)."""
    poignee = resultats.get("session_handle")
    if isinstance(poignee, str) and poignee:
        return poignee
    racine, _sep, reste = req.partition("/request/")
    expediteur = reste.rsplit("/", 1)[0]
    return f"{racine}/session/{expediteur}/{token_session}"


def opts_bind(session: str,
              entrees: "list[tuple[str, str, str]]") -> GLib.Variant:
    """Options de BindShortcuts — type `(oa(sa{sv})sa{sv})` : chaque entrée
    `(id, description, preferred_trigger)` devient la struct `(id, options)`.
    Le troisième champ est la fenêtre X11 cible (`""` = indifférente)."""
    raccourcis = [
        (ident, {
            "description": GLib.Variant("s", description),
            "preferred_trigger": GLib.Variant("s", declencheur),
        })
        for ident, description, declencheur in entrees
    ]
    return GLib.Variant("(oa(sa{sv})sa{sv})", (
        session, raccourcis, "",
        {"handle_token": GLib.Variant("s", TOKEN_BIND)},
    ))


class RaccourciPortal:
    """Chaîne asynchrone CreateSession → BindShortcuts → Activated.

    Toute l'API est en callbacks (jamais `call_sync`, qui figerait l'UI
    pendant le dialogue KDE de confirmation). Contraintes figées au spike :
    - une seule tentative de bind par session (spec) ;
    - les signaux `Activated`/`Response` arrivent sur des chemins précis —
      le `Response` de chaque requête sur le chemin PRÉVISIBLE de la requête
      (abonné avant l'appel), `Activated` sur le chemin de BASE ;
    - tout échec (refus, portail absent, dialogue jamais validé) appelle
      `on_echec` UNE seule fois — l'overlay sort, jamais de processus muet."""

    def __init__(self, on_toggle: callable, on_echec: callable):
        self.on_toggle = on_toggle
        self.on_echec = on_echec
        self.bus: "Gio.DBusConnection | None" = None
        self._expediteur = ""
        self._session: "str | None" = None
        self._lie = False
        self._echec_fait = False
        self._veille_id: "int | None" = None

    def demarrer(self) -> None:
        """Lance la chaîne : veille puis bus → abonnements → CreateSession.
        La veille (45 s) part DÈS LE DÉPART : toute la chaîne est bornée,
        y compris un portail jamais répondant — aucune attente muette."""
        self._veille_id = GLib.timeout_add_seconds(VEILLE_SECONDES, self._veille_ecoulee)
        Gio.bus_get(Gio.BusType.SESSION, None, self._bus_pret)

    # ------------------------------------------------------------- bus prêt

    def _bus_pret(self, _source, res) -> None:
        try:
            self.bus = Gio.bus_get_finish(res)
        except GLib.Error as err:
            self._echouer(f"{tr('Shortcut unavailable — the overlay has stopped.')} ({err.message})")
            return
        self._expediteur = self.bus.get_unique_name().lstrip(":").replace(".", "_")
        self.bus.signal_subscribe(
            None, IFACE, "Activated", CHEMIN, None,
            Gio.DBusSignalFlags.NONE, self._sur_activated)
        self.bus.signal_subscribe(
            None, IFACE_REQUETE, "Response",
            f"{CHEMIN}/request/{self._expediteur}/{TOKEN_REQUETE}", None,
            Gio.DBusSignalFlags.NONE, self._sur_reponse_create)
        self.bus.call(
            BUS, CHEMIN, IFACE, "CreateSession", opts_create(), None,
            Gio.DBusCallFlags.NONE, -1, None, self._create_fini)

    def _create_fini(self, _proxy, res) -> None:
        try:
            self.bus.call_finish(res)  # OUT = poignée de requête, déjà abonnée
        except GLib.Error as err:
            self._echouer(f"{tr('Shortcut unavailable — the overlay has stopped.')} ({err.message})")

    # --------------------------------------------------------- création OK

    def _sur_reponse_create(self, _bus, _sender, chemin, _iface, _membre, params) -> None:
        code, resultats = params.unpack()
        if code != 0:
            self._echouer(tr("Shortcut unavailable — the overlay has stopped."))
            return
        session = session_depuis_reponse(chemin, resultats)
        self._session = session
        self.bus.signal_subscribe(
            None, IFACE_REQUETE, "Response",
            f"{CHEMIN}/request/{self._expediteur}/{TOKEN_BIND}", None,
            Gio.DBusSignalFlags.NONE, self._sur_reponse_bind)
        # Bind une seule fois par session : jamais de re-liaison.
        self.bus.call(
            BUS, CHEMIN, IFACE, "BindShortcuts",
            opts_bind(session, [("overlay", tr("Open/close overlay"), "SHIFT+Tab")]),
            None, Gio.DBusCallFlags.NONE, -1, None, self._bind_fini)

    def _bind_fini(self, _proxy, res) -> None:
        try:
            self.bus.call_finish(res)
        except GLib.Error as err:
            self._echouer(f"{tr('Shortcut unavailable — the overlay has stopped.')} ({err.message})")

    def _sur_reponse_bind(self, _bus, _sender, _chemin, _iface, _membre, params) -> None:
        code, _resultats = params.unpack()
        self._desveiller()
        if code != 0:
            self._echouer(tr("Shortcut unavailable — the overlay has stopped."))
            return
        self._lie = True

    # -------------------------------------------------------------- signaux

    def _sur_activated(self, _bus, _sender, _chemin, _iface, _membre, params) -> None:
        # Signal diffusé à tous les abonnés : seul NOTRE session de portal
        # (argument 0) compte — d'autres apps ont leurs propres raccourcis.
        if not self._lie:
            return
        if params.unpack()[0] == self._session:
            self.on_toggle()

    # --------------------------------------------------------------- veille

    def _veille_ecoulee(self) -> bool:
        self._veille_id = None
        self._echouer(tr("The shortcut dialog was not confirmed."))
        return False

    def _desveiller(self) -> None:
        if self._veille_id is not None:
            GLib.source_remove(self._veille_id)
            self._veille_id = None

    def _echouer(self, message: str) -> None:
        """Appelé au plus une fois : notification puis sortie (spec)."""
        if self._echec_fait:
            return
        self._echec_fait = True
        self._desveiller()
        self.on_echec(message)
