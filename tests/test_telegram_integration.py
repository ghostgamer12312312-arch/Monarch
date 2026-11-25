from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from monarchbot import handlers
from monarchbot.config import Settings


class FakeMessage:
    def __init__(self):
        self.replies: list[str] = []
        self.reply_kwargs: list[dict] = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)
        self.reply_kwargs.append(kwargs)


class FakeCallbackQuery:
    def __init__(self, data: str, user_id: int = 101):
        self.data = data
        self.from_user = SimpleNamespace(id=user_id)
        self.message = FakeMessage()
        self.answers: list[dict] = []
        self.edits: list[tuple[str, dict]] = []

    async def answer(self, text=None, **kwargs):
        self.answers.append({"text": text, **kwargs})

    async def edit_message_text(self, text, **kwargs):
        self.edits.append((text, kwargs))


class FakeUpdate:
    def __init__(self, user_id: int):
        self.update_id = 1
        self.effective_user = SimpleNamespace(
            id=user_id, first_name="Test", last_name=None, username="tester"
        )
        self.effective_message = FakeMessage()
        self.callback_query = None


class FakeCallbackUpdate(FakeUpdate):
    def __init__(self, data: str, user_id: int = 101):
        super().__init__(user_id)
        self.callback_query = FakeCallbackQuery(data, user_id)


def context_for(service):
    application = handlers.build_application(
        Settings("123456789:TEST_TOKEN_NEVER_USED", ":memory:", frozenset()), service
    )
    return SimpleNamespace(application=application)


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
    assert "gacha" in command_names
    assert "economy_history" not in command_names


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


def test_main_menu_exposes_real_inline_destinations():
    callback_data = {button.callback_data for row in handlers.main_menu().inline_keyboard for button in row}
    assert {"m:dungeons", "m:daily", "m:raid", "m:market", "m:about", "m:help"} <= callback_data


@pytest.mark.asyncio
async def test_dungeon_button_uses_server_battle_service_and_renders_turn(game):
    service, _, _ = game
    service.register(101, "tester", "Test Hunter")
    context = SimpleNamespace(
        application=SimpleNamespace(
            bot_data={"game": service, "settings": SimpleNamespace(admin_ids=frozenset())}
        )
    )
    update = FakeCallbackUpdate("m:battle_gate")
    await handlers._menu_callback(update, context)
    assert update.callback_query.answers == [{"text": None}]
    assert update.callback_query.edits[0][0].startswith("🌸 CRIMSON GATE · TURN 1")
    assert service.battle_status(101)["dungeon_id"] == "gate"


@pytest.mark.asyncio
async def test_battle_action_callback_commits_and_refreshes_screen(game):
    service, _, _ = game
    service.register(101, "tester", "Test Hunter")
    battle = service.start_battle(101, "gate")
    context = SimpleNamespace(
        application=SimpleNamespace(
            bot_data={"game": service, "settings": SimpleNamespace(admin_ids=frozenset())}
        )
    )
    update = FakeCallbackUpdate(f"b:{battle['id']}:{battle['turn']}:attack")
    await handlers.battle_callback(update, context)
    assert update.callback_query.answers == [{"text": None}]
    assert update.callback_query.edits[0][0].startswith("🌸 CRIMSON GATE · TURN 2")
    assert service.battle_status(101)["turn"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action,first_line",
    [
        ("profile", "👤 <b>HUNTER PROFILE</b>"),
        ("home", "MONARCH HUNTER HUB"),
        ("about", "MONARCH is an original"),
        ("help", "Use /help"),
        ("daily", "Daily reward claimed:"),
        ("inventory", "🎒 <b>INVENTORY</b>"),
        ("companions", "ECHO COMPANIONS"),
        ("guild", "Guilds:"),
        ("raid", "WORLD RAID"),
        ("tournament", "WEEKLY ARENA"),
        ("market", "MARKET"),
        ("unknown", "Use /help"),
    ],
)
async def test_menu_callback_routes_render_expected_destinations(game, action, first_line):
    service, _, _ = game
    service.register(101, "tester", "Test Hunter")
    update = FakeCallbackUpdate(f"m:{action}")
    await handlers._menu_callback(update, context_for(service))
    assert update.callback_query.answers == [{"text": None}]
    assert update.callback_query.edits[0][0].startswith(first_line)


@pytest.mark.asyncio
@pytest.mark.parametrize("category", ["classes", "dungeons", "items", "companions"])
async def test_codex_callbacks_render_each_catalog_category(game, category):
    service, _, _ = game
    service.register(101, "tester", "Test Hunter")
    update = FakeCallbackUpdate(f"c:{category}")
    await handlers._codex_callback(update, context_for(service))
    assert update.callback_query.answers == [{"text": None}]
    assert update.callback_query.edits
    assert update.callback_query.edits[0][1]["reply_markup"].inline_keyboard


@pytest.mark.asyncio
async def test_small_command_surface_renders_real_player_data(game):
    service, _, _ = game
    service.register(101, "tester", "Test Hunter")
    context = context_for(service)

    class Args:
        def __init__(self, *args):
            self.args = list(args)

    choose = FakeUpdate(101)
    choose.args = []
    await handlers.choose_class(choose, SimpleNamespace(application=context.application, args=[]))
    assert "AWAKENING CLASSES" in choose.effective_message.replies[0]

    choose_class = FakeUpdate(101)
    await handlers.choose_class(
        choose_class, SimpleNamespace(application=context.application, args=["assassin"])
    )
    assert "Assassin" in choose_class.effective_message.replies[0]

    daily_status = FakeUpdate(101)
    await handlers.daily_status(daily_status, SimpleNamespace(application=context.application, args=[]))
    assert "DAILY STATUS" in daily_status.effective_message.replies[0]

    shop = FakeUpdate(101)
    await handlers.shop(shop, SimpleNamespace(application=context.application, args=[]))
    assert "SUPPLY DEPOT" in shop.effective_message.replies[0]

    battle_status = FakeUpdate(101)
    await handlers.battle_status(battle_status, SimpleNamespace(application=context.application, args=[]))
    assert "no active battle" in battle_status.effective_message.replies[0].lower()


@pytest.mark.asyncio
async def test_profile_and_inventory_use_rich_safe_markup(game):
    service, _, _ = game
    service.register(101, "tester", "<Hunter>")
    context = SimpleNamespace(
        application=SimpleNamespace(
            bot_data={"game": service, "settings": SimpleNamespace(admin_ids=frozenset())}
        )
    )
    profile_update = FakeUpdate(101)
    await handlers.profile(profile_update, context)
    assert "HUNTER FILE" in profile_update.effective_message.replies[0]
    assert "&lt;Hunter&gt;" in profile_update.effective_message.replies[0]
    assert profile_update.effective_message.reply_kwargs[0]["parse_mode"] == "HTML"

    inventory_update = FakeUpdate(101)
    await handlers.inventory(inventory_update, context)
    assert "INVENTORY" in inventory_update.effective_message.replies[0]
