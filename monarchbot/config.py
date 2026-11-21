from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _parse_ids(raw: str | None) -> frozenset[int]:
    return frozenset(int(value.strip()) for value in (raw or "").split(",") if value.strip())


def _parse_chat_id(raw: str | None) -> int | str | None:
    value = (raw or "").strip()
    if not value:
        return None
    if value.lstrip("-").isdigit():
        return int(value)
    return value


@dataclass(frozen=True)
class Settings:
    bot_token: str = field(repr=False)
    database_path: str
    admin_ids: frozenset[int]
    owner_ids: frozenset[int] = frozenset()
    group_link: str = ""
    channel_link: str = ""
    support_link: str = ""
    query_group_id: int | str | None = None
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
    try:
        admins = _parse_ids(os.getenv("ADMIN_IDS", ""))
    except ValueError as exc:
        raise RuntimeError("ADMIN_IDS must be comma-separated numeric Telegram IDs.") from exc
    if any(user_id <= 0 for user_id in admins):
        raise RuntimeError("ADMIN_IDS must contain positive Telegram user IDs.")
    try:
        owners = _parse_ids(os.getenv("OWNER_IDS", "")) or admins
    except ValueError as exc:
        raise RuntimeError("OWNER_IDS must be comma-separated numeric Telegram IDs.") from exc
    if any(user_id <= 0 for user_id in owners):
        raise RuntimeError("OWNER_IDS must contain positive Telegram user IDs.")
    database_path = os.getenv("DATABASE_PATH", "data/monarch.sqlite3").strip() or "data/monarch.sqlite3"
    if database_path != ":memory:":
        database_path = str(Path(database_path).expanduser())
        Path(database_path).parent.mkdir(parents=True, exist_ok=True)
    return Settings(
        bot_token=token,
        database_path=database_path,
        admin_ids=admins,
        owner_ids=owners,
        group_link=os.getenv("GROUP_LINK", "").strip(),
        channel_link=os.getenv("CHANNEL_LINK", "").strip(),
        support_link=os.getenv("SUPPORT_LINK", "").strip(),
        query_group_id=_parse_chat_id(os.getenv("QUERY_GROUP_ID", "")),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
    )
