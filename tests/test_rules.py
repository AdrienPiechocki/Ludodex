import glob
import pathlib
import sqlite3
import textwrap

import pytest

from core import rules, tracker
from core.migrations import migrate

ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture
def env(tmp_path):
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    migrate(con)
    log = tmp_path / "game.log"
    log.write_text("")

    def setup(yaml_text):
        y = tmp_path / "a.yaml"
        y.write_text(textwrap.dedent(yaml_text))
        tracker.set_tracker(con, 1, y, log)

    def add(*lines):
        with open(log, "a") as f:
            f.write("\n".join(lines) + "\n")

    def scan(now=1000):
        return {a["id"]: a for a in tracker.scan(con, 1, now=now)}

    return setup, add, scan


def test_all_community_yaml_still_load():
    files = glob.glob(str(ROOT / "achievements" / "**" / "*.yaml"), recursive=True)
    assert files
    for f in files:
        assert tracker.load_tracker(f)


def test_patterns_keep_their_trailing_space():
    d = rules.compile_def({"name": "x", "any": ["item=189 "]}, 0)
    assert d["regexes"][0].search("item=189 foo") and not d["regexes"][0].search("item=1890")


def test_sequence_requires_order(env):
    setup, add, scan = env
    setup("""
    achievements:
      - {name: Ritual, sequence: ['lit torch', 'open door', 'kill boss']}
    """)
    add("kill boss", "open door", "lit torch")
    assert scan()["ritual"]["progress"] == 1            # seule la 1re étape est valide
    add("kill boss")
    a = scan()["ritual"]
    assert a["progress"] == 1 and not a["done"]
    add("open door", "noise", "kill boss")
    a = scan()["ritual"]
    assert a["done"] and a["progress"] == 3


def test_distinct_counts_unique_values_across_scans(env):
    setup, add, scan = env
    setup(r"""
    achievements:
      - {name: Explorer, distinct: 'Entered (?P<v>\w+)', count: 3}
    """)
    add("Entered Ald", "Entered Ald", "Entered Balmora")
    assert scan()["explorer"]["progress"] == 2
    add("Entered Balmora", "Entered Caldera")
    a = scan()["explorer"]
    assert a["done"] and a["progress"] == 3


def test_sum_and_number_formats(env):
    setup, add, scan = env
    setup(r"""
    achievements:
      - {name: Rich, sum: 'Gold \+(\d[\d ,.]*)', count: 1000}
    """)
    add("Gold +400", "Gold +1,5")
    a = scan()["rich"]
    assert not a["done"]
    add("Gold +599")
    assert scan()["rich"]["done"]
    assert rules.number("1 234") == 1234 and rules.number("1,234") == 1234
    assert rules.number("1.234,5") == 1234.5 and rules.number("3,5") == 3.5
    assert rules.number("abc") is None


def test_max_keeps_best(env):
    setup, add, scan = env
    setup(r"""
    achievements:
      - {name: High, max: 'Combo x(\d+)', count: 50}
    """)
    add("Combo x20", "Combo x7")
    assert scan()["high"]["progress"] == 20
    add("Combo x55")
    assert scan()["high"]["done"]


def test_reset_on_and_exclude(env):
    setup, add, scan = env
    setup("""
    achievements:
      - {name: Streak, match: 'kill', count: 3, reset_on: 'You died', exclude: 'training'}
    """)
    add("kill", "kill", "kill training", "You died", "kill")
    assert scan()["streak"]["progress"] == 1
    add("kill", "kill")
    assert scan()["streak"]["done"]


def test_requires_sees_unlock_from_same_batch(env):
    setup, add, scan = env
    setup("""
    achievements:
      - {name: Boss, match: 'boss down'}
      - {name: Cleanup, match: 'kill', count: 2, requires: [boss]}
    """)
    add("kill", "boss down", "kill", "kill")           # le 1er « kill » précède le boss : ignoré
    a = scan()
    assert a["boss"]["done"] and a["cleanup"]["done"]


def test_meta_all_of_any_of(env):
    setup, add, scan = env
    setup("""
    achievements:
      - {name: A, match: 'a'}
      - {name: B, match: 'b'}
      - {name: C, match: 'c'}
      - {name: Trio, all_of: [a, b, c]}
      - {name: Duo, any_of: [a, b, c], count: 2}
    """)
    add("a", "c")
    a = scan()
    assert a["duo"]["done"] and not a["trio"]["done"] and a["trio"]["progress"] == 2
    add("b")
    assert scan()["trio"]["done"]


def test_legacy_all_incremental(env):
    setup, add, scan = env
    setup("""
    achievements:
      - {name: Both, all: ['x=1', 'y=2']}
    """)
    add("x=1")
    assert scan()["both"]["progress"] == 1
    add("y=2")
    assert scan()["both"]["done"]


@pytest.mark.parametrize("snippet, msg", [
    ("- {name: A, sum: 'x(\\d+)'}", "seuil"),
    ("- {name: A, sum: 'x\\d+', count: 5}", "groupe"),
    ("- {name: A, match: a, sequence: [a, b]}", "seule règle"),
    ("- {name: A, all_of: [nope]}", "inconnu"),
    ("- {name: A, all_of: [b]}\n  - {name: B, all_of: [a]}", "circulaire"),
    ("- {name: A, match: '('}", "invalide"),
    ("- {name: A, match: a, count: 0}", "entier"),
])
def test_validation_errors(tmp_path, snippet, msg):
    y = tmp_path / "a.yaml"
    y.write_text("achievements:\n  " + snippet + "\n")
    with pytest.raises(ValueError, match=msg):
        tracker.load_tracker(y)


def test_lint_and_header():
    data = {"game": {"name": "G", "steam_appid": "42", "aliases": "g"},
            "achievements": [{"name": "A", "match": "x", "cuont": 3}, {"name": "B", "match": ".*"}]}
    w = rules.lint(data)
    assert any("cuont" in x for x in w) and any("ligne vide" in x for x in w)
    assert rules.parse_header(data) == {"name": "G", "steam_appid": 42, "aliases": ["g"]}
