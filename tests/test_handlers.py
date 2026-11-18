from telegram.ext import CallbackQueryHandler, CommandHandler

from monarchbot.config import Settings
from monarchbot.handlers import COMMANDS, PUBLIC_COMMANDS, build_application


def test_all_commands_and_interactive_callbacks_are_registered(game):
    service, _, _ = game
    settings = Settings("123456789:TEST_TOKEN_NEVER_USED", ":memory:", frozenset({101}))
    app = build_application(settings, service)
    registered = {
        name
        for groups in app.handlers.values()
        for handler in groups
        if isinstance(handler, CommandHandler)
        for name in handler.commands
    }
    callback_count = sum(
        isinstance(handler, CallbackQueryHandler) for groups in app.handlers.values() for handler in groups
    )
    assert registered == set(COMMANDS)
    assert {name for name, _ in PUBLIC_COMMANDS} <= registered
    assert {
        "codex",
        "daily_status",
        "quest_claim",
        "guild_leave",
        "guild_disband",
        "tournament_claim",
        "market_list",
        "market_buy",
        "market_cancel",
    } <= {name for name, _ in PUBLIC_COMMANDS}
    assert not {
        "admin_stats",
        "givecoins",
        "ban",
        "unban",
        "admin_add",
        "admin_remove",
        "admin_audit",
        "maintenance",
        "admin_reset_raids",
        "admin_resolve_matches",
    } & {name for name, _ in PUBLIC_COMMANDS}
    assert callback_count >= 2
    assert app.bot_data["game"] is service
