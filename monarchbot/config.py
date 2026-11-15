from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    bot_token: str = field(repr=False)
    database_path: str
    admin_ids: frozenset[int]
    log_level: str = "INFO"


def load_settings() -> Settings:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            "BOT_TOKEN is missing. Copy .env.example to .env and set your bot token privately."
        )
    raw_admins = os.getenv("ADMIN_IDS", "")
    try:
        admins = frozenset(int(value.strip()) for value in raw_admins.split(",") if value.strip())
    except ValueError as exc:
        raise RuntimeError("ADMIN_IDS must be comma-separated numeric Telegram IDs.") from exc
    if any(user_id <= 0 for user_id in admins):
        raise RuntimeError("ADMIN_IDS must contain positive Telegram user IDs.")
    database_path = os.getenv("DATABASE_PATH", "data/monarch.sqlite3").strip() or "data/monarch.sqlite3"
    if database_path != ":memory:":
        database_path = str(Path(database_path).expanduser())
        Path(database_path).parent.mkdir(parents=True, exist_ok=True)
    return Settings(
        bot_token=token,
        database_path=database_path,
        admin_ids=admins,
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
    )
