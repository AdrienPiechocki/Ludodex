"""tests/test_shortcuts.py — recettes de variantes D-Bus du portail (sans bus).

Ces tests figent les signatures réellement implémentées par le daemon
(GlobalShortcuts) et les pièges découverts au spike : les deux jetons de
CreateSession, la struct du BindShortcuts et la dérivation du chemin de session.
"""
from gi.repository import GLib

from ludodex_overlay import shortcuts


def test_opts_create_contient_les_deux_tokens():
    v = shortcuts.opts_create()
    assert v.get_type_string() == "(a{sv})"
    d = v.unpack()[0]
    assert set(d) == {"handle_token", "session_handle_token"}
    assert d["handle_token"] == "ludodexoverlay"


def test_opts_bind_a_la_signature_reelle_du_daemon():
    v = shortcuts.opts_bind("/session/1_42/ludodexsession",
                            [("overlay", "Ouvrir/fermer", "SHIFT+Tab")])
    assert v.get_type_string() == "(oa(sa{sv})sa{sv})"
    session, raccourcis, fenetre, opts = v.unpack()
    assert session == "/session/1_42/ludodexsession"
    assert fenetre == "" and opts["handle_token"] == "ludodexbind"
    rid, ropts = raccourcis[0]
    assert rid == "overlay"
    assert ropts["description"] == "Ouvrir/fermer"
    assert ropts["preferred_trigger"] == "SHIFT+Tab"


def test_session_depuis_reponse_prefere_le_resultat():
    req = "/org/freedesktop/portal/desktop/request/1_42/ludodexoverlay"
    det = {"session_handle": "/org/freedesktop/portal/desktop/session/1_77/autre"}
    assert shortcuts.session_depuis_reponse(req, det) == det["session_handle"]


def test_session_depuis_reponse_derive_sans_resultat():
    req = "/org/freedesktop/portal/desktop/request/1_42/ludodexoverlay"
    assert shortcuts.session_depuis_reponse(req, {}) == \
        "/org/freedesktop/portal/desktop/session/1_42/ludodexsession"
