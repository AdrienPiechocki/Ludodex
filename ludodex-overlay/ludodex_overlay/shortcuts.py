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
from gi.repository import GLib

TOKEN_REQUETE = "ludodexoverlay"   # handle_token de CreateSession
TOKEN_SESSION = "ludodexsession"   # session_handle_token de CreateSession
TOKEN_BIND = "ludodexbind"         # handle_token de BindShortcuts


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
