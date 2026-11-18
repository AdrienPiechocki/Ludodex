import hashlib
import json
import pathlib
import shutil

import pytest

from core import community, tracker
from core.migrations import migrate

ROOT = pathlib.Path(__file__).resolve().parent.parent
GOOD = """game:
  name: Test Game
  aliases: [TG]
  steam_appid: 4242
  version: "1.2"
achievements:
  - {name: First, description: d, match: 'start'}
"""


@pytest.fixture
def repo(tmp_path, monkeypatch):
    r = tmp_path / "repo"
    (r / "testgame").mkdir(parents=True)
    (r / "testgame" / "testgame-achievements.yaml").write_text(GOOD)
    (r / "testgame" / "Logger.zip").write_bytes(b"zipdata")
    (r / "testgame" / "README.md").write_text("hi")
    index, _ = community.build_index(r)
    (r / "index.json").write_text(community.index_json(index))
    monkeypatch.setenv("LUDODEX_REPO", str(r))
    monkeypatch.setattr(community, "install_dir", lambda: tmp_path / "installed")
    return r


def test_index_contents(repo):
    (e,) = community.load_index()
    assert (e["id"], e["name"], e["count"], e["steam_appid"]) == ("testgame", "Test Game", 1, 4242)
    assert [x["path"] for x in e["extras"]] == ["testgame/Logger.zip"]        # README exclu
    assert e["sha256"] == hashlib.sha256((repo / e["yaml"]).read_bytes()).hexdigest()


def test_search_and_match():
    idx = [{"id": "votv", "name": "Voices of the Void", "aliases": ["VotV"], "steam_appid": 1}]
    assert community.search(idx, "voices") and community.search(idx, "VOTV") and not community.search(idx, "zzz")
    assert community.match_game(idx, {"source": "custom", "ext_id": "x", "name": "voices of the void"})
    assert community.match_game(idx, {"source": "steam", "ext_id": "1", "name": "Whatever"})
    assert community.match_game(idx, {"source": "steam", "ext_id": "2", "name": "Other"}) is None


def test_install_and_update_cycle(repo):
    (e,) = community.load_index()
    path = community.install(e, with_extras=True)
    assert path.read_text() == GOOD and (path.parent / "Logger.zip").exists()
    assert tracker.load_tracker(path)
    assert community.installed()["testgame"]["version"] == "1.2"
    assert community.outdated(community.load_index()) == []
    (repo / "testgame" / "testgame-achievements.yaml").write_text(GOOD.replace('"1.2"', '"1.3"'))
    index, _ = community.build_index(repo)
    (repo / "index.json").write_text(community.index_json(index))
    (old,) = community.outdated(community.load_index())
    community.install(old)
    assert community.installed()["testgame"]["version"] == "1.3"
    assert community.uninstall("testgame") and not community.installed()


def test_tampered_file_is_refused_and_previous_kept(repo):
    (e,) = community.load_index()
    path = community.install(e)
    (repo / "testgame" / "testgame-achievements.yaml").write_text(GOOD + "# tampered\n")   # index non régénéré
    with pytest.raises(community.CommunityError, match="Checksum"):
        community.install(e)
    assert path.read_text() == GOOD


def test_invalid_yaml_never_installed(repo):
    bad = "achievements:\n  - {name: Broken, sum: 'x(\\d+)'}\n"            # sum sans count
    sha = hashlib.sha256(bad.encode()).hexdigest()
    (repo / "testgame" / "testgame-achievements.yaml").write_text(bad)
    e = dict(community.load_index()[0], sha256=sha)
    with pytest.raises(community.CommunityError, match="Invalid"):
        community.install(e)
    assert "testgame" not in community.installed()


def test_bad_index(tmp_path, monkeypatch):
    (tmp_path / "index.json").write_text(json.dumps({"schema": 99, "games": []}))
    with pytest.raises(community.CommunityError):
        community.load_index(str(tmp_path))
    with pytest.raises(community.CommunityError):
        community.load_index(str(tmp_path / "nope"))


def test_build_rejects_invalid_yaml(tmp_path):
    (tmp_path / "g").mkdir()
    (tmp_path / "g" / "g.yaml").write_text("achievements:\n  - {name: X}\n")
    with pytest.raises(ValueError):
        community.build_index(tmp_path)


def test_committed_index_is_up_to_date():
    """Garde-fou CI : achievements/index.json doit refléter les YAML du dépôt."""
    index, _ = community.build_index(ROOT / "achievements")
    assert (ROOT / "achievements" / "index.json").read_text(encoding="utf-8") == community.index_json(index)


def test_every_committed_entry_installs_from_local_repo(tmp_path, monkeypatch):
    monkeypatch.setattr(community, "install_dir", lambda: tmp_path / "installed")
    base = str(ROOT / "achievements")
    for e in community.load_index(base):
        assert tracker.load_tracker(community.install(e, with_extras=True, base=base))


def test_empty_query_lists_everything_and_non_latin_names_dont_collide():
    idx = [{"id": "a", "name": "日本語", "aliases": []}, {"id": "b", "name": "한국어", "aliases": []}]
    assert len(community.search(idx, "")) == 2
    assert [e["id"] for e in community.search(idx, "日本")] == ["a"]
