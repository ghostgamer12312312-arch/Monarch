from __future__ import annotations

import asyncio
import logging
from functools import wraps

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from .catalog import CLASSES, COMPANION_EXTRACT_COST, COMPANIONS, DUNGEONS, ITEMS
from .config import Settings
from .marketplace import MarketplaceService
from .service import GameError, GameService
from .social import SocialService
from .tournament import TournamentService

logger = logging.getLogger(__name__)
_CALL_FAILED = object()


def _uid(update: Update) -> int:
    if update.effective_user is None:
        raise GameError("I couldn't identify your Telegram account for this action.")
    return update.effective_user.id


async def _is_operator(update: Update, context: ContextTypes.DEFAULT_TYPE, minimum: str = "admin") -> bool:
    return await asyncio.to_thread(
        context.application.bot_data["game"].has_admin_role,
        _uid(update),
        context.application.bot_data["settings"].admin_ids,
        minimum,
    )


async def _call(update: Update, function, *args, preserve_failure: bool = False):
    try:
        return await asyncio.to_thread(function, *args)
    except GameError as exc:
        if update.effective_message:
            await update.effective_message.reply_text(str(exc))
        return _CALL_FAILED if preserve_failure else None
    except Exception:
        logger.exception("Game operation failed for update %s", update.update_id)
        if update.effective_message:
            await update.effective_message.reply_text(
                "The game hit an internal error. Your action was not confirmed; try again shortly."
            )
        return _CALL_FAILED if preserve_failure else None


def _protected(function):
    @wraps(function)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if function.__name__ not in {"start", "help_command", "maintenance"}:
                maintenance_on = await asyncio.to_thread(
                    context.application.bot_data["game"].maintenance_enabled
                )
                if maintenance_on and not await _is_operator(update, context):
                    if update.effective_message:
                        await update.effective_message.reply_text(
                            "Monarch is in maintenance mode. Please try again later."
                        )
                    return
            return await function(update, context)
        except GameError as exc:
            if update.effective_message:
                await update.effective_message.reply_text(str(exc))
        except Exception:
            logger.exception("Telegram command failed (%s), update=%s", function.__name__, update.update_id)
            if update.effective_message:
                await update.effective_message.reply_text("Something went wrong. Please try again.")

    return wrapped


def _player_name(update: Update) -> tuple[str | None, str]:
    user = update.effective_user
    if user is None:
        return None, "Hunter"
    name = " ".join(filter(None, [user.first_name, user.last_name])) or user.username or "Hunter"
    return user.username, name


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Profile", callback_data="m:profile"),
                InlineKeyboardButton("Dungeons", callback_data="m:dungeons"),
            ],
            [
                InlineKeyboardButton("Daily Reward", callback_data="m:daily"),
                InlineKeyboardButton("Inventory", callback_data="m:inventory"),
            ],
            [
                InlineKeyboardButton("Guild", callback_data="m:guild"),
                InlineKeyboardButton("World Boss", callback_data="m:raid"),
            ],
            [
                InlineKeyboardButton("Arena", callback_data="m:tournament"),
                InlineKeyboardButton("Market", callback_data="m:market"),
            ],
            [InlineKeyboardButton("Codex", callback_data="m:codex")],
            [InlineKeyboardButton("Echo Companions", callback_data="m:companions")],
        ]
    )


def _bar(value: int, maximum: int, width: int = 12) -> str:
    maximum = max(1, maximum)
    filled = max(0, min(width, round(width * value / maximum)))
    return "█" * filled + "░" * (width - filled)


def _battle_markup(battle: dict) -> InlineKeyboardMarkup:
    bid, turn = battle["id"], battle["turn"]
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Quick Attack", callback_data=f"b:{bid}:{turn}:attack"),
                InlineKeyboardButton("Guard", callback_data=f"b:{bid}:{turn}:guard"),
            ],
            [
                InlineKeyboardButton("Awakening Skill", callback_data=f"b:{bid}:{turn}:skill"),
                InlineKeyboardButton("Healing Tonic", callback_data=f"b:{bid}:{turn}:potion"),
            ],
            [
                InlineKeyboardButton("Mana Vial", callback_data=f"b:{bid}:{turn}:mana"),
                InlineKeyboardButton("Retreat", callback_data=f"b:{bid}:{turn}:flee"),
            ],
        ]
    )


def _battle_text(battle: dict) -> str:
    dungeon = DUNGEONS[battle["dungeon_id"]]
    lines = [
        f"{dungeon['name'].upper()} · TURN {battle['turn']}",
        "",
        f"Your HP  {_bar(int(battle['player_hp']), int(battle['player_max_hp']))}  {battle['player_hp']}/{battle['player_max_hp']}",
        f"Your Mana {_bar(int(battle['mana']), int(battle['max_mana']))}  {battle['mana']}/{battle['max_mana']}",
        "",
        f"{battle['enemy_name']} HP  {_bar(int(battle['enemy_hp']), int(battle['enemy_max_hp']))}  {battle['enemy_hp']}/{battle['enemy_max_hp']}",
    ]
    result = battle.get("result") or {}
    log = result.get("log") or []
    if log:
        lines.extend(["", "LAST TURN"])
        lines.extend(log[-4:])
    if battle["status"] != "active":
        lines.extend(["", f"Battle ended: {battle['status'].upper()}."])
    else:
        lines.extend(["", "Choose your action."])
    return "\n".join(lines)


async def _show_battle(update: Update, battle: dict, *, edit: bool = False) -> None:
    markup = _battle_markup(battle) if battle["status"] == "active" else None
    text = _battle_text(battle)
    if edit and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=markup)
    elif update.effective_message:
        await update.effective_message.reply_text(text, reply_markup=markup)


