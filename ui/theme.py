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
.retro-section { background-color: alpha(currentColor, 0.05); border-radius: 10px; padding: 10px 12px; }
.retro-card { background-color: alpha(currentColor, 0.07); border-radius: 8px; padding: 8px; }
.retro-delta-up { color: @success_color; }
.retro-delta-down { color: @error_color; }
.retro-bar { background-color: alpha(@accent_bg_color, 0.55); border-radius: 3px 3px 0 0; }
.retro-bar.peak { background-color: @accent_bg_color; }
.retro-bar.hot { background-color: alpha(@accent_bg_color, 0.85); }
.retro-bar.peak.hot { background-color: @accent_bg_color; }
.retro-dot-0 { background-color: #614d8f; border-radius: 5px; }
.retro-dot-1 { background-color: #4f9e6e; border-radius: 5px; }
.retro-dot-2 { background-color: #c9a227; border-radius: 5px; }
.retro-dot-3 { background-color: #c96a3d; border-radius: 5px; }
.retro-dot-4 { background-color: #4f7fc9; border-radius: 5px; }
.retro-dot-other { background-color: #55555f; border-radius: 5px; }
.retro-time { font-size: 11px; }
.retro-row { border-radius: 6px; padding: 3px 6px; }
.retro-row.hot { background-color: alpha(currentColor, 0.10); }
.retro-hm-0 { background-color: alpha(currentColor, 0.08); border-radius: 2px; }
.retro-hm-1 { background-color: alpha(@accent_bg_color, 0.30); border-radius: 2px; }
.retro-hm-2 { background-color: alpha(@accent_bg_color, 0.55); border-radius: 2px; }
.retro-hm-3 { background-color: alpha(@accent_bg_color, 0.80); border-radius: 2px; }
.retro-hm-4 { background-color: @accent_bg_color; border-radius: 2px; }
.retro-future { background-color: transparent; border-radius: 2px; }

/* Barres de complétion du module Succès (jeu dans la liste, succès à étapes).
   Voir ui/achievements.py : la classe doit être posée par `add_css_class`,
   GTK4 ne peint pas une barre à laquelle on a effacé sa classe `horizontal`. */
.ach-bar { min-height: 6px; }
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
