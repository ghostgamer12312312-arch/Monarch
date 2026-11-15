from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 1


class Database:
    def __init__(self, path: str | Path):
        raw = str(path)
        self._uri = raw == ":memory:"
        if self._uri:
            self.path = f"file:monarch-{uuid.uuid4().hex}?mode=memory&cache=shared"
            self._anchor = self._connect()
        else:
            expanded = Path(raw).expanduser()
            expanded.parent.mkdir(parents=True, exist_ok=True)
            self.path = str(expanded)
            self._anchor = None

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=30, isolation_level=None, uri=self._uri)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        con.execute("PRAGMA busy_timeout = 30000")
        if not self._uri:
            con.execute("PRAGMA journal_mode = WAL")
            con.execute("PRAGMA synchronous = NORMAL")
        return con

    @contextmanager
    def reader(self) -> Iterator[sqlite3.Connection]:
        con = self._connect()
        try:
            yield con
        finally:
            con.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        con = self._connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def initialize(self) -> None:
        con = self._connect()
        try:
            existing = con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_meta'"
            ).fetchone()
            if existing:
                version = con.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
                if version is not None and version["value"] != str(SCHEMA_VERSION):
                    raise RuntimeError(
                        f"Database schema version {version['value']} is not supported by this build (expected {SCHEMA_VERSION}). Back up the database before upgrading."
                    )
            con.executescript(
                """
                BEGIN IMMEDIATE;
                CREATE TABLE IF NOT EXISTS schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS players (
                    telegram_id INTEGER PRIMARY KEY CHECK(telegram_id > 0),
                    username TEXT,
                    display_name TEXT NOT NULL,
                    level INTEGER NOT NULL DEFAULT 1 CHECK(level >= 1),
                    xp INTEGER NOT NULL DEFAULT 0 CHECK(xp >= 0),
                    coins INTEGER NOT NULL DEFAULT 100 CHECK(coins >= 0),
                    essence INTEGER NOT NULL DEFAULT 0 CHECK(essence >= 0),
                    energy INTEGER NOT NULL DEFAULT 100 CHECK(energy >= 0),
                    max_energy INTEGER NOT NULL DEFAULT 100 CHECK(max_energy >= 1),
                    class_id TEXT NOT NULL DEFAULT 'vanguard',
                    class_locked INTEGER NOT NULL DEFAULT 0 CHECK(class_locked IN (0,1)),
                    equipped_companion TEXT NOT NULL DEFAULT 'ash_hound',
                    daily_date TEXT,
                    daily_streak INTEGER NOT NULL DEFAULT 0 CHECK(daily_streak >= 0),
                    last_raid_at INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_players_leaderboard ON players(level DESC, xp DESC, coins DESC);
                CREATE TABLE IF NOT EXISTS banned_players (
                    telegram_id INTEGER PRIMARY KEY REFERENCES players(telegram_id) ON DELETE CASCADE,
                    reason TEXT NOT NULL,
                    banned_by INTEGER NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS inventory (
                    player_id INTEGER NOT NULL REFERENCES players(telegram_id) ON DELETE CASCADE,
                    item_id TEXT NOT NULL,
                    quantity INTEGER NOT NULL DEFAULT 0 CHECK(quantity >= 0),
                    PRIMARY KEY(player_id, item_id)
                );
                CREATE TABLE IF NOT EXISTS player_companions (
                    player_id INTEGER NOT NULL REFERENCES players(telegram_id) ON DELETE CASCADE,
                    companion_id TEXT NOT NULL,
                    level INTEGER NOT NULL DEFAULT 1 CHECK(level BETWEEN 1 AND 20),
                    fragments INTEGER NOT NULL DEFAULT 0 CHECK(fragments BETWEEN 0 AND 2),
                    PRIMARY KEY(player_id, companion_id)
                );
                CREATE TABLE IF NOT EXISTS active_battles (
                    id TEXT PRIMARY KEY,
                    player_id INTEGER NOT NULL REFERENCES players(telegram_id) ON DELETE CASCADE,
                    dungeon_id TEXT NOT NULL,
                    enemy_name TEXT NOT NULL,
                    enemy_attack INTEGER NOT NULL CHECK(enemy_attack >= 0),
                    enemy_hp INTEGER NOT NULL CHECK(enemy_hp >= 0),
                    enemy_max_hp INTEGER NOT NULL CHECK(enemy_max_hp >= 1),
                    player_hp INTEGER NOT NULL CHECK(player_hp >= 0),
                    player_max_hp INTEGER NOT NULL CHECK(player_max_hp >= 1),
                    mana INTEGER NOT NULL CHECK(mana >= 0),
                    max_mana INTEGER NOT NULL CHECK(max_mana >= 0),
                    turn INTEGER NOT NULL DEFAULT 1 CHECK(turn >= 1),
                    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','victory','defeat','escaped','expired')),
                    result_json TEXT,
                    started_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_one_active_battle_per_player
                    ON active_battles(player_id) WHERE status = 'active';
                CREATE TABLE IF NOT EXISTS daily_quests (
                    player_id INTEGER NOT NULL REFERENCES players(telegram_id) ON DELETE CASCADE,
                    quest_date TEXT NOT NULL,
                    quest_id TEXT NOT NULL,
                    progress INTEGER NOT NULL DEFAULT 0 CHECK(progress >= 0),
                    claimed INTEGER NOT NULL DEFAULT 0 CHECK(claimed IN (0,1)),
                    PRIMARY KEY(player_id, quest_date, quest_id)
                );
                CREATE TABLE IF NOT EXISTS guilds (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    leader_id INTEGER NOT NULL REFERENCES players(telegram_id),
                    level INTEGER NOT NULL DEFAULT 1 CHECK(level >= 1),
                    xp INTEGER NOT NULL DEFAULT 0 CHECK(xp >= 0),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS guild_members (
                    player_id INTEGER PRIMARY KEY REFERENCES players(telegram_id) ON DELETE CASCADE,
                    guild_id INTEGER NOT NULL REFERENCES guilds(id) ON DELETE CASCADE,
                    joined_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_guild_members_guild ON guild_members(guild_id);
                CREATE TABLE IF NOT EXISTS raid_state (
                    id INTEGER PRIMARY KEY CHECK(id=1),
                    name TEXT NOT NULL,
                    hp INTEGER NOT NULL CHECK(hp >= 0),
                    max_hp INTEGER NOT NULL CHECK(max_hp >= 1),
                    cycle INTEGER NOT NULL DEFAULT 1 CHECK(cycle >= 1),
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS raid_contributions (
                    player_id INTEGER PRIMARY KEY REFERENCES players(telegram_id) ON DELETE CASCADE,
                    total_damage INTEGER NOT NULL DEFAULT 0 CHECK(total_damage >= 0),
                    hits INTEGER NOT NULL DEFAULT 0 CHECK(hits >= 0)
                );
                CREATE TABLE IF NOT EXISTS tournament_entries (
                    tournament_week TEXT NOT NULL,
                    player_id INTEGER NOT NULL REFERENCES players(telegram_id) ON DELETE CASCADE,
                    score INTEGER NOT NULL DEFAULT 0 CHECK(score >= 0),
                    PRIMARY KEY(tournament_week, player_id)
                );
                CREATE TABLE IF NOT EXISTS tournament_awards (
                    tournament_week TEXT NOT NULL,
                    player_id INTEGER NOT NULL REFERENCES players(telegram_id) ON DELETE CASCADE,
                    place INTEGER NOT NULL CHECK(place BETWEEN 1 AND 3),
                    prize INTEGER NOT NULL CHECK(prize >= 0),
                    claimed INTEGER NOT NULL DEFAULT 0 CHECK(claimed IN (0,1)),
                    PRIMARY KEY(tournament_week, player_id)
                );
                CREATE TABLE IF NOT EXISTS market_listings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    seller_id INTEGER NOT NULL REFERENCES players(telegram_id),
                    buyer_id INTEGER REFERENCES players(telegram_id),
                    item_id TEXT NOT NULL,
                    quantity INTEGER NOT NULL CHECK(quantity BETWEEN 1 AND 99),
                    price INTEGER NOT NULL CHECK(price BETWEEN 1 AND 1000000),
                    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','sold','cancelled','expired')),
                    expires_at INTEGER NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    sold_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_market_listings ON market_listings(status, expires_at, id);
                INSERT OR IGNORE INTO raid_state(id,name,hp,max_hp,cycle)
                    VALUES(1,'The Rift Sovereign',250000,250000,1);
                INSERT OR IGNORE INTO schema_meta(key,value) VALUES('schema_version','1');
                COMMIT;
                """
            )
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def close(self) -> None:
        if self._anchor is not None:
            self._anchor.close()
            self._anchor = None