@_protected
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None:
        raise GameError("Open a private chat with the bot and try /start again.")
    username, display_name = _player_name(update)
    service: GameService = context.application.bot_data["game"]
    result = await _call(update, service.register, user.id, username, display_name)
    if result:
        _, created = result
        greeting = (
            "Your hunter file is ready. Two Healing Tonics and an Ash Hound are yours."
            if created
            else "Your hunter file is ready."
        )
        await update.effective_message.reply_text(
            f"MONARCH\n\n{greeting}\nUse /menu to open the game hub.", reply_markup=main_menu()
        )


@_protected
async def help_command(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "MONARCH — HUNTER COMMANDS\n"
        "/start — create your hunter file\n/menu — open the interactive game hub\n/profile — stats and awakening class\n"
        "/class [vanguard|assassin|arcanist] — choose your awakening once\n"
        "/battle [gate|rift|abyss] — enter a turn-based dungeon\n/battle_status — recover your active battle screen\n"
        "/daily — claim daily coins and energy\n/quests — view daily objectives\n/quest_claim <id> — claim a quest\n"
        "/daily_status — check today's claim and streak\n/weekly — claim a larger weekly reward\n/give <player_id> <amount> — send coins to another hunter\n/codex [classes|dungeons|items|companions] — browse game content\n"
        "/shop — view items\n/buy <item_id> [qty] — buy items\n/inventory — view items\n/use energy_shard — restore energy\n"
        "/leaderboard — top hunters\n/guild — view or browse guilds\n/guild_create <name>\n/guild_join <id>\n/guild_leave\n/guild_disband\n/guild_donate <coins>\n/guild_promote <id>\n/guild_demote <id>\n/guild_kick <id>\n/guild_rename <name>\n"
        "/companions — view your echo allies\n/extract — spend essence to summon an ally\n/equip <id|none> — change your companion\n"
        "/raid — shared boss status\n/raid_attack — attack once per hour\n/tournament — weekly arena standings\n/tournament_join\n/tournament_claim\n"
        "/market [page] — browse player listings\n/market_list <item> <qty> <price>\n/market_buy <id>\n/market_cancel <id>\n"
        "/tournament_start\n/tournament_checkin <match_id>\n/tournament_matches\n"
        "Admin: /admin_stats, /givecoins <player_id> <amount>, /ban <player_id> [minutes] [reason], /unban <player_id>, /admin_add <id> [role], /admin_remove <id>, /admin_audit, /maintenance <on|off|status>, /admin_reset_raids, /admin_resolve_matches"
    )


@_protected
async def menu(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "MONARCH HUNTER HUB\nChoose a destination.", reply_markup=main_menu()
    )


@_protected
async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    p = await _call(update, context.application.bot_data["game"].profile, _uid(update))
    if p:
        guild = p["guild"]["name"] if p["guild"] else "None"
        await update.effective_message.reply_text(
            f"HUNTER PROFILE — {p['display_name']}\nRank: {p['rank']} (Level {p['level']})\n"
            f"Class: {p['class_name']}\nXP: {p['xp']}/{p['level'] * 100}\nCoins: {p['coins']:,} · Essence: {p['essence']:,}\n"
            f"Energy: {p['energy']}/{p['max_energy']}\nCombat: {p['attack']} ATK · {p['base_hp']} HP · {p['max_mana']} Mana\nEcho ally: {p['companion_name']}\nGuild: {guild}"
        )


@_protected
async def choose_class(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.effective_message.reply_text(
            "Awakening classes:\n"
            + "\n".join(f"{key}: {v['name']} — {v['skill']}" for key, v in CLASSES.items())
        )
        return
    chosen = await _call(
        update, context.application.bot_data["game"].choose_class, _uid(update), context.args[0].lower()
    )
    if chosen:
        await update.effective_message.reply_text(f"Awakening complete. You are now a {chosen['name']}.")


@_protected
async def daily(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    result = await _call(update, context.application.bot_data["game"].daily, _uid(update))
    if result:
        await update.effective_message.reply_text(
            f"Daily reward: {result['coins']} coins, +{result['energy_gained']} energy. Streak: {result['streak']} day(s)."
        )


@_protected
async def daily_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    result = await _call(update, context.application.bot_data["game"].daily_status, _uid(update))
    if result:
        claim = "Already claimed today." if result["claimed_today"] else "Available now with /daily."
        await update.effective_message.reply_text(
            f"DAILY STATUS\n{claim}\nCurrent streak: {result['streak']} day(s)\n"
            f"If you claim today: {result['next_streak']} day(s)\nEnergy: {result['energy']}/{result['max_energy']}"
        )


@_protected
async def weekly(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    result = await _call(update, context.application.bot_data["game"].weekly, _uid(update))
    if result:
        await update.effective_message.reply_text(
            f"Weekly reward: {result['coins']} coins for {result['week']}."
        )


@_protected
async def give_coins(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) != 2 or not all(value.isdigit() for value in context.args):
        raise GameError("Usage: /give <player_id> <amount>.")
    result = await _call(
        update,
        context.application.bot_data["game"].give_coins,
        _uid(update),
        int(context.args[0]),
        int(context.args[1]),
    )
    if result:
        await update.effective_message.reply_text(f"Sent {result['amount']} coins to {result['target']}.")


@_protected
async def codex(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    category = context.args[0].lower() if context.args else "all"
    if len(context.args) > 1 or category not in {"all", "classes", "dungeons", "items", "companions"}:
        raise GameError("Usage: /codex [classes|dungeons|items|companions].")
    sections = {
        "classes": "AWAKENING CLASSES\n"
        + "\n".join(
            f"{key} — {value['name']}: {value['skill']} · {value['hp']} HP · {value['attack']} ATK · {value['mana']} Mana"
            for key, value in CLASSES.items()
        ),
        "dungeons": "DUNGEONS\n"
        + "\n".join(
            f"{key} — {value['name']} · level {minimum} · {value['energy']} energy · {value['xp']} XP / {value['coins']} coins / {value['essence']} essence"
            for (key, value), minimum in zip(DUNGEONS.items(), (1, 3, 8), strict=True)
        ),
        "items": "SUPPLIES\n"
        + "\n".join(
            f"{key} — {value['name']}: {value['description']} · {value['price']} coins"
            for key, value in ITEMS.items()
        ),
        "companions": f"ECHO COMPANIONS · extraction costs {COMPANION_EXTRACT_COST} essence\n"
        + "\n".join(
            f"{key} — {value['name']} ({value['rarity']}): +{value['hp']} HP, +{value['attack']} ATK"
            for key, value in COMPANIONS.items()
        ),
    }
    if category == "all":
        text = "\n\n".join(sections.values())
    else:
        text = sections[category]
    await update.effective_message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("Classes", callback_data="c:classes"),
                    InlineKeyboardButton("Dungeons", callback_data="c:dungeons"),
                ],
                [
                    InlineKeyboardButton("Items", callback_data="c:items"),
                    InlineKeyboardButton("Companions", callback_data="c:companions"),
                ],
            ]
        ),
    )


