import random

import pytest

from core import pick


def _pool(n):
    return [({"id": i, "name": f"g{i}"}, 1.0) for i in range(1, n + 1)]


@pytest.mark.parametrize("n", [2, 3, 5, 23, 60])
def test_strip_has_constant_length_and_winner_at_rank(n):
    for seed in range(20):
        winner = random.Random(seed).randint(1, n)
        bande, rang = pick.roulette_strip_rang(_pool(n), winner, 45, random.Random(seed))
        assert len(bande) == 45 and rang == 42 and bande[rang]["id"] == winner
        ids = [g["id"] for g in bande]
        assert all(a != b for a, b in zip(ids, ids[1:]))        # jamais deux fois la même jaquette de suite
        assert set(ids) <= set(range(1, n + 1))


def test_two_games_alternate():
    bande, rang = pick.roulette_strip_rang(_pool(2), 1, 45, random.Random(0))
    assert [g["id"] for g in bande[:4]] in ([1, 2, 1, 2], [2, 1, 2, 1]) and {g["id"] for g in bande} == {1, 2}


def test_single_game_and_empty_pool():
    assert pick.roulette_strip(_pool(1), 1) == [{"id": 1, "name": "g1"}]
    assert pick.roulette_strip([], 1) == []


def test_deja_visibles_does_not_break_rank():
    bande, rang = pick.roulette_strip_rang(_pool(3), 2, 45, random.Random(1), deja_visibles=(1, 3))
    assert len(bande) == 45 and bande[rang]["id"] == 2
