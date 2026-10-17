"""placement.py — logique pure de positionnement des widgets.

Aimantation douce aux bords et coins de l'écran, bornage dans l'écran et
conversions fraction <-> pixel : les positions persistées sont des fractions
de l'écran, donc valables quelle que soit la sortie choisie.
"""
import typing

SEUIL_AIMANTATION = 24  # px : au-delà, le bord n'attire plus


def aimanter(x: float, y: float, largeur: float, hauteur: float,
             elargeur: float, ehauteur: float) -> typing.Tuple[float, float]:
    """Colle (x, y) sur le bord ou le coin le plus proche s'il est dans le seuil."""
    if abs(x) <= SEUIL_AIMANTATION:
        x = 0.0
    elif abs(x - (elargeur - largeur)) <= SEUIL_AIMANTATION:
        x = float(elargeur - largeur)
    if abs(y) <= SEUIL_AIMANTATION:
        y = 0.0
    elif abs(y - (ehauteur - hauteur)) <= SEUIL_AIMANTATION:
        y = float(ehauteur - hauteur)
    return (x, y)


def borner(x: float, y: float, largeur: float, hauteur: float,
           elargeur: float, ehauteur: float) -> typing.Tuple[float, float]:
    """Clamp la position dans l'écran — un widget ne dépasse jamais un bord."""
    x = min(max(x, 0.0), float(elargeur - largeur))
    y = min(max(y, 0.0), float(ehauteur - hauteur))
    return (x, y)


def ajuster(x: float, y: float, largeur: float, hauteur: float,
            elargeur: float, ehauteur: float) -> typing.Tuple[float, float]:
    """Aimantation puis bornage : un widget visé sur un bord n'en sort jamais,
    même si l'écran a rétréci depuis la dernière sauvegarde."""
    return borner(*aimanter(x, y, largeur, hauteur, elargeur, ehauteur),
                  largeur, hauteur, elargeur, ehauteur)


def frac_vers_px(fx: float, fy: float, elargeur: float, ehauteur: float) -> typing.Tuple[float, float]:
    """Position fractionnaire (persistance) -> pixel (rendu)."""
    return (fx * elargeur, fy * ehauteur)


def px_vers_frac(x: float, y: float, elargeur: float, ehauteur: float) -> typing.Tuple[float, float]:
    """Position pixel (rendu) -> fraction (persistance)."""
    return (x / elargeur, y / ehauteur)


def pas_de_drag(marge_x: float, marge_y: float, dx: float, dy: float,
                largeur: float, hauteur: float,
                elargeur: float, ehauteur: float) -> typing.Tuple[int, int]:
    """Prochaines marges à appliquer pendant un drag GTK4.

    Les offsets `drag-update` sont livrés dans le repère du WIDGET, qui
    bouge dès qu'on applique une marge : partir de la marge COURANTE (et
    non du point de départ) replace le point de préhension exactement sous
    le pointeur à chaque event — l'ancienne base faisait traîner le widget
    derrière la souris (constat du 2026-10-17)."""
    x, y = ajuster(marge_x + dx, marge_y + dy, largeur, hauteur, elargeur, ehauteur)
    return (int(x), int(y))
