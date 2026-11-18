from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from monarchbot import handlers


class FakeMessage:
    def __init__(self):
        self.replies: list[str] = []

    async def reply_text(self, text, **_kwargs):
        self.replies.append(text)


class FakeUpdate:
    def __init__(self, user_id: int):
        self.update_id = 1
        self.effective_user = SimpleNamespace(
            id=user_id, first_name="Test", last_name=None, username="tester"
        )
        self.effective_message = FakeMessage()
        self.callback_query = None


@pytest.mark.asyncio
async def test_post_init_publishes_commands_and_profile_metadata(game):
    service, _, _ = game
    bot = SimpleNamespace(
        set_my_commands=AsyncMock(),
        set_my_name=AsyncMock(),
        set_my_short_description=AsyncMock(),
        set_my_description=AsyncMock(),
    )
    application = SimpleNamespace(
        bot=bot, bot_data={"game": service, "settings": SimpleNamespace(admin_ids=frozenset())}
    )
    await handlers._post_init(application)
    bot.set_my_commands.assert_awaited_once()
    bot.set_my_name.assert_awaited_once_with(handlers.BOT_NAME)
    bot.set_my_short_description.assert_awaited_once_with(handlers.BOT_SHORT_DESCRIPTION)
    bot.set_my_description.assert_awaited_once_with(handlers.BOT_DESCRIPTION)
    command_names = [command.command for command in bot.set_my_commands.await_args.args[0]]
    assert "about" in command_names
    assert "economy_history" in command_names


@pytest.mark.asyncio
async def test_about_command_returns_the_published_description(game):
    service, _, _ = game
    update = FakeUpdate(101)
    context = SimpleNamespace(
        application=SimpleNamespace(
            bot_data={"game": service, "settings": SimpleNamespace(admin_ids=frozenset())}
        )
    )
    await handlers.about_command(update, context)
    assert update.effective_message.replies
    assert handlers.BOT_DESCRIPTION in update.effective_message.replies[0]


@pytest.mark.asyncio
async def test_first_start_returns_a_guided_tutorial(game):
    service, _, _ = game
    update = FakeUpdate(404)
    context = SimpleNamespace(
        application=SimpleNamespace(
            bot_data={"game": service, "settings": SimpleNamespace(admin_ids=frozenset())}
        )
    )
    await handlers.start(update, context)
    message = update.effective_message.replies[0]
    assert "WELCOME TO MONARCH" in message
    assert "/class vanguard" in message
    assert "/battle gate" in message
    assert "/daily" in message


@pytest.mark.asyncio
async def test_maintenance_gate_blocks_regular_handler_but_not_help(game):
    service, _, _ = game
    service.register(101, "tester", "Test Hunter")
    service.set_maintenance(999, True)
    context = SimpleNamespace(
        application=SimpleNamespace(
            bot_data={"game": service, "settings": SimpleNamespace(admin_ids=frozenset())}
        )
    )
    blocked = FakeUpdate(101)
    await handlers.profile(blocked, context)
    assert "maintenance mode" in blocked.effective_message.replies[0].lower()
    help_update = FakeUpdate(101)
    await handlers.help_command(help_update, context)
    assert "MONARCH" in help_update.effective_message.replies[0]
    assert "Admin:" not in help_update.effective_message.replies[0]
