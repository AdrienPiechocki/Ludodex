import sqlite3, stat

from core import db as D, keystore, ra, steam
from core.migrations import migrate


def _con():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    migrate(c)
    return c


def test_keys_go_to_private_files_not_the_database():
    c = _con()
    steam.set_steam_credentials(c, " STEAMKEY123 ", "76561190000000000")
    ra.set_ra_credentials(c, "bob", "RAKEY456")
    assert steam.steam_api_key(c) == "STEAMKEY123" and ra.ra_key(c) == "RAKEY456"
    assert steam.steam_id_setting(c) == "76561190000000000" and ra.ra_user(c) == "bob"
    keys = [r[0] for r in c.execute("SELECT key FROM settings")]
    assert "steam_api_key" not in keys and "ra_key" not in keys
    for name in ("steam_api_key", "ra_key"):
        assert stat.S_IMODE(keystore.path(name).stat().st_mode) == 0o600
    steam.set_steam_credentials(c, "", "")
    assert steam.steam_api_key(c) == "" and not keystore.path("steam_api_key").exists()
    ra.set_ra_credentials(c, "", "")


def test_legacy_database_keys_are_moved_then_erased():
    c = _con()
    c.executemany("INSERT INTO settings VALUES (?,?)", [("steam_api_key", "OLDSTEAM"), ("ra_key", "OLDRA"), ("lang", "fr")])
    assert sorted(keystore.migrate_from_db(c)) == ["ra_key", "steam_api_key"]
    assert [r[0] for r in c.execute("SELECT key FROM settings")] == ["lang"]
    assert steam.steam_api_key(c) == "OLDSTEAM" and ra.ra_key(c) == "OLDRA"
    assert keystore.migrate_from_db(c) == []                       # idempotent
    steam.set_steam_credentials(c, "", ""); ra.set_ra_credentials(c, "", "")


def test_existing_file_wins_over_stale_database_value():
    c = _con()
    keystore.write("ra_key", "NEWER")
    c.execute("INSERT INTO settings VALUES ('ra_key', 'STALE')")
    keystore.migrate_from_db(c)
    assert ra.ra_key(c) == "NEWER" and not c.execute("SELECT 1 FROM settings WHERE key='ra_key'").fetchone()
    ra.set_ra_credentials(c, "", "")
