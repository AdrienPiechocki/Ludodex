"""tests/test_placement.py — aimantation, bornage et conversions de position."""
from ludodex_overlay import placement


def test_aimantation_aux_bords_et_coins():
    # 10 px du bord gauche → collé ; au milieu → inchangé
    assert placement.aimanter(10, 500, 100, 60, 1920, 1080) == (0, 500)
    assert placement.aimanter(1810, 500, 100, 60, 1920, 1080) == (1820, 500)
    assert placement.aimanter(900, 500, 100, 60, 1920, 1080) == (900, 500)


def test_aimantation_aux_coins():
    assert placement.aimanter(8, 1030, 100, 60, 1920, 1080) == (0, 1020)


def test_bornage_dans_lecran():
    assert placement.borner(1900, 1070, 100, 60, 1920, 1080) == (1820, 1020)
    assert placement.borner(-50, -10, 100, 60, 1920, 1080) == (0, 0)


def test_ajuster_ne_sort_jamais_de_lecran():
    x, y = placement.ajuster(1900, 500, 100, 60, 1920, 1080)
    assert 0 <= x <= 1820 and 0 <= y <= 1020


def test_fractions():
    assert placement.frac_vers_px(0.5, 0.5, 1920, 1080) == (960, 540)
    assert placement.px_vers_frac(960, 540, 1920, 1080) == (0.5, 0.5)


def test_drag_suit_le_pointeur_meme_si_le_widget_bouge():
    """Reproduction du feedback GTK4 : les offsets `drag-update` sont livrés
    dans le repère du WIDGET, qui bouge à chaque marge appliquée — partir
    de la marge COURANTE place le point de préhension sous le pointeur à
    chaque event, alors que l'ancienne base (point de départ) traîne."""
    surface_presse, marge0, p0 = 150.0, 50.0, 100.0  # appui à x_widget=100
    marge = marge0
    traces = []
    for surface_souris in (200.0, 300.0, 420.0):
        # x_widget du pointeur = surface - marge ACTUELLE (le widget a bougé)
        dx = (surface_souris - marge) - p0
        marge = placement.pas_de_drag(marge, 0.0, dx, 0.0, 100, 60, 1920, 1080)[0]
        traces.append(marge)
    assert traces == [100, 200, 320]  # collé au pointeur à chaque pas