@_protected
async def duel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) != 1 or not context.args[0].isdigit():
        raise GameError("Usage: /duel <player_id>.")
    result = await _call(
        update, context.application.bot_data["social"].challenge, _uid(update), int(context.args[0])
    )
    if result:
        await update.effective_message.reply_text(
            f"Duel challenge #{result['id']} sent. It expires in {result['expires']} minutes; opponent uses /duel_accept {result['id']}."
        )


@_protected
async def duel_accept(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) != 1:
        raise GameError("Usage: /duel_accept <challenge_id>.")
    result = await _call(update, context.application.bot_data["social"].accept, _uid(update), context.args[0])
    if result:
        await update.effective_message.reply_text(
            f"Duel complete. Winner: {result.winner_id}; +{result.reward} coins. Ratings: {result.winner_rating} / {result.loser_rating}."
        )


@_protected
async def arena(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rows = await _call(update, context.application.bot_data["social"].leaderboard)
    if rows is not None:
        await update.effective_message.reply_text(
            "PVP ARENA\n"
            + (
                "\n".join(
                    f"{i}. {r['display_name']} — {r['rating']} rating · {r['wins']}W/{r['losses']}L"
                    for i, r in enumerate(rows, 1)
                )
                or "No rated duels yet."
            )
        )


@_protected
async def tournament_slots(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    result = await _call(update, context.application.bot_data["social"].tournament_slots, _uid(update))
    if result is not None:
        text = f"10-SLOT TOURNAMENT · {result['week']}\n" + (
            "\n".join(f"Slot {r['slot']}: {r['display_name']}" for r in result["slots"]) or "No entrants yet."
        )
        await update.effective_message.reply_text(text + "\nJoin with /tournament_join.")


@_protected
async def tournament_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    result = await _call(update, context.application.bot_data["social"].start_tournament, _uid(update))
    if result:
        await update.effective_message.reply_text(
            f"Tournament scheduled: {len(result['matches'])} match(es), random start times within {result['scheduled_within_hours']} hours, and a {result['duration_days']}-day check-in window."
        )


@_protected
async def tournament_checkin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) != 1:
        raise GameError("Usage: /tournament_checkin <match_id>.")
    result = await _call(
        update, context.application.bot_data["social"].checkin_tournament, _uid(update), context.args[0]
    )
    if result:
        await update.effective_message.reply_text(
            f"Checked in to match {result['id']}. Status: {result['status']}."
        )


@_protected
async def tournament_matches(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rows = await _call(update, context.application.bot_data["social"].tournament_matches, _uid(update))
    if rows is not None:
        await update.effective_message.reply_text(
            "TOURNAMENT MATCHES\n"
            + (
                "\n".join(
                    f"{r['id']}: {r['player_a']} vs {r['player_b']} · {r['status']} · check in with /tournament_checkin {r['id']}"
                    for r in rows
                )
                or "No matches scheduled."
            )
        )


@_protected
async def guild_raid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    result = await _call(update, context.application.bot_data["social"].guild_raid, _uid(update))
    if result:
        await update.effective_message.reply_text(
            f"GUILD RAID · {result['boss']} · Cycle {result['cycle']}\nYour damage: {result['damage']}\nBoss HP: {result['hp']:,}/{result['max_hp']:,}\n"
            + "\n".join(f"{i}. {x['display_name']} — {x['damage']:,}" for i, x in enumerate(result["top"], 1))
        )


@_protected
async def team(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    social = context.application.bot_data["social"]
    if context.args:
        if len(context.args) != 2 or not all(x.isdigit() for x in context.args):
            raise GameError("Usage: /team <player_id> <slot 1-6>.")
        result = await _call(
            update, social.add_team_member, _uid(update), int(context.args[0]), int(context.args[1])
        )
        if result:
            await update.effective_message.reply_text(
                f"Team slot {result['slot']} set to player {result['member_id']}."
            )
        return
    rows = await _call(update, social.team, _uid(update))
    if rows is not None:
        await update.effective_message.reply_text(
            "TEAM (up to 6)\n"
            + (
                "\n".join(f"Slot {r['slot']}: {r['display_name']} ({r['member_id']})" for r in rows)
                or "Empty. Add with /team <player_id> <slot>."
            )
        )


@_protected
async def team_power(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    result = await _call(update, context.application.bot_data["social"].team_power, _uid(update))
    if result is not None:
        await update.effective_message.reply_text(
            f"TEAM POWER · {result['power']} ATK across {result['members']} hunter(s)."
        )


@_protected
async def quests(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rows = await _call(update, context.application.bot_data["game"].quests, _uid(update))
    if rows is not None:
        lines = ["DAILY OBJECTIVES"]
        for quest in rows:
            reward = (
                "(claimed)" if quest["claimed"] else f"· reward {quest['coins']} coins + {quest['xp']} XP"
            )
            lines.append(f"{quest['id']} — {quest['name']}: {quest['progress']}/{quest['goal']} {reward}")
        lines.append("Claim with /quest_claim <id>.")
        await update.effective_message.reply_text("\n".join(lines))


@_protected
async def quest_claim(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) != 1:
        raise GameError("Usage: /quest_claim <quest_id>.")
    result = await _call(
        update, context.application.bot_data["game"].claim_quest, _uid(update), context.args[0]
    )
    if result:
        await update.effective_message.reply_text(
            f"Quest reward: {result['coins']} coins and {result['xp']} XP."
        )


@_protected
async def battle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    dungeon_id = context.args[0].lower() if context.args else "gate"
    result = await _call(update, context.application.bot_data["game"].start_battle, _uid(update), dungeon_id)
    if result:
        await _show_battle(update, result)


@_protected
async def battle_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    result = await _call(
        update,
        context.application.bot_data["game"].battle_status,
        _uid(update),
        preserve_failure=True,
    )
    if result is _CALL_FAILED:
        return
    if result:
        await _show_battle(update, result)
    else:
        await update.effective_message.reply_text("You have no active battle. Start one with /battle gate.")


async def battle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    try:
        _, battle_id, raw_turn, action = query.data.split(":", 3)
        result = await asyncio.to_thread(
            context.application.bot_data["game"].battle_action,
            query.from_user.id,
            battle_id,
            int(raw_turn),
            action,
        )
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    except Exception:
        logger.exception("Battle action failed before confirmation; update=%s", update.update_id)
        try:
            await query.answer("Action was not confirmed. Refresh with /battle_status.", show_alert=True)
        except BadRequest:
            pass
        return

    # The game transaction has committed; a Telegram UI failure must not imply rollback.
    try:
        await query.answer()
    except BadRequest:
        pass
    try:
        await _show_battle(update, result, edit=True)
    except TelegramError:
        logger.exception("Battle turn committed but screen refresh failed; update=%s", update.update_id)
        try:
            if query.message:
                await query.message.reply_text(
                    "Your turn was processed. Send /battle_status to refresh the battle screen."
                )
        except TelegramError:
            logger.exception("Could not send battle refresh fallback; update=%s", update.update_id)


@_protected
async def inventory(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rows = await _call(update, context.application.bot_data["game"].inventory, _uid(update))
    if rows is not None:
        text = "INVENTORY\n" + (
            "\n".join(f"{x['name']} × {x['quantity']} ({x['item_id']})" for x in rows) if rows else "Empty"
        )
        await update.effective_message.reply_text(text)


@_protected
async def companions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rows = await _call(update, context.application.bot_data["game"].companions, _uid(update))
    if rows is None:
        return
    lines = ["ECHO COMPANIONS"]
    lines.extend(
        f"{'★ ' if row['equipped'] else ''}{row['name']} ({row['id']}) · {row['rarity']} · Lv {row['level']} · +{row['attack'] + (row['level'] - 1)} ATK / +{row['hp'] + (row['level'] - 1) * 3} HP · fragments {row['fragments']}/3"
        for row in rows
    )
    if not rows:
        lines.append("No companions owned yet.")
    lines.append("/extract costs 25 essence · /equip <companion_id|none>")
    await update.effective_message.reply_text("\n".join(lines))


@_protected
async def extract_companion(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    result = await _call(update, context.application.bot_data["game"].extract_companion, _uid(update))
    if result:
        if result["duplicate"]:
            detail = "Duplicate echo converted into a fragment" + (
                "; companion level increased!" if result["level_up"] else f" ({result['fragments']}/3)."
            )
        else:
            detail = "New companion added to your collection."
        await update.effective_message.reply_text(
            f"Echo extraction: {result['name']} [{result['rarity']}]. {detail} Cost: {result['cost']} essence. Use /companions and /equip."
        )


@_protected
async def equip_companion(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) != 1:
        raise GameError("Usage: /equip <companion_id|none>.")
    result = await _call(
        update, context.application.bot_data["game"].equip_companion, _uid(update), context.args[0]
    )
    if result:
        await update.effective_message.reply_text(f"Echo companion equipped: {result['name']}.")


@_protected
async def shop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rows = context.application.bot_data["game"].shop()
    await update.effective_message.reply_text(
        "HUNTER SUPPLY\n"
        + "\n".join(f"{x['id']} — {x['name']}: {x['price']} coins · {x['description']}" for x in rows)
    )


@_protected
async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not 1 <= len(context.args) <= 2:
        raise GameError("Usage: /buy <item_id> [quantity].")
    try:
        quantity = int(context.args[1]) if len(context.args) == 2 else 1
    except ValueError as exc:
        raise GameError("Quantity must be a whole number.") from exc
    result = await _call(
        update, context.application.bot_data["game"].buy_item, _uid(update), context.args[0].lower(), quantity
    )
    if result:
        await update.effective_message.reply_text(
            f"Purchased {result['quantity']} × {result['item']} for {result['cost']} coins."
        )


@_protected
async def use_item(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) != 1:
        raise GameError("Usage: /use energy_shard.")
    result = await _call(
        update, context.application.bot_data["game"].use_energy_item, _uid(update), context.args[0].lower()
    )
    if result:
        await update.effective_message.reply_text(
            f"Energy restored by {result['gained']}. Current energy: {result['energy']}."
        )


@_protected
async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rows = await _call(update, context.application.bot_data["game"].leaderboard)
    if rows is not None:
        lines = ["TOP HUNTERS"]
        lines.extend(
            f"{i}. {row['display_name']} — Lv {row['level']} · {row['xp']} XP"
            for i, row in enumerate(rows, 1)
        )
        await update.effective_message.reply_text("\n".join(lines))


@_protected
async def guild(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    result = await _call(update, context.application.bot_data["game"].guild_info, _uid(update))
    if result is None:
        return
    if result["guild"]:
        g = result["guild"]
        lines = [f"GUILD {g['name']} · Level {g['level']} · {g['xp']} XP"]
        lines.extend(f"• {m['display_name']}" for m in result["members"])
    else:
        lines = ["GUILDS — create with /guild_create <name> or join using /guild_join <id>"]
        lines.extend(
            f"{x['id']}. {x['name']} · Lv {x['level']} · {x['members']} members" for x in result["guilds"]
        )
    await update.effective_message.reply_text("\n".join(lines))


@_protected
async def guild_create(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        raise GameError("Usage: /guild_create <name>.")
    result = await _call(
        update, context.application.bot_data["game"].create_guild, _uid(update), " ".join(context.args)
    )
    if result:
        await update.effective_message.reply_text(
            f"Guild {result['name']} created. Invite hunters with /guild_join {result['id']}."
        )


@_protected
async def guild_join(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) != 1 or not context.args[0].isdigit():
        raise GameError("Usage: /guild_join <guild_id>.")
    result = await _call(
        update, context.application.bot_data["game"].join_guild, _uid(update), int(context.args[0])
    )
    if result:
        await update.effective_message.reply_text(f"You joined {result['name']}.")


@_protected
async def guild_leave(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    result = await _call(update, context.application.bot_data["game"].leave_guild, _uid(update))
    if result:
        await update.effective_message.reply_text(f"You left {result}.")


@_protected
async def guild_disband(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    result = await _call(update, context.application.bot_data["game"].disband_guild, _uid(update))
    if result:
        await update.effective_message.reply_text(f"Guild {result} was disbanded.")


@_protected
async def guild_donate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) != 1 or not context.args[0].isdigit():
        raise GameError("Usage: /guild_donate <coins>.")
    result = await _call(
        update, context.application.bot_data["game"].donate_guild, _uid(update), int(context.args[0])
    )
    if result:
        await update.effective_message.reply_text(
            f"Donated {result['donation']} coins to {result['name']}; guild is level {result['level']} with {result['xp']} XP."
        )


@_protected
async def guild_promote(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) != 1 or not context.args[0].isdigit():
        raise GameError("Usage: /guild_promote <player_id>.")
    role = await _call(
        update, context.application.bot_data["game"].guild_promote, _uid(update), int(context.args[0])
    )
    if role:
        await update.effective_message.reply_text(f"Player promoted to {role}.")


@_protected
async def guild_demote(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) != 1 or not context.args[0].isdigit():
        raise GameError("Usage: /guild_demote <player_id>.")
    role = await _call(
        update, context.application.bot_data["game"].guild_demote, _uid(update), int(context.args[0])
    )
    if role:
        await update.effective_message.reply_text("Officer demoted to member.")


@_protected
async def guild_kick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) != 1 or not context.args[0].isdigit():
        raise GameError("Usage: /guild_kick <player_id>.")
    target = await _call(
        update, context.application.bot_data["game"].guild_kick, _uid(update), int(context.args[0])
    )
    if target:
        await update.effective_message.reply_text(f"Player {target} removed from the guild.")


@_protected
async def guild_rename(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        raise GameError("Usage: /guild_rename <new name>.")
    name = await _call(
        update, context.application.bot_data["game"].guild_rename, _uid(update), " ".join(context.args)
    )
    if name:
        await update.effective_message.reply_text(f"Guild renamed to {name}.")


@_protected
async def raid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    result = await _call(update, context.application.bot_data["game"].raid_status)
    if result is not None:
        boss = result["boss"]
        lines = [
            f"WORLD RAID — {boss['name']} · Cycle {boss['cycle']}",
            f"HP {boss['hp']:,}/{boss['max_hp']:,}",
            "Top hunters:",
        ]
        lines.extend(
            f"{i}. {p['display_name']} · {p['total_damage']:,}" for i, p in enumerate(result["top"], 1)
        )
        lines.append("Attack with /raid_attack (20 energy; 60-minute cooldown).")
        await update.effective_message.reply_text("\n".join(lines))


@_protected
async def raid_attack(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    result = await _call(update, context.application.bot_data["game"].raid_attack, _uid(update))
    if result:
        msg = f"Raid strike: {result['damage']:,} effective damage to {result['boss']}. Boss HP: {result['hp']:,}/{result['max_hp']:,}. Reward: {result['reward']} coins."
        if result["defeated"]:
            msg += " Boss defeated! The next cycle has begun."
        await update.effective_message.reply_text(msg)


@_protected
async def tournament(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    result = await _call(update, context.application.bot_data["tournament"].status, _uid(update))
    if result:
        lines = [
            f"WEEKLY HUNTER ARENA · {result['week']}",
            f"Entered: {result['joined']} · Score: {result['score']:,}",
            f"Participants: {result['participants']} · Prizes: {', '.join(map(str, result['prizes']))} coins",
            "Top hunters:",
        ]
        lines.extend(f"{i}. {x['display_name']} — {x['score']:,}" for i, x in enumerate(result["leaders"], 1))
        lines.append("Join with /tournament_join; claim after reset with /tournament_claim.")
        await update.effective_message.reply_text("\n".join(lines))


@_protected
async def tournament_join(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    result = await _call(update, context.application.bot_data["social"].join_tournament, _uid(update))
    if result:
        await update.effective_message.reply_text(
            f"Entered {result['week']} tournament in slot {result['slot']}/10. Participants: {result['participants']}/10."
        )


@_protected
async def tournament_claim(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    result = await _call(update, context.application.bot_data["tournament"].claim, _uid(update))
    if result:
        await update.effective_message.reply_text(
            f"Arena prize: place #{result['place']} — {result['prize']} coins for {result['week']}."
        )


@_protected
async def market(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        offset = int(context.args[0]) * 10 if context.args else 0
    except ValueError as exc:
        raise GameError("Usage: /market [page_number].") from exc
    if offset < 0:
        raise GameError("Page number must be positive.")
    result = await _call(update, context.application.bot_data["market"].browse, _uid(update), offset)
    if result is not None:
        lines = [f"HUNTER MARKET · {result['total']} active listings · page {offset // 10 + 1}"]
        lines.extend(
            f"#{x['id']} {x['item_name']} ×{x['quantity']} — {x['price']:,} coins · {x['seller_name']}"
            for x in result["items"]
        )
        lines.append("/market_buy <listing_id> · /market page numbers are 0-based")
        await update.effective_message.reply_text("\n".join(lines))


@_protected
async def market_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) != 3:
        raise GameError("Usage: /market_list <item_id> <quantity> <total_price>.")
    try:
        quantity, price = int(context.args[1]), int(context.args[2])
    except ValueError as exc:
        raise GameError("Quantity and price must be whole numbers.") from exc
    result = await _call(
        update,
        context.application.bot_data["market"].create_listing,
        _uid(update),
        context.args[0].lower(),
        quantity,
        price,
    )
    if result:
        await update.effective_message.reply_text(
            f"Listed #{result['id']}: {result['item']} ×{result['quantity']} for {result['price']:,} coins. Items are held in escrow for up to seven days."
        )


@_protected
async def market_buy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) != 1 or not context.args[0].isdigit():
        raise GameError("Usage: /market_buy <listing_id>.")
    result = await _call(
        update, context.application.bot_data["market"].buy, _uid(update), int(context.args[0])
    )
    if result:
        await update.effective_message.reply_text(
            f"Purchased {result['item']} ×{result['quantity']} for {result['price']:,} coins. Seller fee: {result['fee']} coins."
        )


@_protected
async def market_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) != 1 or not context.args[0].isdigit():
        raise GameError("Usage: /market_cancel <listing_id>.")
    result = await _call(
        update, context.application.bot_data["market"].cancel, _uid(update), int(context.args[0])
    )
    if result:
        await update.effective_message.reply_text(
            f"Cancelled listing; {result['item']} ×{result['quantity']} returned to inventory."
        )


@_protected
async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _is_operator(update, context):
        raise GameError("Admin only.")
    result = await _call(update, context.application.bot_data["game"].admin_stats)
    if result:
        await update.effective_message.reply_text(
            f"ADMIN STATS\nHunters: {result['players']}\nGuilds: {result['guilds']}\nCoins in circulation: {result['coins']:,}"
        )


@_protected
async def givecoins(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _is_operator(update, context):
        raise GameError("Admin only.")
    if len(context.args) != 2:
        raise GameError("Usage: /givecoins <player_id> <amount>.")
    try:
        player_id, amount = map(int, context.args)
    except ValueError as exc:
        raise GameError("Player ID and amount must be whole numbers.") from exc
    result = await _call(update, context.application.bot_data["game"].admin_grant, player_id, amount)
    if result is not None:
        await update.effective_message.reply_text("Coins granted.")


@_protected
async def ban_player(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    admin_id = _uid(update)
    if not await _is_operator(update, context, "moderator"):
        raise GameError("Admin only.")
    if not context.args or not context.args[0].isdigit():
        raise GameError("Usage: /ban <player_id> [reason].")
    target = int(context.args[0])
    if target in context.application.bot_data["settings"].admin_ids:
        raise GameError("You cannot ban a configured bot administrator.")
    duration = 0
    reason_args = context.args[1:]
    if reason_args and reason_args[0].isdigit():
        duration = int(reason_args[0])
        reason_args = reason_args[1:]
    result = await _call(
        update,
        context.application.bot_data["game"].admin_ban,
        target,
        " ".join(reason_args) or "No reason",
        admin_id,
        duration,
    )
    if result is not None:
        await update.effective_message.reply_text("Player restricted from game actions.")


@_protected
async def unban_player(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _is_operator(update, context, "moderator"):
        raise GameError("Admin only.")
    if len(context.args) != 1 or not context.args[0].isdigit():
        raise GameError("Usage: /unban <player_id>.")
    result = await _call(update, context.application.bot_data["game"].admin_unban, int(context.args[0]))
    if result is not None:
        await update.effective_message.reply_text("Player access restored.")


@_protected
async def admin_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if _uid(update) not in context.application.bot_data["settings"].admin_ids:
        raise GameError("Only configured administrators can grant roles.")
    if len(context.args) not in {1, 2} or not context.args[0].isdigit():
        raise GameError("Usage: /admin_add <player_id> [admin|moderator].")
    role = context.args[1] if len(context.args) == 2 else "moderator"
    result = await _call(
        update,
        context.application.bot_data["game"].grant_admin_role,
        _uid(update),
        int(context.args[0]),
        role,
    )
    if result:
        await update.effective_message.reply_text(f"Granted {result['role']} role to {result['target_id']}.")


@_protected
async def admin_remove(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if _uid(update) not in context.application.bot_data["settings"].admin_ids:
        raise GameError("Only configured administrators can revoke roles.")
    if len(context.args) != 1 or not context.args[0].isdigit():
        raise GameError("Usage: /admin_remove <player_id>.")
    result = await _call(
        update, context.application.bot_data["game"].revoke_admin_role, _uid(update), int(context.args[0])
    )
    if result:
        await update.effective_message.reply_text("Database-backed role revoked.")


@_protected
async def admin_audit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _is_operator(update, context):
        raise GameError("Admin only.")
    rows = await _call(update, context.application.bot_data["game"].admin_audit, 20)
    if rows is not None:
        await update.effective_message.reply_text(
            "ADMIN AUDIT\n"
            + (
                "\n".join(
                    f"#{row['id']} {row['action']} target={row['target_id']} actor={row['actor_id']} {row['detail']}"
                    for row in rows
                )
                or "No audit events."
            )
        )


@_protected
async def maintenance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if _uid(update) not in context.application.bot_data["settings"].admin_ids:
        raise GameError("Only configured administrators can change maintenance mode.")
    if len(context.args) != 1 or context.args[0].lower() not in {"on", "off", "status"}:
        raise GameError("Usage: /maintenance <on|off|status>.")
    action = context.args[0].lower()
    if action == "status":
        enabled = await _call(update, context.application.bot_data["game"].maintenance_enabled)
    else:
        enabled = await _call(
            update, context.application.bot_data["game"].set_maintenance, _uid(update), action == "on"
        )
    if enabled is not None:
        await update.effective_message.reply_text(f"Maintenance mode: {'ON' if enabled else 'OFF'}.")


@_protected
async def admin_reset_raids(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if _uid(update) not in context.application.bot_data["settings"].admin_ids:
        raise GameError("Admin only.")
    count = await _call(update, context.application.bot_data["social"].reset_weekly_raids)
    if count is not None:
        await update.effective_message.reply_text(
            f"Reset {count} guild raid(s), if the current UTC week had not already been reset."
        )


@_protected
async def admin_resolve_matches(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if _uid(update) not in context.application.bot_data["settings"].admin_ids:
        raise GameError("Admin only.")
    count = await _call(update, context.application.bot_data["social"].resolve_tournaments)
    if count is not None:
        await update.effective_message.reply_text(f"Resolved {count} due tournament match(es).")


COMMANDS = {
    "start": start,
    "help": help_command,
    "menu": menu,
    "profile": profile,
    "class": choose_class,
    "daily": daily,
    "daily_status": daily_status,
    "weekly": weekly,
    "give": give_coins,
    "codex": codex,
    "quests": quests,
    "quest_claim": quest_claim,
    "battle": battle,
    "battle_status": battle_status,
    "inventory": inventory,
    "companions": companions,
    "extract": extract_companion,
    "equip": equip_companion,
    "shop": shop,
    "buy": buy,
    "use": use_item,
    "leaderboard": leaderboard,
    "guild": guild,
    "guild_create": guild_create,
    "guild_join": guild_join,
    "guild_leave": guild_leave,
    "guild_disband": guild_disband,
    "guild_donate": guild_donate,
    "guild_promote": guild_promote,
    "guild_demote": guild_demote,
    "guild_kick": guild_kick,
    "guild_rename": guild_rename,
    "raid": raid,
    "raid_attack": raid_attack,
    "tournament": tournament,
    "tournament_join": tournament_join,
    "tournament_claim": tournament_claim,
    "market": market,
    "market_list": market_list,
    "market_buy": market_buy,
    "market_cancel": market_cancel,
    "admin_stats": admin_stats,
    "givecoins": givecoins,
    "ban": ban_player,
    "unban": unban_player,
    "admin_add": admin_add,
    "admin_remove": admin_remove,
    "admin_audit": admin_audit,
    "maintenance": maintenance,
    "admin_reset_raids": admin_reset_raids,
    "admin_resolve_matches": admin_resolve_matches,
    "duel": duel,
    "duel_accept": duel_accept,
    "arena": arena,
    "tournament_slots": tournament_slots,
    "tournament_start": tournament_start,
    "tournament_checkin": tournament_checkin,
    "tournament_matches": tournament_matches,
    "guild_raid": guild_raid,
    "team": team,
    "team_power": team_power,
}

PUBLIC_COMMANDS = [
    ("start", "Create your hunter file"),
    ("menu", "Open the hunter hub"),
    ("help", "Show commands"),
    ("profile", "View your hunter stats"),
    ("class", "Choose your awakening class"),
    ("daily", "Claim daily reward"),
    ("daily_status", "Check reward and streak"),
    ("weekly", "Claim weekly reward"),
    ("give", "Send coins to a hunter"),
    ("codex", "Browse classes, dungeons and items"),
    ("quests", "View daily objectives"),
    ("quest_claim", "Claim a daily objective"),
    ("battle", "Enter a turn-based dungeon"),
    ("battle_status", "Refresh your active battle"),
    ("shop", "View hunter supplies"),
    ("buy", "Buy an item"),
    ("inventory", "View your items"),
    ("companions", "View your echo allies"),
    ("extract", "Summon an echo ally"),
    ("equip", "Equip an echo companion"),
    ("use", "Use an energy shard"),
    ("leaderboard", "Top hunters"),
    ("guild", "View or browse guilds"),
    ("guild_create", "Create a guild"),
    ("guild_join", "Join a guild"),
    ("guild_leave", "Leave your guild"),
    ("guild_disband", "Disband your guild"),
    ("guild_donate", "Donate guild XP"),
    ("guild_promote", "Promote a guild officer"),
    ("guild_demote", "Demote a guild officer"),
    ("guild_kick", "Remove a guild member"),
    ("guild_rename", "Rename your guild"),
    ("raid", "View the world raid"),
    ("raid_attack", "Attack the world boss"),
    ("tournament", "Weekly arena standings"),
    ("tournament_join", "Enter this week's arena"),
    ("tournament_claim", "Claim an arena prize"),
    ("market", "Browse item listings"),
    ("market_list", "List an item for sale"),
    ("market_buy", "Buy a market listing"),
    ("market_cancel", "Cancel a market listing"),
    ("duel", "Challenge a hunter"),
    ("duel_accept", "Accept a duel"),
    ("arena", "PvP rating leaderboard"),
    ("tournament_slots", "View ten tournament slots"),
    ("tournament_start", "Schedule tournament matches"),
    ("tournament_checkin", "Check in to a match"),
    ("tournament_matches", "View your tournament matches"),
    ("guild_raid", "Attack your guild boss"),
    ("team", "Manage your six-member team"),
    ("team_power", "View combined team power"),
]


async def _render_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    action = query.data[2:]
    if action == "profile":
        result = await asyncio.to_thread(context.application.bot_data["game"].profile, query.from_user.id)
        text = f"{result['display_name']} · Level {result['level']} · {result['class_name']}\nHP {result['base_hp']} · ATK {result['attack']} · Energy {result['energy']}/{result['max_energy']}"
    elif action == "dungeons":
        text = "Choose a dungeon:\n/battle gate — Crimson Gate\n/battle rift — Forgotten Rift\n/battle abyss — Abyssal Citadel"
    elif action == "codex":
        text = "CODEX\nChoose a section below."
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("Classes", callback_data="c:classes"),
                        InlineKeyboardButton("Dungeons", callback_data="c:dungeons"),
                    ],
                    [
                        InlineKeyboardButton("Items", callback_data="c:items"),
                        InlineKeyboardButton("Companions", callback_data="c:companions"),
                    ],
                ]
            ),
        )
        return
    elif action == "daily":
        result = await asyncio.to_thread(context.application.bot_data["game"].daily, query.from_user.id)
        text = f"Daily reward claimed: {result['coins']} coins and +{result['energy_gained']} energy."
    elif action == "inventory":
        rows = await asyncio.to_thread(context.application.bot_data["game"].inventory, query.from_user.id)
        text = "INVENTORY\n" + ("\n".join(f"{r['name']} ×{r['quantity']}" for r in rows) if rows else "Empty")
    elif action == "companions":
        rows = await asyncio.to_thread(context.application.bot_data["game"].companions, query.from_user.id)
        text = "ECHO COMPANIONS\n" + (
            "\n".join(
                f"{'★ ' if r['equipped'] else ''}{r['name']} · Lv {r['level']} · {r['rarity']}" for r in rows
            )
            if rows
            else "No companions owned yet."
        )
    elif action == "guild":
        text = "Guilds: /guild to browse · /guild_create <name> · /guild_join <id>"
    elif action == "raid":
        result = await asyncio.to_thread(context.application.bot_data["game"].raid_status)
        b = result["boss"]
        text = f"WORLD RAID · {b['name']}\nHP {b['hp']:,}/{b['max_hp']:,}\nUse /raid_attack to strike."
    elif action == "tournament":
        result = await asyncio.to_thread(
            context.application.bot_data["tournament"].status, query.from_user.id
        )
        text = f"WEEKLY ARENA · {result['week']}\nYour score: {result['score']}\nJoin with /tournament_join."
    elif action == "market":
        result = await asyncio.to_thread(context.application.bot_data["market"].browse, query.from_user.id, 0)
        text = "MARKET\n" + (
            "\n".join(
                f"#{r['id']} {r['item_name']} ×{r['quantity']} — {r['price']} coins" for r in result["items"]
            )
            if result["items"]
            else "No listings yet."
        )
    else:
        text = "Use /help to see available actions."
    await query.edit_message_text(text, reply_markup=main_menu())


async def _menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    try:
        await _render_menu_callback(update, context)
    except GameError as exc:
        text = str(exc)
        try:
            await query.edit_message_text(text, reply_markup=main_menu())
        except BadRequest:
            if query.message:
                await query.message.reply_text(text, reply_markup=main_menu())
    except Exception:
        logger.exception("Menu callback failed; update=%s", update.update_id)
        text = "Could not load that section. Try the matching command or /menu again."
        try:
            await query.edit_message_text(text, reply_markup=main_menu())
        except BadRequest:
            if query.message:
                await query.message.reply_text(text, reply_markup=main_menu())


async def _codex_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    category = query.data.split(":", 1)[1]
    if category not in {"classes", "dungeons", "items", "companions"}:
        await query.answer("Unknown Codex section.", show_alert=True)
        return
    await query.answer()
    if category == "classes":
        text = "AWAKENING CLASSES\n" + "\n".join(
            f"{k} — {v['name']}: {v['skill']}" for k, v in CLASSES.items()
        )
    elif category == "dungeons":
        text = "DUNGEONS\n" + "\n".join(
            f"{k} — {v['name']} · {v['energy']} energy" for k, v in DUNGEONS.items()
        )
    elif category == "items":
        text = "SUPPLIES\n" + "\n".join(f"{k} — {v['name']}: {v['description']}" for k, v in ITEMS.items())
    else:
        text = "ECHO COMPANIONS\n" + "\n".join(
            f"{k} — {v['name']} ({v['rarity']})" for k, v in COMPANIONS.items()
        )
    await query.edit_message_text(
        text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="m:codex")]])
    )


async def _post_init(application: Application) -> None:
    commands = [BotCommand(name, description) for name, description in PUBLIC_COMMANDS]
    await application.bot.set_my_commands(commands)


async def _on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Unhandled Telegram update failure", exc_info=context.error)


def build_application(settings: Settings, game: GameService) -> Application:
    application = ApplicationBuilder().token(settings.bot_token).post_init(_post_init).build()
    application.bot_data["game"] = game
    application.bot_data["settings"] = settings
    application.bot_data["market"] = MarketplaceService(game)
    application.bot_data["tournament"] = TournamentService(game)
    application.bot_data["social"] = SocialService(game)
    for name, handler in COMMANDS.items():
        application.add_handler(CommandHandler(name, handler))
    application.add_handler(CallbackQueryHandler(battle_callback, pattern=r"^b:"))
    application.add_handler(CallbackQueryHandler(_menu_callback, pattern=r"^m:"))
    application.add_handler(CallbackQueryHandler(_codex_callback, pattern=r"^c:"))
    application.add_error_handler(_on_error)
    return application
