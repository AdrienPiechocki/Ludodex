"""theme.py — constantes visuelles et feuille de style de l'interface GTK."""
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, GObject, Gtk, Pango

import picker as P
from i18n import available, get_lang, tr

STATUTS = list(P.STATUS_WEIGHT)  # en_cours, backlog, pause, abandonne, termine
DUREES = [None, 15, 30, 60, 120]
TILE_W, TILE_H = 150, 225  # pochette 2:3 de la grille de la bibliothèque
COVER_PX = 320             # hauteur (px) des textures de couverture gardées en mémoire

CSS = """
.game-placeholder { color: white; }
.ph-0 { background: linear-gradient(160deg, #3584e4, #1a5fb4); }
.ph-1 { background: linear-gradient(160deg, #2ec27e, #1a7a4c); }
.ph-2 { background: linear-gradient(160deg, #9141ac, #613583); }
.ph-3 { background: linear-gradient(160deg, #e66100, #a2420a); }
.ph-4 { background: linear-gradient(160deg, #e01b24, #a51d2d); }
.ph-5 { background: linear-gradient(160deg, #2190a4, #0e5560); }
.game-title-bar { background: linear-gradient(to bottom, transparent, rgba(0, 0, 0, 0.8));
                  color: white; font-weight: bold; padding: 22px 8px 7px 8px; }
.dim-tile { opacity: 0.45; }
.navigation-sidebar row.col-sep { min-height: 0; padding: 0; background: transparent; }
.navigation-sidebar row.col-sep separator { margin: 3px 8px; }
.navigation-sidebar row.col-on { background-color: @accent_bg_color; color: @accent_fg_color; }
.navigation-sidebar row.col-on:hover { background-color: @accent_bg_color; }
.retro-col { border-radius: 8px; padding: 6px; background-color: alpha(currentColor, 0.06); }
.retro-col.today { background-color: @accent_bg_color; color: @accent_fg_color; }
.retro-col.today .retro-cell { background-color: alpha(currentColor, 0.16); }
.retro-cell { border-radius: 6px; padding: 4px 6px; margin-top: 6px; background-color: alpha(currentColor, 0.08); }
"""
_css_done = False


def _install_css():
    global _css_done
    if _css_done:
        return
    prov = Gtk.CssProvider()
    if hasattr(prov, "load_from_string"):  # GTK >= 4.12
        prov.load_from_string(CSS)
    else:
        prov.load_from_data(CSS.encode())
    Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    _css_done = True
