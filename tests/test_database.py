import sqlite3

import pytest

from monarchbot.database import SCHEMA_VERSION, Database


def test_schema_initializes_idempotently_and_contains_core_tables(game):
    service, _, _ = game
    service.db.initialize()
    with service.db.reader() as con:
        version = con.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0]
        tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert version == str(SCHEMA_VERSION)
    assert {
        "players",
        "active_battles",
        "guilds",
        "tournament_entries",
        "market_listings",
        "banned_players",
        "player_companions",
    } <= tables


def test_shared_memory_database_survives_per_operation_connections():
    db = Database(":memory:")
    try:
        db.initialize()
        with db.transaction() as con:
            con.execute("INSERT INTO players(telegram_id,display_name) VALUES(1,'Test')")
        with db.reader() as con:
            assert con.execute("SELECT display_name FROM players WHERE telegram_id=1").fetchone()[0] == "Test"
    finally:
        db.close()


def test_unknown_schema_version_is_not_overwritten(tmp_path):
    path = tmp_path / "future.sqlite3"
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE schema_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
        con.execute("INSERT INTO schema_meta VALUES('schema_version','99')")
    db = Database(path)
    try:
        with pytest.raises(RuntimeError, match="not supported"):
            db.initialize()
        with db.reader() as con:
            assert (
                con.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0] == "99"
            )
    finally:
        db.close()


def test_only_one_active_battle_per_player_is_allowed(game):
    service, _, _ = game
    service.register(1, None, "Test")
    first = service.start_battle(1, "gate")
    with pytest.raises(sqlite3.IntegrityError), service.db.transaction() as con:
        con.execute(
            "INSERT INTO active_battles(id,player_id,dungeon_id,enemy_name,enemy_attack,enemy_hp,enemy_max_hp,player_hp,player_max_hp,mana,max_mana,started_at,expires_at) VALUES('duplicate',1,'gate','Enemy',1,1,1,1,1,1,1,1,1000)"
        )
    assert service.battle_status(1)["id"] == first["id"]
