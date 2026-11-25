from __future__ import annotations

import asyncio
import html
import logging
import time
from functools import wraps
from html import escape
from pathlib import Path

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from .catalog import (
    BANK_MIN_TRANSFER,
    CLASS_CRIT,
    CLASS_DODGE,
    CLASSES,
    COMPANION_EXTRACT_COST,
    COMPANIONS,
    DEFAULT_SKILL,
    DUNGEONS,
    ENEMIES,
    GACHA_CHARACTERS,
    GACHA_COST,
    GACHA_PITY,
    GACHA_RANKS,
    GACHA_TEN_COST,
    GACHA_WEIGHTS,
    GUILD_UPGRADE_BASE,
    HUNT_ENERGY_COST,
    ITEMS,
    REGIONS,
    SKILLS,
    WORLD_BOSSES,
)
from .config import Settings
from .marketplace import MarketplaceService
from .service import GameError, GameService
from .social import SocialService
from .tournament import TournamentService

logger = logging.getLogger(__name__)
_CALL_FAILED = object()
BOT_NAME = "H U N T E R S"
BOT_SHORT_DESCRIPTION = (
    "Turn-based hunter RPG with dungeons, guilds, PvP, raids, tournaments, and a fair player market."
)
BOT_DESCRIPTION = (
    "MONARCH is an original turn-based Telegram hunter RPG. Create your hunter, choose an awakening class, "
    "enter persistent dungeon battles, collect Echo companions, build a six-member team, join guilds, fight the "
    "world raid, challenge other hunters, compete in weekly tournaments, and trade supplies through a protected "
    "player market. Travel the rift, explore for supplies, forge a team, and climb the arena. "
    "Progression, rewards, moderation, and economy changes are stored transactionally. "
    "Use /start to begin and /menu to open the game hub."
)

ASSET_DIR = Path(__file__).resolve().parent.parent / "assets"
RULE = "━━━━━━━━━━━━━━━━━━"


def _asset_path(name: str) -> Path | None:
    path = ASSET_DIR / f"{name}.jpg"
    return path if path.is_file() else None


def _trim_caption(text: str, limit: int = 1000) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _combat_bars(p: dict) -> str:
    hp = _bar(int(p["base_hp"]), max(1, int(p["base_hp"])))
    return (
        f"HP {p['base_hp']}  ·  ATK {p['attack']}  ·  MANA {p['max_mana']}\n"
        f"XP {_bar(int(p['xp']), max(1, int(p['level']) * 100), 10)} {p['xp']}/{p['level'] * 100}"
    )


def _profile_caption(p: dict) -> str:
    guild = html.escape(str(p["guild"]["name"])) if p["guild"] else "No guild"
    return _trim_caption(
        f"<b>╭─『 HUNTER FILE 』</b>\n"
        f"<b>{html.escape(str(p['display_name']))}</b>\n"
        f"{html.escape(str(p['rank']))} · Level <b>{p['level']}</b> · {html.escape(str(p['class_name']))}\n"
        f"╰{RULE}\n\n"
        f"❤️ HP {p['base_hp']} · ⚔️ ATK {p['attack']} · 🔷 Mana {p['max_mana']}\n"
        f"{_combat_bars(p)}\n\n"
        f"🏟️ Arena <b>{p.get('arena_rating', 1000)}</b> · {p.get('wins', 0)}W/{p.get('losses', 0)}L\n"
        f"🪙 <b>{int(p['coins']):,}</b> coins · ✨ <b>{int(p['essence']):,}</b> essence\n"
        f"⚡ Energy <b>{p['energy']}/{p['max_energy']}</b>\n"
        f"🏰 Guild: <b>{guild}</b>\n"
        f"🐺 Echo: <b>{html.escape(str(p['companion_name']))}</b>\n\n"
        f"{RULE}\n"
        f"<i>💡 /hunt · /battle · /daily · /menu</i>"
    )


async def _reply_visual(
    update: Update,
    image_key: str,
    caption: str,
    markup: InlineKeyboardMarkup | None = None,
    details: str | None = None,
    parse_mode: str | None = None,
) -> None:
    message = update.effective_message
    if message is None:
        return
    path = _asset_path(image_key)
    sent = False
    if path is not None and len(caption) <= 1024:
        try:
            with path.open("rb") as photo:
                await message.reply_photo(
                    photo=photo,
                    caption=_trim_caption(caption, 1000),
                    parse_mode=parse_mode,
                    reply_markup=markup,
                )
            sent = True
        except (TelegramError, AttributeError):
            logger.warning("Could not send %s visual", image_key, exc_info=True)
    if not sent:
        await message.reply_text(caption, reply_markup=markup, parse_mode=parse_mode)
    if details:
        await message.reply_text(details, parse_mode=parse_mode, disable_web_page_preview=True)


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
            if function.__name__ not in {"start", "help_command", "maintenance", "userid", "query_command"}:
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


def community_links(settings: Settings | None) -> list[InlineKeyboardButton]:
    if settings is None:
        return []
    links = [
        ("Channel", getattr(settings, "channel_link", "")),
        ("Group", getattr(settings, "group_link", "")),
        ("Support", getattr(settings, "support_link", "")),
    ]
    return [InlineKeyboardButton(label, url=url) for label, url in links if url]


def main_menu(settings: Settings | None = None) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("👤 Profile", callback_data="m:profile"),
            InlineKeyboardButton("🌸 Hunt", callback_data="m:hunt"),
        ],
        [
            InlineKeyboardButton("⚔️ Dungeons", callback_data="m:dungeons"),
            InlineKeyboardButton("🌀 Skills", callback_data="m:skills"),
        ],
        [
            InlineKeyboardButton("✨ Gacha", callback_data="m:gacha"),
            InlineKeyboardButton("🎒 Inventory", callback_data="m:inventory"),
        ],
        [
            InlineKeyboardButton("🛒 Shop", callback_data="m:shop"),
            InlineKeyboardButton("🏪 Market", callback_data="m:market"),
        ],
        [
            InlineKeyboardButton("📜 Quests", callback_data="m:quests"),
            InlineKeyboardButton("🎁 Daily", callback_data="m:daily"),
        ],
        [
            InlineKeyboardButton("🏰 Guild", callback_data="m:guild"),
            InlineKeyboardButton("👹 World Boss", callback_data="m:raid"),
        ],
        [
            InlineKeyboardButton("🏦 Bank", callback_data="m:bank"),
            InlineKeyboardButton("👑 Crown", callback_data="m:crown"),
        ],
        [
            InlineKeyboardButton("🐾 Companions", callback_data="m:companions"),
            InlineKeyboardButton("🏆 Arena", callback_data="m:tournament"),
        ],
        [
            InlineKeyboardButton("📊 Leaderboard", callback_data="m:leaderboard"),
            InlineKeyboardButton("🌍 Travel", callback_data="m:travel"),
        ],
        [
            InlineKeyboardButton("ℹ️ About", callback_data="m:about"),
            InlineKeyboardButton("📖 Help", callback_data="m:help"),
        ],
    ]
    link_buttons = community_links(settings)
    if link_buttons:
        rows.append(link_buttons)
    return InlineKeyboardMarkup(rows)


def _bar(value: int, maximum: int, width: int = 12) -> str:
    maximum = max(1, maximum)
    filled = max(0, min(width, round(width * value / maximum)))
    return "▰" * filled + "▱" * (width - filled)


def _slug(value: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "_" for ch in value).strip("_")


def _enemy_image(name: str) -> str:
    return f"enemy_{_slug(name)}"


def _hunt_caption(preview: dict) -> str:
    lines = [
        f"🌍 {preview['dungeon_name'].upper()}",
        "━━━━━━━━━━━━━━━━━━",
        "",
        f"A villain blocks your path in {preview['dungeon_name']}!",
        f"👹 {preview['enemy_name']} (Level {preview['level']})",
        f"❤️ HP {preview['enemy_hp']} · ⚔️ ATK {preview['enemy_attack']}",
        f"⚡ Energy {preview['energy']} · You have {preview['player_energy']}",
        "",
        "Would you wanna fight or run?",
    ]
    if int(preview["player_energy"]) < int(preview["energy"]):
        lines.extend(["", "⚠️ Not enough energy — claim /daily or use an energy shard."])
    return "\n".join(lines)


def _hunt_markup(preview: dict, owner_id: int) -> InlineKeyboardMarkup:
    dungeon = preview["dungeon_id"]
    index = preview["enemy_index"]
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🟢 Fight", callback_data=f"h:f:{owner_id}:{dungeon}:{index}"),
                InlineKeyboardButton("🔴 Run Away", callback_data=f"h:x:{owner_id}:{dungeon}"),
            ],
            [InlineKeyboardButton("🔁 Find Another", callback_data=f"h:r:{owner_id}:{dungeon}")],
            [
                InlineKeyboardButton("🗺️ Travel", callback_data="m:travel"),
                InlineKeyboardButton("👹 World Boss", callback_data="m:raid"),
            ],
        ]
    )


async def _send_hunt_preview(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    preview: dict,
    owner_id: int,
    *,
    via_callback: bool = False,
) -> None:
    caption = _hunt_caption(preview)
    markup = _hunt_markup(preview, owner_id)
    image_key = _enemy_image(preview["enemy_name"])
    path = _asset_path(image_key)
    if via_callback:
        query = update.callback_query
        try:
            if query.message:
                await query.message.delete()
        except TelegramError:
            logger.info("Could not delete old hunt preview", exc_info=True)
        try:
            chat_id = query.message.chat_id if query.message else None
            if chat_id is None:
                return
            if path is not None:
                with path.open("rb") as photo:
                    await context.bot.send_photo(
                        chat_id=chat_id,
                        photo=photo,
                        caption=_trim_caption(caption, 1000),
                        reply_markup=markup,
                    )
            else:
                await context.bot.send_message(chat_id, caption, reply_markup=markup)
        except TelegramError:
            logger.warning("Could not send hunt preview", exc_info=True)
        return
    await _reply_visual(update, image_key, caption, markup)


@_protected
async def hunt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    dungeon_id = None
    if context.args:
        dungeon_id = context.args[0].lower()
        if dungeon_id not in DUNGEONS:
            raise GameError("Usage: /hunt [gate|rift|abyss].")
    preview = await _call(
        update, context.application.bot_data["game"].hunt_preview, _uid(update), dungeon_id
    )
    if preview:
        await _send_hunt_preview(update, context, preview, _uid(update))


async def hunt_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    parts = query.data.split(":")
    try:
        action = parts[1]
        owner_id = int(parts[2])
    except (IndexError, ValueError):
        await query.answer("That hunt expired.", show_alert=True)
        return
    if owner_id != query.from_user.id:
        await query.answer("This hunt belongs to another hunter.", show_alert=True)
        return
    game = context.application.bot_data["game"]
    if action == "f":
        try:
            dungeon_id, index = parts[3], int(parts[4])
        except (IndexError, ValueError):
            await query.answer("That hunt expired.", show_alert=True)
            return
        try:
            cost = int(HUNT_ENERGY_COST[dungeon_id])
            result = await asyncio.to_thread(
                game.start_battle, owner_id, dungeon_id, index, cost
            )
        except GameError as exc:
            await query.answer(str(exc), show_alert=True)
            return
        except Exception:
            logger.exception("Hunt battle failed; update=%s", update.update_id)
            await query.answer("Battle could not start. Try /hunt again.", show_alert=True)
            return
        try:
            await query.answer()
        except BadRequest:
            pass
        await _show_battle(update, result, edit=True)
        return
    if action == "r":
        dungeon_id = parts[3] if len(parts) > 3 and parts[3] in DUNGEONS else None
        try:
            preview = await asyncio.to_thread(game.hunt_preview, owner_id, dungeon_id)
        except GameError as exc:
            await query.answer(str(exc), show_alert=True)
            return
        except Exception:
            logger.exception("Hunt reroll failed; update=%s", update.update_id)
            await query.answer("Could not find a new target. Try /hunt again.", show_alert=True)
            return
        try:
            await query.answer()
        except BadRequest:
            pass
        await _send_hunt_preview(update, context, preview, owner_id, via_callback=True)
        return
    if action == "x":
        dungeon_id = parts[3] if len(parts) > 3 and parts[3] in DUNGEONS else "gate"
        try:
            await query.answer("You slip away without a fight.")
        except BadRequest:
            pass
        markup = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("🎲 Hunt Again", callback_data=f"h:r:{owner_id}:{dungeon_id}")],
                [InlineKeyboardButton("🗺️ Travel", callback_data="m:travel"),
                 InlineKeyboardButton("⬅️ Menu", callback_data="m:main")],
            ]
        )
        text = (
            "━━━━━━━━━━━━━━━━━━\n"
            "🏃 YOU SLIPPED AWAY\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "You vanish before the creature notices.\n"
            "No energy was spent.\n\n"
            "💡 Hunt again when you are ready."
        )
        try:
            if query.message is not None and getattr(query.message, "photo", None):
                await query.edit_message_caption(
                    caption=_trim_caption(text, 1000), reply_markup=markup
                )
            elif query.message:
                await query.edit_message_text(text, reply_markup=markup)
        except BadRequest:
            logger.info("Could not edit hunt flee screen", exc_info=True)
        return
    await query.answer("That hunt expired.", show_alert=True)


@_protected
async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    p = await _call(update, context.application.bot_data["game"].profile, _uid(update))
    if not p:
        return
    await update.effective_message.reply_text(
        f"BALANCE — {p['display_name']}\n"
        f"🪙 Coins: {int(p['coins']):,}\n"
        f"✨ Essence: {int(p['essence']):,}\n"
        f"⚡ Energy: {p['energy']}/{p['max_energy']}\n\n"
        "Earn with /daily, /quests, /explore, /hunt and /battle."
    )


@_protected
async def stamina_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    p = await _call(update, context.application.bot_data["game"].profile, _uid(update))
    if not p:
        return
    missing = max(0, int(p["max_energy"]) - int(p["energy"]))
    await update.effective_message.reply_text(
        f"⚡ ENERGY — {p['energy']}/{p['max_energy']}\n"
        f"Missing: {missing}\n\n"
        "Restore with /daily, /weekly, or /use energy_shard. "
        "Hunts cost 5–14 energy; dungeons cost 10–28."
    )


@_protected
async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = context.application.bot_data["settings"]
    links = "\n".join(
        f"• {label}: {url}"
        for label, url in (
            ("Channel", getattr(settings, "channel_link", "")),
            ("Group", getattr(settings, "group_link", "")),
            ("Support", getattr(settings, "support_link", "")),
        )
        if url
    )
    await update.effective_message.reply_text(
        "SETTINGS & LINKS\n"
        f"{links or 'No community links configured.'}\n\n"
        "Use /help for commands, /links for buttons, /rules for community rules."
    )


@_protected
async def flee(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    battle = await _call(
        update,
        context.application.bot_data["game"].battle_status,
        _uid(update),
        preserve_failure=True,
    )
    if battle is _CALL_FAILED or not battle:
        if battle is not _CALL_FAILED:
            await update.effective_message.reply_text(
                "You have no active battle. Hunt with /hunt or enter a dungeon with /battle gate."
            )
        return
    result = await _call(
        update,
        context.application.bot_data["game"].battle_action,
        _uid(update),
        battle["id"],
        int(battle["turn"]),
        "flee",
    )
    if result:
        await _show_battle(update, result)


@_protected
async def npc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    dungeon_id = context.args[0].lower() if context.args else "gate"
    if dungeon_id not in DUNGEONS:
        raise GameError("Usage: /npc [gate|rift|abyss].")
    dungeon = DUNGEONS[dungeon_id]
    lines = [f"🗺️ {dungeon['name'].upper()} — TIER {dungeon['tier']}", ""]
    for enemy in ENEMIES[dungeon_id]:
        lines.append(f"👹 {enemy['name']}")
    lines.extend(
        [
            "",
            "✦ <b>WORLD BOSSES</b>",
            *[f"👑 {boss['name']} — <i>{boss['description']}</i>" for boss in WORLD_BOSSES],
            "",
            f"Hunt cost: {HUNT_ENERGY_COST[dungeon_id]} energy",
            "Use /hunt to preview a target, then tap Battle or Flee.",
        ]
    )
    await _reply_visual(
        update,
        f"battle_{dungeon_id}",
        "\n".join(lines),
        markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("🌸 Hunt", callback_data="m:hunt"),
                    InlineKeyboardButton("👹 World Boss", callback_data="m:raid"),
                ],
                [InlineKeyboardButton("👑 Boss Gallery", callback_data="cr:bosses")],
            ]
        ),
        parse_mode="HTML",
    )


def _profile_text(profile: dict) -> str:
    guild = profile["guild"]["name"] if profile["guild"] else "No guild yet"
    class_info = CLASSES.get(str(profile["class_id"]), {})
    xp_goal = max(1, int(profile["level"]) * 100)
    xp_bar = _bar(int(profile["xp"]), xp_goal, 10)
    return (
        "👤 <b>HUNTER PROFILE</b>\n"
        "━━━━━━━━━━━━━━━━\n"
        f"🪪 <b>{escape(str(profile['display_name']))}</b> · Level {profile['level']} · Rank {profile['rank']}\n"
        f"✨ XP <code>{xp_bar}</code> {profile['xp']}/{xp_goal}\n\n"
        f"⚔️ <b>{escape(str(profile['class_name']))}</b> · <i>{escape(str(class_info.get('skill', 'Awakening skill')))}</i>\n"
        f"❤️ HP <b>{profile['base_hp']}</b>   🗡 ATK <b>{profile['attack']}</b>   🔷 Mana <b>{profile['max_mana']}</b>\n"
        f"🪙 Coins <b>{profile['coins']:,}</b>   🔥 Essence <b>{profile['essence']:,}</b>\n"
        f"🏦 Bank <b>{profile['bank']:,}</b>   ⚡ Energy <b>{profile['energy']}/{profile['max_energy']}</b>\n"
        f"👑 Collection <b>{profile['characters']}</b>/{profile['character_total']}\n\n"
        f"🐾 Echo ally: <b>{escape(str(profile['companion_name']))}</b>\n"
        f"🏰 Guild: <b>{escape(str(guild))}</b>"
    )


def _inventory_text(rows: list[dict]) -> str:
    if not rows:
        return "🎒 <b>INVENTORY</b>\n━━━━━━━━━━━━━━━━\nYour satchel is empty. Visit /shop to prepare for your next hunt."
    icons = {"healing_tonic": "🧪", "mana_vial": "🔷", "energy_shard": "⚡"}
    lines = ["🎒 <b>INVENTORY</b>", "━━━━━━━━━━━━━━━━"]
    for row in rows:
        item_id = str(row["item_id"])
        item = ITEMS.get(item_id, {})
        lines.append(
            f"{icons.get(item_id, '📦')} <b>{escape(str(row['name']))}</b> × <code>{row['quantity']}</code>\n"
            f"   <i>{escape(str(item.get('description', 'Stored hunter supply.')))}</i>"
        )
    lines.append("\nUse /use energy_shard or battle buttons to consume supplies.")
    return "\n".join(lines)


def _battle_markup(battle: dict) -> InlineKeyboardMarkup:
    bid, turn = battle["id"], battle["turn"]
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("⚔️ Strike", callback_data=f"b:{bid}:{turn}:attack"),
                InlineKeyboardButton("🌀 Skills", callback_data=f"b:{bid}:{turn}:skills"),
            ],
            [
                InlineKeyboardButton("💨 Dodge", callback_data=f"b:{bid}:{turn}:dodge"),
                InlineKeyboardButton("🧪 Tonic", callback_data=f"b:{bid}:{turn}:potion"),
            ],
            [
                InlineKeyboardButton("🔷 Mana Vial", callback_data=f"b:{bid}:{turn}:mana"),
                InlineKeyboardButton("🏃 Run", callback_data=f"b:{bid}:{turn}:flee"),
            ],
        ]
    )


def _battle_text(battle: dict) -> str:
    dungeon = DUNGEONS[battle["dungeon_id"]]
    result = battle.get("result") or {}
    status = battle["status"]
    name = str(battle.get("player_name") or "HUNTER")
    level = int(battle.get("player_level") or 0)
    if status == "victory":
        reward = result.get("reward") or {}
        lines = [
            "━━━━━━━━━━━━━━━━━━",
            "🌟 VICTORY!",
            "━━━━━━━━━━━━━━━━━━",
            "",
            f"⭐ +{int(reward.get('xp', 0)):,} XP",
            f"🪙 +{int(reward.get('coins', 0)):,} coins",
            f"✨ +{int(reward.get('essence', 0)):,} essence",
        ]
        if reward.get("levels"):
            lines.append(f"🆙 Level up ×{int(reward['levels'])}!")
        log = result.get("log") or []
        if log:
            lines.extend(["", f"> {log[-1]}"])
        lines.extend(["", "💡 Use /hunt to fight again or /battle to enter a dungeon."])
        return "\n".join(lines)
    if status == "defeat":
        return (
            "━━━━━━━━━━━━━━━━━━\n"
            "💀 DEFEAT\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"{battle.get('enemy_name', 'The enemy')} overwhelmed you.\n"
            "No rewards were earned.\n\n"
            "💡 Use /daily for energy, then /hunt to try again."
        )
    if status == "escaped":
        return (
            "━━━━━━━━━━━━━━━━━━\n"
            "🏃 ESCAPED\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "You retreated safely. No rewards were earned.\n\n"
            "💡 Use /hunt to find a new target."
        )
    if status == "expired":
        return (
            "━━━━━━━━━━━━━━━━━━\n"
            "⌛ BATTLE EXPIRED\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "You took too long. The enemy slipped away.\n\n"
            "💡 Use /hunt or /battle to start again."
        )
    state = result.get("state") or {}
    log = result.get("log") or []
    lines = [
        f"🌸 {dungeon['name'].upper()} · TURN {battle['turn']}",
        "━━━━━━━━━━━━━━━━━━",
    ]
    if log:
        lines.append(f"> {log[-1]}")
        if len(log) > 1:
            lines.append(f"> {log[-2]}")
        lines.append("━━━━━━━━━━━━━━━━━━")
    lines.extend(
        [
            "",
            f"🗡️ {name}" + (f" · Lv {level}" if level else ""),
            f"❤️ HP {battle['player_hp']}/{battle['player_max_hp']}",
            _bar(int(battle["player_hp"]), int(battle["player_max_hp"]), 10),
            f"🔷 Mana {battle['mana']}/{battle['max_mana']}",
            "",
            f"👹 {battle['enemy_name']}",
            f"❤️ HP {battle['enemy_hp']}/{battle['enemy_max_hp']}",
            _bar(int(battle["enemy_hp"]), int(battle["enemy_max_hp"]), 10),
        ]
    )
    effects = []
    for side, label in (("player", "You"), ("enemy", battle["enemy_name"])):
        side_state = state.get(side) or {}
        tags = []
        for effect in ("burn", "poison"):
            turns = int(side_state.get(effect) or 0)
            if turns:
                tags.append(f"{effect.title()} {turns}")
        if int(side_state.get("stun") or 0):
            tags.append("Stunned")
        if float(side_state.get("shield") or 0):
            tags.append("Shielded")
        if tags:
            effects.append(f"{label}: " + ", ".join(tags))
    if effects:
        lines.extend(["", "✦ EFFECTS", *effects])
    cooldown = int(state.get("skill_cd") or 0)
    if cooldown:
        lines.append(f"✦ Awakening recharge: {cooldown} turn(s)")
    lines.extend(["", "✦ Choose your action."])
    return "\n".join(lines)


async def _show_battle(update: Update, battle: dict, *, edit: bool = False) -> None:
    markup = _battle_markup(battle) if battle["status"] == "active" else None
    text = _battle_text(battle)
    if edit and update.callback_query:
        query = update.callback_query
        try:
            if query.message is not None and getattr(query.message, "photo", None):
                await query.edit_message_caption(caption=_trim_caption(text, 1000), reply_markup=markup)
            else:
                await query.edit_message_text(text, reply_markup=markup)
        except BadRequest:
            logger.info("Could not edit battle screen; leaving the previous view in place")
        return
    message = update.effective_message
    if message is None:
        return
    path = _asset_path(_enemy_image(battle["enemy_name"])) or _asset_path(
        f"battle_{battle['dungeon_id']}"
    )
    if path is not None and len(text) <= 1024:
        try:
            with path.open("rb") as photo:
                await message.reply_photo(photo=photo, caption=text, reply_markup=markup)
            return
        except TelegramError:
            logger.warning("Could not send battle visual", exc_info=True)
    await message.reply_text(text, reply_markup=markup)


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
        hunter = escape(str(user.first_name or "Hunter"))
        greeting = (
            "╭─『 WELCOME TO MONARCH 』\n"
            f"Hunter: {hunter}\n"
            "Your hunter file is ready.\n"
            f"╰{RULE}\n\n"
            "🎁 Starter kit · 2× Healing Tonic · Ash Hound echo\n"
            "🌀 Free skill · Quick Strike\n\n"
            "✦ Awaken. Hunt. Ascend.\n"
            "Everything lives in the buttons below.\n"
            "✦ Start: /class vanguard · /battle gate · /daily\n\n"
            f"{RULE}\n"
            "💡 /menu · /skills · /gacha · /help"
            if created
            else "╭─『 WELCOME BACK 』\n"
            f"Hunter: {hunter}\n"
            "Your hunter file is ready.\n"
            f"╰{RULE}\n\n"
            "✦ Continue your climb.\n"
            f"{RULE}\n"
            "💡 /menu for the hunter hub."
        )
        await _reply_visual(
            update,
            "announcement",
            f"<b>MONARCH</b>\n\n{greeting}",
            markup=main_menu(context.application.bot_data["settings"]),
        )


@_protected
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "╭─『 MONARCH HELP 』\n"
        f"{RULE}\n\n"
        "⚔️ <b>HUNT</b> — /hunt · /battle · /explore · /travel\n"
        "🌀 <b>GROW</b> — /class · /skills · /team · /codex\n"
        "🎁 <b>REWARDS</b> — /daily · /quests · /profile\n"
        "🏰 <b>SOCIAL</b> — /guild · /raid · /arena · /tournament\n"
        "💰 <b>ECONOMY</b> — /shop · /market · /inventory · /bank\n"
        "✨ <b>SUMMON</b> — /gacha · /crown · /companions\n"
        "🏆 <b>RANKS</b> — /leaderboard\n\n"
        f"{RULE}\n"
        "💡 /menu · /links · /rules · /about"
    )
    await update.effective_message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("🌸 Hunt", callback_data="m:hunt"),
                    InlineKeyboardButton("🌀 Skills", callback_data="m:skills"),
                ],
                [
                    InlineKeyboardButton("✨ Gacha", callback_data="m:gacha"),
                    InlineKeyboardButton("🏦 Bank", callback_data="m:bank"),
                ],
                [
                    InlineKeyboardButton("🏰 Guild", callback_data="m:guild"),
                    InlineKeyboardButton("👹 World Boss", callback_data="m:raid"),
                ],
                [InlineKeyboardButton("🏠 Open Hub", callback_data="m:home")],
            ]
        ),
    )


@_protected
async def about_command(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        f"╭─『 ABOUT MONARCH 』\n{RULE}\n\n{BOT_DESCRIPTION}\n\n{RULE}\n💡 /help · /menu · /rules"
    )


@_protected
async def userid(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat
    lines = ["╭─『 YOUR IDS 』", RULE, ""]
    if user is not None:
        handle = f" (@{user.username})" if user.username else ""
        name = " ".join(filter(None, [user.first_name, user.last_name])) or "Unknown"
        lines.append(f"User ID: {user.id}{handle}")
        lines.append(f"Name: {name}")
    if chat is not None:
        title = f" · {chat.title}" if chat.title else ""
        lines.append(f"Chat ID: {chat.id} ({chat.type}{title})")
    lines.extend(["", RULE, "💡 Keep these private · /query to report an issue"])
    if update.effective_message is not None:
        await update.effective_message.reply_text("\n".join(lines))


@_protected
async def query_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    args = list(context.args or [])
    user = update.effective_user
    chat = update.effective_chat
    if not args:
        await update.effective_message.reply_text(
            f"╭─『 SEND A QUERY 』\n{RULE}\n\n"
            "Usage: /query <your question or report>\n"
            "Example: /query my dungeon battle is stuck on turn 3\n\n"
            f"{RULE}\n💡 Your report reaches the admin team with your IDs."
        )
        return
    text = " ".join(args).strip()
    if len(text) < 3:
        await update.effective_message.reply_text("Please add a little more detail so the team can help.")
        return
    name = "Unknown"
    handle = "N/A"
    if user is not None:
        name = " ".join(filter(None, [user.first_name, user.last_name])) or (user.username or "Unknown")
        handle = f"@{user.username}" if user.username else "N/A"
    report = (
        "<b>NEW QUERY</b>\n\n"
        f"<b>From:</b> {html.escape(name)} ({html.escape(handle)})\n"
        f"<b>User ID:</b> <code>{user.id if user else 0}</code>\n"
        f"<b>Chat ID:</b> <code>{chat.id if chat else 0}</code>\n\n"
        f"<b>Message:</b>\n{html.escape(text)}"
    )
    delivered = False
    if settings.query_group_id is not None:
        try:
            await context.bot.send_message(
                chat_id=settings.query_group_id,
                text=report,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
            delivered = True
        except TelegramError:
            logger.exception("Could not deliver query to group %s", settings.query_group_id)
    if not delivered:
        for admin_id in sorted(settings.admin_ids | settings.owner_ids):
            try:
                await context.bot.send_message(chat_id=admin_id, text=report, parse_mode=ParseMode.HTML)
                delivered = True
            except TelegramError:
                logger.warning("Could not deliver query to admin %s", admin_id)
    link = (
        InlineKeyboardMarkup([[InlineKeyboardButton("Open Support Group", url=settings.support_link)]])
        if settings.support_link
        else None
    )
    if delivered:
        message = "Your query has been sent to the admin team. Keep this chat open for a reply."
    else:
        message = (
            "Your query could not be delivered right now. Please open the support group below."
        )
    await update.effective_message.reply_text(message, reply_markup=link)


@_protected
async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        f"╭─『 HUNTER HUB 』\n{RULE}\n\nChoose a destination.\n\n{RULE}\n💡 /help for commands · /profile for your file",
        reply_markup=main_menu(context.application.bot_data["settings"]),
    )


@_protected
async def nickname_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.effective_message.reply_text("Usage: /nickname <new name>")
        return
    result = await _call(update, context.application.bot_data["game"].nickname, _uid(update), " ".join(context.args))
    if result:
        await update.effective_message.reply_text(f"Your hunter is now known as {result}.")


@_protected
async def explore_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    result = await _call(update, context.application.bot_data["game"].explore, _uid(update))
    if not result:
        return
    if result["outcome"] == "coins":
        found = f"You found {result['coins']} coins."
    elif result["outcome"] == "essence":
        found = f"You found {result['essence']} rift essence."
    elif result["outcome"] == "item":
        found = f"You found a {result['item']}."
    else:
        found = f"Rare find! {result['coins']} coins and {result['essence']} rift essence."
    level_text = f"\n🆙 Level up ×{result['levels']}!" if result["levels"] else ""
    await _reply_visual(
        update,
        "explore",
        f"🌍 EXPLORATION COMPLETE\n{RULE}\n\n"
        f"{found}\n⭐ +{result['xp']} XP{level_text}\n"
        f"⚡ Energy {result['energy']}\n\n"
        f"{RULE}\n💡 Use /explore to search again.",
    )


def _lookup_entry(query: str):
    needle = query.strip().lower()
    for group, entries in (("Class", CLASSES), ("Companion", COMPANIONS), ("Dungeon", DUNGEONS), ("Item", ITEMS), ("Region", REGIONS)):
        for key, data in entries.items():
            if needle in (key.lower(), str(data.get("name", "")).lower()):
                return group, key, data
    return None


@_protected
async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.args:
        found = _lookup_entry(" ".join(context.args))
        if found is None:
            await update.effective_message.reply_text(
                f"╭─『 NO MATCH 』\n{RULE}\n\nTry /stats vanguard, /stats assassin, /stats arcanist, or a companion name."
            )
            return
        group, key, data = found
        lines = [
            f"╭─『 {str(data.get('name', key)).upper()} 』",
            RULE,
            f"{group} entry",
            "",
        ]
        for field in ("rarity", "hp", "attack", "mana", "skill", "multiplier", "description", "energy", "enemy_hp", "enemy_attack", "xp", "coins", "essence", "level", "price", "effect", "amount"):
            if field in data:
                lines.append(f"• {field.replace('_', ' ').title()}: {data[field]}")
        lines.extend(["", RULE, "💡 /codex · /data <name>"])
        await update.effective_message.reply_text("\n".join(lines))
        return
    p = await _call(update, context.application.bot_data["game"].profile, _uid(update))
    if not p:
        return
    crit = float(CLASS_CRIT.get(p["class_id"], 0.08))
    dodge = float(CLASS_DODGE.get(p["class_id"], 0.05))
    await update.effective_message.reply_text(
        f"╭─『 COMBAT STATS 』\n{RULE}\n"
        f"🗡️ {p['display_name']}\n"
        f"{p['class_name']} · {p['rank']} · Level {p['level']}\n\n"
        f"❤️ HP {p['base_hp']} · ⚔️ ATK {p['attack']} · 🔷 Mana {p['max_mana']}\n"
        f"🎯 Crit {crit * 100:.0f}% · 💨 Dodge {dodge * 100:.0f}%\n"
        f"🐺 Echo ally: {p['companion_name']}\n\n"
        f"{RULE}\n💡 /stats <class or companion> · /data <name>"
    )


@_protected
async def achievements_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    p = await _call(update, context.application.bot_data["game"].profile, _uid(update))
    if not p:
        return
    companions = await _call(update, context.application.bot_data["game"].companions, _uid(update)) or []
    milestones = [
        ("First awakening", bool(p.get("class_locked"))),
        ("Reach level 10", int(p["level"]) >= 10),
        ("Reach level 20", int(p["level"]) >= 20),
        ("Reach level 30", int(p["level"]) >= 30),
        ("Hold 1,000 coins", int(p["coins"]) >= 1000),
        ("Hold 10,000 coins", int(p["coins"]) >= 10000),
        ("Join a guild", bool(p.get("guild"))),
        ("Win 5 arena battles", int(p.get("wins", 0)) >= 5),
        ("Win 20 arena battles", int(p.get("wins", 0)) >= 20),
        ("Collect 3 Echo companions", len(companions) >= 3),
        ("Own every Echo companion", len(companions) >= len(COMPANIONS)),
    ]
    lines = [
        "╭─『 ACHIEVEMENTS 』",
        RULE,
        f"Unlocked {sum(1 for _, done in milestones if done)}/{len(milestones)}",
        "",
    ]
    lines.extend(f"{'✅' if done else '🔒'} {name}" for name, done in milestones)
    lines.extend(["", RULE, "💡 /profile · /stats · /leaderboard"])
    await _reply_visual(update, "achievements", "\n".join(lines))


@_protected
async def data_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.effective_message.reply_text(
            f"╭─『 DATA CODEX 』\n{RULE}\n\n"
            f"Classes {len(CLASSES)} · Dungeons {len(DUNGEONS)} · Items {len(ITEMS)} · "
            f"Companions {len(COMPANIONS)} · Regions {len(REGIONS)}\n\n"
            f"{RULE}\n💡 /data rift · /data arcanist · /data healing tonic"
        )
        return
    needle = " ".join(context.args).strip().lower()
    matches = []
    for group, entries in (("Class", CLASSES), ("Companion", COMPANIONS), ("Dungeon", DUNGEONS), ("Item", ITEMS), ("Region", REGIONS)):
        for key, data in entries.items():
            haystack = f"{key} {data.get('name', '')} {data.get('description', '')}".lower()
            if needle in haystack:
                matches.append(f"{group}: {data.get('name', key)} (/stats {data.get('name', key)})")
    if not matches:
        await update.effective_message.reply_text("No codex entry matched that search.")
        return
    await update.effective_message.reply_text(
        f"╭─『 DATA RESULTS 』\n{RULE}\n\n" + "\n".join(matches[:15]) + f"\n\n{RULE}\n💡 /stats <name> for full entry"
    )


@_protected
async def travel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    p = await _call(update, context.application.bot_data["game"].profile, _uid(update))
    if not p:
        return
    lines = [f"🗺️ REGIONS OF THE RIFT · Lv {p['level']}", RULE, ""]
    for region in REGIONS.values():
        level = int(region["level"])
        unlocked = int(p["level"]) >= level
        dungeon = DUNGEONS[str(region["dungeon"])]
        lines.append(
            f"{'✅' if unlocked else '🔒'} {region['name']} · Lv {level}\n"
            f"   {region['description']}\n"
            f"   ⚡ {dungeon['energy']} · "
            f"{'→ /hunt ' + str(region['dungeon']) + ' or /battle ' + str(region['dungeon']) if unlocked else 'Reach level ' + str(level) + ' to unlock.'}"
        )
    lines.extend(["", RULE, "💡 /explore for supplies · /hunt to preview a target"])
    await _reply_visual(update, "travel", "\n".join(lines))


@_protected
async def links_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    lines = ["╭─『 MONARCH LINKS 』", RULE, ""]
    if settings.channel_link:
        lines.append(f"Channel: {settings.channel_link}")
    if settings.group_link:
        lines.append(f"Group: {settings.group_link}")
    if settings.support_link:
        lines.append(f"Support: {settings.support_link}")
    if len(lines) == 2:
        lines.append("No community links are configured yet.")
    lines.extend(["", RULE, "💡 Join the group to grow the hunt"])
    await update.effective_message.reply_text(
        "\n".join(lines), reply_markup=InlineKeyboardMarkup([community_links(settings)]) if community_links(settings) else None
    )


@_protected
async def ping_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    started = time.perf_counter()
    await context.bot.get_me()
    elapsed = (time.perf_counter() - started) * 1000
    await update.effective_message.reply_text(
        f"╭─『 PING 』\n{RULE}\n\n🟢 Online · Telegram latency {elapsed:.0f} ms"
    )


ASK_TOPICS = {
    "battle": "/battle gate, /battle rift, or /battle abyss. Use /battle_status to resume a fight.",
    "fight": "/battle gate starts a turn-based dungeon fight. /hunt previews a wild enemy first.",
    "hunt": "/hunt [gate|rift|abyss] shows an enemy with its picture, then Battle or Flee.",
    "flee": "/flee escapes your active battle. In a hunt preview, tap Flee to leave without spending energy.",
    "escape": "/escape is an alias for /flee.",
    "npc": "/npc [gate|rift|abyss] lists enemies in a region.",
    "team": "/team manages your six-member team. /myteam is an alias.",
    "daily": "/daily claims your daily reward. Check /daily_status for your streak.",
    "shop": "/shop shows supplies. Buy with /buy <item_id> <qty>.",
    "market": "/market browses player listings. /market_list sells your items.",
    "trade": "Player trading uses the protected market: /market_list and /market_buy.",
    "guild": "/guild shows guild tools. Create with /guild_create, join with /guild_join.",
    "raid": "/raid shows the world boss. Attack once per hour with /raid_attack.",
    "quest": "/quests lists daily objectives. Claim with /quest_claim <id>.",
    "class": "/class vanguard, /class assassin, or /class arcanist. Choose once.",
    "profile": "/profile shows your stats. /stats shows combat numbers.",
    "travel": "/travel lists regions and unlock levels. Explore with /explore.",
    "explore": "/explore costs energy and can find coins, essence, or items.",
    "nickname": "/nickname <name> changes your hunter name.",
    "companion": "/companions shows echo allies. /extract summons one.",
    "tournament": "/tournament shows weekly arena standings. /tournament_join enters.",
    "link": "Use /links for the official channel, group, and support group.",
    "query": "/query <message> sends a report to the admin team.",
    "userid": "/userid shows your Telegram user ID and chat ID.",
}


@_protected
async def ask_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    question = " ".join(context.args or []).strip().lower()
    if not question:
        await update.effective_message.reply_text(
            f"╭─『 ASK 』\n{RULE}\n\n"
            "Ask about any command, e.g. /ask how do I battle?\n"
            f"{RULE}\n💡 Try: battle, team, daily, shop, market, guild, raid, travel, links"
        )
        return
    answers = [answer for key, answer in ASK_TOPICS.items() if key in question]
    if not answers:
        await update.effective_message.reply_text(
            f"╭─『 NO ANSWER 』\n{RULE}\n\nTry /help for every command, or ask about battle, team, daily, market, guild, raid, travel, or links."
        )
        return
    await update.effective_message.reply_text("XANDER SAYS\n\n" + "\n\n".join(dict.fromkeys(answers[:4])))


@_protected
async def rules_command(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "MONARCH RULES\n"
        "• Respect every hunter. No harassment, spam, or scams.\n"
        "• No exploits, bots, or multi-accounting.\n"
        "• Market and duel abuse is punished.\n"
        "• Report bugs with /query so the team can fix them.\n"
        "• Admin decisions are final."
    )


async def _send_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = _uid(update)
    p = await _call(update, context.application.bot_data["game"].profile, user_id)
    if not p:
        return
    caption = _profile_caption(p)
    settings: Settings = context.application.bot_data["settings"]
    markup = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Travel", callback_data="m:travel"),
                InlineKeyboardButton("Battle", callback_data="m:dungeons"),
            ],
            [InlineKeyboardButton("Inventory", callback_data="m:inventory"), InlineKeyboardButton("Guild", callback_data="m:guild")],
            *([[InlineKeyboardButton(label, url=url)] for label, url in (("Channel", settings.channel_link), ("Group", settings.group_link), ("Support", settings.support_link)) if url] if community_links(settings) else []),
            [InlineKeyboardButton("Back to hub", callback_data="m:home")],
        ]
    )
    message = update.effective_message
    if message is None:
        return
    photo: bytes | None = None
    try:
        bot = getattr(context, "bot", None)
        if bot is None:
            raise AttributeError("context.bot unavailable")
        photos = await bot.get_user_profile_photos(user_id, limit=1)
        if photos.total_count and photos.photos:
            file = await bot.get_file(photos.photos[0][-1].file_id)
            photo = bytes(await file.download_as_bytearray())
    except (TelegramError, AttributeError):
        logger.warning("Could not load Telegram profile photo for %s", user_id, exc_info=True)
    if photo is not None:
        try:
            await message.reply_photo(
                photo=photo,
                caption=caption,
                parse_mode=ParseMode.HTML,
                reply_markup=markup,
            )
            return
        except (TelegramError, AttributeError):
            logger.warning("Could not send profile photo for %s", user_id, exc_info=True)
    path = _asset_path("profile")
    if path is not None:
        try:
            with path.open("rb") as banner:
                await message.reply_photo(
                    photo=banner,
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                    reply_markup=markup,
                )
            return
        except (TelegramError, AttributeError):
            logger.warning("Could not send profile banner", exc_info=True)
    await message.reply_text(caption, parse_mode=ParseMode.HTML, reply_markup=markup)


@_protected
async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _send_profile(update, context)


@_protected
async def choose_class(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        p = await _call(update, context.application.bot_data["game"].profile, _uid(update))
        current = f"Current awakening: {p['class_name']}." if p else "You have not awakened yet."
        details = "\n".join(
            f"{v['name']} — {v['skill']} · HP {v['hp']} · ATK {v['attack']} · Mana {v['mana']} · Skill x{v['multiplier']}"
            for v in CLASSES.values()
        )
        await _reply_visual(
            update,
            "classes",
            "AWAKENING CLASSES\n\n"
            + current
            + "\nFirst awakening is free. Respec once at any time, and again at level 20.\n"
            "Use /class vanguard, /class assassin, or /class arcanist.",
            details=details,
        )
        return
    chosen = await _call(
        update, context.application.bot_data["game"].choose_class, _uid(update), context.args[0].lower()
    )
    if chosen:
        if chosen.get("changed"):
            detail = f"Class changed to {chosen['name']}."
        else:
            detail = f"Awakening complete. You are now a {chosen['name']}."
        remaining = chosen.get("remaining", 0)
        detail += f" Respecs remaining now: {remaining} (one more unlocks at level 20)."
        await update.effective_message.reply_text(detail)


@_protected
async def daily(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    result = await _call(update, context.application.bot_data["game"].daily, _uid(update))
    if result:
        markup = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("📅 Weekly Reward", callback_data="d:weekly"),
                    InlineKeyboardButton("📊 Status", callback_data="d:status"),
                ],
                [InlineKeyboardButton("🏠 Hub", callback_data="m:home")],
            ]
        )
        await _reply_visual(
            update,
            "daily",
            f"🎁 <b>DAILY SUPPLIES</b>\n{RULE}\n\n"
            f"🪙 <b>+{result['coins']:,}</b> coins\n"
            f"⚡ <b>+{result['energy_gained']}</b> energy\n"
            f"🔥 Streak <b>{result['streak']}</b> day(s)\n\n"
            f"{RULE}\n💡 Return tomorrow to keep the streak alive.",
            markup=markup,
            parse_mode="HTML",
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
        f"╭─『 CODEX 』\n{RULE}\n\n{text}\n\n{RULE}\n💡 /data <name> · /stats <class>",
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
            f"╭─『 PVP ARENA 』\n{RULE}\n"
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
        text = (
            f"╭─『 10-SLOT TOURNAMENT 』\n{RULE}\n"
            f"Week {result['week']}\n\n"
            + ("\n".join(f"Slot {r['slot']}: {r['display_name']}" for r in result["slots"]) or "No entrants yet.")
            + f"\n\n{RULE}\n💡 /tournament_join to enter"
        )
        await update.effective_message.reply_text(text)


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
            f"╭─『 TOURNAMENT MATCHES 』\n{RULE}\n"
            + (
                "\n".join(
                    f"{r['id']}: {r['player_a']} vs {r['player_b']} · {r['status']} · check in with /tournament_checkin {r['id']}"
                    for r in rows
                )
                or "No matches scheduled."
            )
            + f"\n\n{RULE}\n💡 /tournament_checkin <match_id>"
        )


@_protected
async def guild_raid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    result = await _call(update, context.application.bot_data["social"].guild_raid, _uid(update))
    if result:
        await update.effective_message.reply_text(
            f"╭─『 GUILD RAID 』\n{RULE}\n"
            f"👹 {result['boss']} · Cycle {result['cycle']}\n"
            f"Your damage: {result['damage']:,}\n"
            f"Boss HP: {_bar(int(result['hp']), int(result['max_hp']), 10)} {result['hp']:,}/{result['max_hp']:,}\n\n"
            + "\n".join(f"{i}. {x['display_name']} — {x['damage']:,}" for i, x in enumerate(result["top"], 1))
            + f"\n\n{RULE}\n💡 /raid_attack"
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
        team_lines = [
            "⚔️ ACTIVE BATTLE TEAM",
            RULE,
            "",
        ]
        if rows:
            team_lines.extend(
                f"{r['slot']}. ★ {r['display_name']} ({r['member_id']})" for r in rows
            )
        else:
            team_lines.append("No members yet.")
        team_lines.extend(
            ["", RULE, "💡 Add with /team <player_id> <slot 1-6> · /team_power"]
        )
        await _reply_visual(update, "team", "\n".join(team_lines))


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
        lines = ["📜 <b>DAILY OBJECTIVES</b>", RULE, ""]
        buttons = []
        for quest in rows:
            done = int(quest["progress"]) >= int(quest["goal"])
            claimed = bool(quest["claimed"])
            status = "✅ claimed" if claimed else ("🎁 ready" if done else "")
            lines.append(
                f"{'✅' if claimed else '•'} <b>{escape(str(quest['name']))}</b>\n"
                f"   <code>{_bar(int(quest['progress']), int(quest['goal']), 8)}</code> {quest['progress']}/{quest['goal']}"
                + (f" · {status}" if status else "")
                + ("" if claimed else f"\n   🪙 {quest['coins']} · ⭐ {quest['xp']} XP")
            )
            if done and not claimed:
                buttons.append(
                    [
                        InlineKeyboardButton(
                            f"🎁 Claim · {quest['name']}",
                            callback_data=f"q:{quest['id']}",
                        )
                    ]
                )
        buttons.append([InlineKeyboardButton("🏠 Hub", callback_data="m:home")])
        lines.extend(["", RULE, "💡 Claim completed objectives with the buttons below."])
        await _reply_visual(
            update,
            "quests",
            "\n".join(lines),
            markup=InlineKeyboardMarkup(buttons),
            parse_mode="HTML",
        )


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
        icons = {"healing_tonic": "🧪", "mana_vial": "🔷", "energy_shard": "⚡"}
        lines = ["🎒 <b>INVENTORY</b>", RULE, ""]
        buttons = []
        if rows:
            for row in rows:
                item_id = str(row["item_id"])
                price = int(ITEMS.get(item_id, {}).get("price", 0))
                lines.append(
                    f"{icons.get(item_id, '📦')} <b>{escape(str(row['name']))}</b> × <code>{row['quantity']}</code>\n"
                    f"   <i>{escape(str(ITEMS.get(item_id, {}).get('description', 'Stored supply.')))}</i>"
                )
                if item_id == "energy_shard":
                    buttons.append(
                        [
                            InlineKeyboardButton(
                                "⚡ Use Energy Shard", callback_data="i:energy_shard"
                            ),
                            InlineKeyboardButton(
                                f"🛒 Buy · {price}", callback_data=f"s:{item_id}:1"
                            ),
                        ]
                    )
        else:
            lines.append("Your satchel is empty. Visit the shop to prepare.")
        buttons.append(
            [
                InlineKeyboardButton("🛒 Shop", callback_data="m:shop"),
                InlineKeyboardButton("🏪 Market", callback_data="m:market"),
            ]
        )
        buttons.append([InlineKeyboardButton("🏠 Hub", callback_data="m:home")])
        lines.extend(["", RULE, "💡 Battle supplies are used from the battle screen."])
        await _reply_visual(
            update,
            "inventory",
            "\n".join(lines),
            markup=InlineKeyboardMarkup(buttons),
            parse_mode="HTML",
        )

@_protected
async def economy_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    limit = 20
    if context.args:
        if len(context.args) != 1 or not context.args[0].isdigit():
            raise GameError("Usage: /economy_history [number of entries].")
        limit = int(context.args[0])
    rows = await _call(update, context.application.bot_data["game"].economy_history, _uid(update), limit)
    if rows is not None:
        if not rows:
            await update.effective_message.reply_text("ECONOMY HISTORY\nNo recorded transactions yet.")
            return
        lines = ["ECONOMY HISTORY"]
        for row in rows:
            direction = (
                "→"
                if row["actor_id"] == _uid(update) and row["target_id"]
                else "←"
                if row["target_id"] == _uid(update)
                else "+"
            )
            lines.append(
                f"{direction} {row['action']} · {row['amount']} {row['currency']} · {row['created_at']}"
            )
        await update.effective_message.reply_text("\n".join(lines))


@_protected
async def companions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rows = await _call(update, context.application.bot_data["game"].companions, _uid(update))
    if rows is None:
        return
    lines = ["╭─『 ECHO COMPANIONS 』", RULE, ""]
    lines.extend(
        f"{'★ ' if row['equipped'] else '•'} {row['name']} ({row['id']})\n"
        f"   {row['rarity']} · Lv {row['level']} · +{row['attack'] + (row['level'] - 1)} ATK / +{row['hp'] + (row['level'] - 1) * 3} HP · shards {row['fragments']}/3"
        for row in rows
    )
    if not rows:
        lines.append("No companions owned yet.")
    lines.extend(["", RULE, "💡 /extract 25 essence · /equip <companion_id|none>"])
    await _reply_visual(update, "companions", "\n".join(lines))


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
    lines = ["🛒 <b>SUPPLY DEPOT</b>", RULE, ""]
    buttons = []
    icons = {"healing_tonic": "🧪", "mana_vial": "🔷", "energy_shard": "⚡"}
    for item in rows:
        lines.append(
            f"{icons.get(item['id'], '📦')} <b>{escape(str(item['name']))}</b> — <b>{item['price']:,}</b> coins\n"
            f"   <i>{escape(str(item['description']))}</i>"
        )
        buttons.append(
            [
                InlineKeyboardButton(
                    f"Buy 1 · {item['name']}", callback_data=f"s:{item['id']}:1"
                ),
                InlineKeyboardButton(
                    f"Buy 5 · {item['price'] * 5:,}", callback_data=f"s:{item['id']}:5"
                ),
            ]
        )
    buttons.append([InlineKeyboardButton("🏠 Hub", callback_data="m:home")])
    lines.extend(["", RULE, "💡 Supplies are delivered to your inventory instantly."])
    await _reply_visual(
        update,
        "shop",
        "\n".join(lines),
        markup=InlineKeyboardMarkup(buttons),
        parse_mode="HTML",
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
    categories = {"level", "xp", "coins", "arena", "wins", "guild"}
    category = context.args[0].lower() if context.args else "level"
    if not context.args:
        command = (
            (update.effective_message.text or "").split()[0].lstrip("/").split("@")[0]
            if update.effective_message and update.effective_message.text
            else ""
        )
        category = {
            "topexp": "xp",
            "toparena": "arena",
            "topwins": "wins",
            "topguild": "guild",
            "wealth": "coins",
        }.get(command, "level")
    if category not in categories:
        raise GameError(
            "Usage: /leaderboard [level|xp|coins|arena|wins|guild]. Example: /leaderboard arena"
        )
    rows = await _call(
        update, context.application.bot_data["game"].leaderboards, category, 10
    )
    if rows is None:
        return
    titles = {
        "level": "TOP HUNTERS · LEVEL",
        "xp": "TOP HUNTERS · EXPERIENCE",
        "coins": "RICHEST HUNTERS",
        "arena": "ARENA RATINGS",
        "wins": "MOST ARENA WINS",
        "guild": "TOP GUILDS",
    }
    templates = {
        "level": "Lv {value} · {detail} XP",
        "xp": "{value:,} XP · Lv {detail}",
        "coins": "{value:,} coins · Lv {detail}",
        "arena": "{value} rating · {detail} wins",
        "wins": "{value} wins · {detail} rating",
        "guild": "Lv {value} · {detail} guild XP",
    }
    lines = [titles[category], ""]
    if not rows:
        lines.append("No entries yet.")
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    for i, row in enumerate(rows, 1):
        marker = "👑" if i == 1 else medals.get(i, f"{i}.")
        lines.append(f"{marker} {row['name']} — " + templates[category].format(**row))
    lines.extend(
        [
            "",
            "Other boards: /leaderboard level · xp · coins · arena · wins · guild",
        ]
    )
    await _reply_visual(update, "leaderboard", "\n".join(lines))


@_protected
async def guild(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    result = await _call(update, context.application.bot_data["game"].guild_info, _uid(update))
    if result is None:
        return
    role_icons = {"leader": "👑", "officer": "⭐", "member": "👤"}
    if result["guild"]:
        g = result["guild"]
        lines = [
            f"🏰 <b>{escape(str(g['name']).upper())}</b>",
            RULE,
            f"Level <b>{g['level']}</b> · {g['xp']:,} XP · 🏦 {int(g.get('coins') or 0):,} coins",
            "",
            "✦ <b>MEMBERS</b>",
        ]
        lines.extend(
            f"{role_icons.get(str(m.get('role', 'member')), '👤')} {escape(str(m['display_name']))}"
            for m in result["members"]
        )
        lines.extend(["", RULE, "💡 Donate coins to fill the treasury and level up your guild."])
        markup = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("🏦 Treasury", callback_data="gl:treasury"),
                    InlineKeyboardButton("⚔️ Guild Raid", callback_data="gl:raid"),
                ],
                [
                    InlineKeyboardButton("💠 Donate", callback_data="gl:donate"),
                    InlineKeyboardButton("⚙️ Manage", callback_data="gl:manage"),
                ],
                [InlineKeyboardButton("🏠 Hub", callback_data="m:home")],
            ]
        )
        await _reply_visual(
            update, "guild", "\n".join(lines), markup=markup, parse_mode="HTML"
        )
        return
    lines = ["🏰 <b>GUILDS</b>", RULE, "You are not in a guild.", "", "✦ <b>AVAILABLE GUILDS</b>"]
    buttons = []
    if result["guilds"]:
        for entry in result["guilds"][:6]:
            lines.append(
                f"• <b>{escape(str(entry['name']))}</b> · Lv {entry['level']} · {entry['members']} members"
            )
            buttons.append(
                [
                    InlineKeyboardButton(
                        f"Join · {entry['name']}", callback_data=f"gl:join:{entry['id']}"
                    )
                ]
            )
    else:
        lines.append("No guilds yet. Be the first to create one.")
    buttons.append([InlineKeyboardButton("➕ Create Guild", callback_data="gl:create")])
    buttons.append([InlineKeyboardButton("🏠 Hub", callback_data="m:home")])
    lines.extend(["", RULE, "💡 Create your own guild or join an existing one."])
    await _reply_visual(
        update, "guild", "\n".join(lines), markup=InlineKeyboardMarkup(buttons), parse_mode="HTML"
    )


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
    if result is None:
        return
    boss = result["boss"]
    lines = [
        f"👹 <b>WORLD BOSS · {escape(str(boss['name']).upper())}</b>",
        RULE,
        f"<i>{escape(str(boss.get('description', 'A rift terror awakens.')))}</i>",
        "",
        f"❤️ <code>{_bar(int(boss['hp']), int(boss['max_hp']), 12)}</code>",
        f"   <b>{int(boss['hp']):,}</b> / {int(boss['max_hp']):,} HP · Cycle {boss['cycle']}",
        "",
        "🏆 <b>TOP DAMAGE</b>",
    ]
    if result["top"]:
        lines.extend(
            f"{i}. {escape(str(row['display_name']))} — <b>{int(row['total_damage']):,}</b>"
            for i, row in enumerate(result["top"], 1)
        )
    else:
        lines.append("No strikes yet. Be the first.")
    lines.extend(["", RULE, "💡 Each strike costs 20⚡ and has a 60 minute cooldown."])
    markup = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("⚔️ Attack · 20⚡", callback_data="rb:attack"),
                InlineKeyboardButton("📊 Rank", callback_data="rb:rank"),
            ],
            [
                InlineKeyboardButton("🔄 Refresh", callback_data="rb:refresh"),
                InlineKeyboardButton("🏠 Hub", callback_data="m:home"),
            ],
        ]
    )
    await _reply_visual(
        update,
        str(boss.get("art") or "boss_raid"),
        "\n".join(lines),
        markup=markup,
        parse_mode="HTML",
    )


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
            "╭─『 WEEKLY HUNTER ARENA 』",
            RULE,
            f"🗓️ Week {result['week']}",
            f"Entered: {result['joined']} · Score: {result['score']:,}",
            f"👥 {result['participants']} hunters · Prizes {', '.join(map(str, result['prizes']))} coins",
            "",
            "🏆 Top hunters:",
        ]
        lines.extend(f"{i}. {x['display_name']} — {x['score']:,}" for i, x in enumerate(result["leaders"], 1))
        lines.extend(["", RULE, "💡 /tournament_join · /tournament_claim"])
        await _reply_visual(update, "tournament", "\n".join(lines))


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
        page = int(context.args[0]) if context.args else 1
    except ValueError as exc:
        raise GameError("Usage: /market [page_number].") from exc
    if page < 1:
        raise GameError("Page number must be positive.")
    offset = (page - 1) * 10
    result = await _call(update, context.application.bot_data["market"].browse, _uid(update), offset)
    if result is None:
        return
    lines = [
        "🏪 <b>HUNTER MARKET</b>",
        RULE,
        f"📦 {result['total']} active listings · Page {page}",
        "",
    ]
    buttons = []
    if result["items"]:
        for listing in result["items"]:
            lines.append(
                f"#{listing['id']} <b>{escape(str(listing['item_name']))}</b> ×{listing['quantity']}\n"
                f"   🪙 {listing['price']:,} · {escape(str(listing['seller_name']))}"
            )
            buttons.append(
                [
                    InlineKeyboardButton(
                        f"Buy #{listing['id']} · {listing['price']:,}",
                        callback_data=f"mk:{listing['id']}",
                    )
                ]
            )
    else:
        lines.append("No listings on this page.")
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("◀️ Prev", callback_data="m:market"))
    if offset + 10 < int(result["total"]):
        nav.append(InlineKeyboardButton("Next ▶️", callback_data="m:market"))
    if nav:
        buttons.append(nav)
    buttons.append([_hub_button()])
    lines.extend(["", RULE, "💡 Sell with /market_list <item> <qty> <price>."])
    await _reply_visual(
        update,
        "market",
        "\n".join(lines),
        markup=InlineKeyboardMarkup(buttons),
        parse_mode="HTML",
    )


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


def _hub_button(label: str = "🏠 Hub") -> InlineKeyboardButton:
    return InlineKeyboardButton(label, callback_data="m:home")


async def _cb_edit(query, text: str, markup: InlineKeyboardMarkup | None = None, parse_mode: str | None = None) -> None:
    try:
        await query.edit_message_text(text, reply_markup=markup, parse_mode=parse_mode)
    except BadRequest:
        try:
            await query.edit_message_caption(caption=_trim_caption(text, 1000), reply_markup=markup, parse_mode=parse_mode)
        except BadRequest:
            logger.info("Could not update callback screen; leaving it in place")


def _gacha_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(f"✨ Summon 1× · {GACHA_COST}🔮", callback_data="ga:pull:1"),
                InlineKeyboardButton(f"✨ Summon 10× · {GACHA_TEN_COST}🔮", callback_data="ga:pull:10"),
            ],
            [
                InlineKeyboardButton("📚 Collection", callback_data="ga:collection"),
                InlineKeyboardButton("📊 Rates", callback_data="ga:rates"),
            ],
            [_hub_button()],
        ]
    )


def _gacha_text(collection: dict) -> str:
    rates = " · ".join(f"{rank} {GACHA_WEIGHTS[rank]:g}%" for rank in GACHA_RANKS)
    return (
        "✨ <b>SHADOW SUMMON</b>\n"
        f"{RULE}\n\n"
        f"🔮 Essence <b>{collection['essence']:,}</b> · Pity <b>{collection['pity']}/{GACHA_PITY}</b>\n"
        f"👥 Collected <b>{collection['owned']}</b> / {collection['total']}\n"
        f"❤️ +{collection['bonus_hp']} HP · 🗡 +{collection['bonus_atk']} ATK\n\n"
        "✦ <b>DROP RATES</b>\n"
        f"{rates}\n\n"
        f"1× · <b>{GACHA_COST}</b> essence · 10× · <b>{GACHA_TEN_COST}</b> essence\n"
        f"Guaranteed S or above every <b>{GACHA_PITY}</b> summons."
    )


def _bank_text(status: dict) -> str:
    total = max(1, int(status["total"]))
    return (
        "🏦 <b>MONARCH BANK</b>\n"
        f"{RULE}\n\n"
        f"🪙 Wallet <b>{status['coins']:,}</b> coins\n"
        f"🏦 Vault <b>{status['bank']:,}</b> coins\n"
        f"💠 Net worth <b>{status['total']:,}</b>\n\n"
        f"<code>{_bar(int(status['bank']), total, 12)}</code> vault share\n\n"
        f"{RULE}\n"
        f"💡 Transfers from {BANK_MIN_TRANSFER:,} coins. Your vault is safe from spending."
    )


def _bank_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("⬇️ 100", callback_data="bk:dep:100"),
                InlineKeyboardButton("⬇️ 1,000", callback_data="bk:dep:1000"),
                InlineKeyboardButton("⬇️ All", callback_data="bk:dep:all"),
            ],
            [
                InlineKeyboardButton("⬆️ 100", callback_data="bk:wd:100"),
                InlineKeyboardButton("⬆️ 1,000", callback_data="bk:wd:1000"),
                InlineKeyboardButton("⬆️ All", callback_data="bk:wd:all"),
            ],
            [_hub_button()],
        ]
    )


@_protected
async def skills_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rows = await _call(update, context.application.bot_data["game"].skills, _uid(update))
    if rows is None:
        return
    owned = [row for row in rows if row["owned"]]
    lines = [
        "🌀 <b>SKILL CODEX</b>",
        RULE,
        f"Owned <b>{len(owned)}</b> / {len(rows)} skills",
        "",
        "✦ <b>OWNED SKILLS</b>",
    ]
    if owned:
        lines.extend(
            f"• <b>{escape(str(row['name']))}</b> [{row['rank']}] · {row['mana']}🔷 · ×{row['multiplier']}\n"
            f"   <i>{escape(str(row['description']))}</i>"
            for row in owned
        )
    else:
        lines.append("No skills learned yet.")
    unowned = [row for row in rows if not row["owned"]]
    buttons = []
    if unowned:
        lines.extend(["", "✦ <b>SKILL SHOP</b>"])
        for row in unowned:
            lines.append(f"• {row['name']} [{row['rank']}] — 🪙 {int(row['price']):,}")
            buttons.append(
                [
                    InlineKeyboardButton(
                        f"Learn {row['name']} · 🪙 {int(row['price']):,}",
                        callback_data=f"sk:{row['id']}",
                    )
                ]
            )
    lines.extend(["", RULE, "💡 Choose skills from the battle screen with 🌀 Skills."])
    buttons.append([_hub_button()])
    await _reply_visual(
        update,
        "skills",
        "\n".join(lines),
        markup=InlineKeyboardMarkup(buttons),
        parse_mode="HTML",
    )


@_protected
async def gacha_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    collection = await _call(
        update, context.application.bot_data["game"].gacha_collection, _uid(update)
    )
    if collection is None:
        return
    await _reply_visual(
        update, "gacha", _gacha_text(collection), markup=_gacha_markup(), parse_mode="HTML"
    )


@_protected
async def bank_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    status = await _call(update, context.application.bot_data["game"].bank_status, _uid(update))
    if status is None:
        return
    await _reply_visual(
        update, "bank", _bank_text(status), markup=_bank_markup(), parse_mode="HTML"
    )


@_protected
async def crown_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    collection = await _call(
        update, context.application.bot_data["game"].gacha_collection, _uid(update)
    )
    if collection is None:
        return
    lines = [
        "👑 <b>CROWN COLLECTION</b>",
        RULE,
        f"Collected <b>{collection['owned']}</b> / {collection['total']} characters",
        f"❤️ +{collection['bonus_hp']} HP · 🗡 +{collection['bonus_atk']} ATK",
        "",
        "✦ <b>RANKS</b>",
    ]
    buttons = []
    for rank in GACHA_RANKS:
        count = len(collection["by_rank"][rank])
        total = sum(1 for char in GACHA_CHARACTERS.values() if char["rank"] == rank)
        lines.append(f"• <b>{rank}</b> — {count}/{total}")
        if count:
            buttons.append(
                [
                    InlineKeyboardButton(
                        f"{rank} · {count}/{total}", callback_data=f"cr:rank:{rank}"
                    )
                ]
            )
    buttons.append([InlineKeyboardButton("👹 World Bosses", callback_data="cr:bosses")])
    buttons.append([_hub_button()])
    lines.extend(["", RULE, "💡 Every collected character grants permanent HP and ATK."])
    await _reply_visual(
        update, "crown", "\n".join(lines), markup=InlineKeyboardMarkup(buttons), parse_mode="HTML"
    )


@_protected
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _is_operator(update, context):
        raise GameError("That command is not available.")
    stats = await _call(update, context.application.bot_data["game"].admin_stats)
    enabled = await _call(update, context.application.bot_data["game"].maintenance_enabled)
    text = (
        "👑 <b>ADMIN CONSOLE</b>\n"
        f"{RULE}\n\n"
        f"👥 Players <b>{stats['players'] if stats else 0}</b> · 🏰 Guilds <b>{stats['guilds'] if stats else 0}</b>\n"
        f"🪙 Coins in circulation <b>{stats['coins'] if stats else 0:,}</b>\n"
        f"🛠 Maintenance <b>{'ON' if enabled else 'OFF'}</b>\n\n"
        f"{RULE}\n"
        "💡 Hidden owner tools. Commands: /givecoins /ban /unban /admin_stats /admin_audit."
    )
    markup = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📊 Refresh", callback_data="adm:stats"),
                InlineKeyboardButton("🛠 Toggle Maint.", callback_data="adm:maint"),
            ],
            [
                InlineKeyboardButton("📜 Audit", callback_data="adm:audit"),
                InlineKeyboardButton("🪙 Grant Help", callback_data="adm:grant"),
            ],
            [InlineKeyboardButton("✖️ Close", callback_data="adm:close")],
        ]
    )
    await _reply_visual(update, "crown", text, markup=markup, parse_mode="HTML")


async def features_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    game: GameService = context.application.bot_data["game"]
    data = query.data
    prefix, _, rest = data.partition(":")
    try:
        if prefix == "d":
            if rest == "weekly":
                result = await asyncio.to_thread(game.weekly, query.from_user.id)
                await query.answer()
                await _cb_edit(
                    query,
                    f"📅 <b>WEEKLY REWARD</b>\n{RULE}\n\n🪙 +{result['coins']:,} coins\n"
                    f"Week {result['week']}\n\n{RULE}\n💡 Come back next week.",
                    InlineKeyboardMarkup([[_hub_button()]]),
                    parse_mode="HTML",
                )
            else:
                result = await asyncio.to_thread(game.daily_status, query.from_user.id)
                claim = "Claimed today" if result["claimed_today"] else "Ready to claim"
                await query.answer()
                await _cb_edit(
                    query,
                    f"📊 <b>DAILY STATUS</b>\n{RULE}\n\n{claim}\n"
                    f"🔥 Streak {result['streak']} day(s) · Next {result['next_streak']}\n"
                    f"⚡ {result['energy']}/{result['max_energy']}\n\n{RULE}",
                    InlineKeyboardMarkup([[_hub_button()]]),
                    parse_mode="HTML",
                )
            return
        if prefix == "q":
            result = await asyncio.to_thread(game.claim_quest, query.from_user.id, rest)
            await query.answer(f"+{result['coins']} coins · +{result['xp']} XP", show_alert=False)
            await _cb_edit(
                query,
                f"🎁 <b>OBJECTIVE CLAIMED</b>\n{RULE}\n\n🪙 +{result['coins']:,} coins\n"
                f"⭐ +{result['xp']} XP\n\n{RULE}",
                InlineKeyboardMarkup(
                    [[InlineKeyboardButton("📜 Quests", callback_data="m:quests"), _hub_button()]]
                ),
                parse_mode="HTML",
            )
            return
        if prefix == "s":
            item_id, _, raw_qty = rest.partition(":")
            quantity = int(raw_qty or "1")
            result = await asyncio.to_thread(game.buy_item, query.from_user.id, item_id, quantity)
            await query.answer(f"Bought {result['quantity']}× {result['item']}", show_alert=False)
            await _cb_edit(
                query,
                f"✅ <b>PURCHASE COMPLETE</b>\n{RULE}\n\n"
                f"📦 {result['quantity']}× {escape(result['item'])}\n"
                f"🪙 −{result['cost']:,} coins\n\n{RULE}",
                InlineKeyboardMarkup(
                    [[InlineKeyboardButton("🛒 Shop", callback_data="m:shop"), _hub_button()]]
                ),
                parse_mode="HTML",
            )
            return
        if prefix == "i":
            result = await asyncio.to_thread(game.use_energy_item, query.from_user.id, rest)
            await query.answer(f"+{result['gained']} energy", show_alert=False)
            await _cb_edit(
                query,
                f"⚡ <b>ENERGY RESTORED</b>\n{RULE}\n\n+{result['gained']} energy\n"
                f"Energy now {result['energy']}\n\n{RULE}",
                InlineKeyboardMarkup(
                    [[InlineKeyboardButton("🎒 Inventory", callback_data="m:inventory"), _hub_button()]]
                ),
                parse_mode="HTML",
            )
            return
        if prefix == "sk":
            result = await asyncio.to_thread(game.learn_skill, query.from_user.id, rest)
            await query.answer(f"Learned {result['name']}", show_alert=False)
            await _cb_edit(
                query,
                f"🌀 <b>SKILL LEARNED</b>\n{RULE}\n\n"
                f"<b>{escape(result['name'])}</b> [{result['rank']}]\n"
                f"🪙 −{result['price']:,} coins\n\n{RULE}\n"
                "💡 Use it in battle with the 🌀 Skills button.",
                InlineKeyboardMarkup(
                    [[InlineKeyboardButton("🌀 Skill Codex", callback_data="m:skills"), _hub_button()]]
                ),
                parse_mode="HTML",
            )
            return
        if prefix == "ga":
            if rest == "collection":
                collection = await asyncio.to_thread(game.gacha_collection, query.from_user.id)
                await query.answer()
                lines = [
                    "📚 <b>SUMMON COLLECTION</b>",
                    RULE,
                    f"Collected <b>{collection['owned']}</b> / {collection['total']}",
                    "",
                ]
                for rank in GACHA_RANKS:
                    chars = collection["by_rank"][rank]
                    if chars:
                        lines.append(
                            f"<b>{rank}</b> · "
                            + ", ".join(
                                f"{escape(str(char['name']))}×{char['copies']}" for char in chars
                            )
                        )
                lines.extend(["", RULE, f"Pity <b>{collection['pity']}/{GACHA_PITY}</b>"])
                await _cb_edit(query, "\n".join(lines), _gacha_markup(), parse_mode="HTML")
                return
            if rest == "rates":
                rates = "\n".join(
                    f"{rank} — <b>{GACHA_WEIGHTS[rank]:g}%</b>" for rank in GACHA_RANKS
                )
                await query.answer()
                await _cb_edit(
                    query,
                    f"📊 <b>SUMMON RATES</b>\n{RULE}\n\n{rates}\n\n"
                    f"{RULE}\nPity guarantees S+ every {GACHA_PITY} summons.",
                    _gacha_markup(),
                    parse_mode="HTML",
                )
                return
            _, _, count_raw = rest.partition(":")
            count = int(count_raw or "1")
            result = await asyncio.to_thread(game.gacha_pull, query.from_user.id, count)
            best = max(result["results"], key=lambda r: GACHA_RANKS.index(r["rank"]))
            await query.answer(f"Best pull: {best['name']} [{best['rank']}]", show_alert=False)
            lines = [f"✨ <b>SUMMON ×{len(result['results'])}</b>", RULE, ""]
            for pull in result["results"]:
                tag = "🆕" if pull["new"] else "↻"
                lines.append(
                    f"{tag} <b>{escape(str(pull['name']))}</b> [{pull['rank']}]\n"
                    f"   <i>{escape(str(pull['title']))}</i>"
                )
            lines.extend(
                [
                    "",
                    RULE,
                    f"🔮 −{result['cost']} essence · Pity {result['pity']}/{GACHA_PITY}",
                    f"❤️ +{result['bonus_hp']} HP · 🗡 +{result['bonus_atk']} ATK",
                ]
            )
            await _cb_edit(query, "\n".join(lines), _gacha_markup(), parse_mode="HTML")
            return
        if prefix == "bk":
            action, _, raw_amount = rest.partition(":")
            if raw_amount == "all":
                status = await asyncio.to_thread(game.bank_status, query.from_user.id)
                amount = status["coins"] if action == "dep" else status["bank"]
            else:
                amount = int(raw_amount)
            if amount < BANK_MIN_TRANSFER:
                await query.answer(
                    f"Minimum transfer is {BANK_MIN_TRANSFER:,} coins.", show_alert=True
                )
                return
            if action == "dep":
                await asyncio.to_thread(game.bank_deposit, query.from_user.id, amount)
            else:
                await asyncio.to_thread(game.bank_withdraw, query.from_user.id, amount)
            status = await asyncio.to_thread(game.bank_status, query.from_user.id)
            await query.answer(f"{'Deposited' if action == 'dep' else 'Withdrew'} {amount:,} coins")
            await _cb_edit(query, _bank_text(status), _bank_markup(), parse_mode="HTML")
            return
        if prefix == "gl":
            action, _, argument = rest.partition(":")
            if action == "join":
                guild = await asyncio.to_thread(game.join_guild, query.from_user.id, int(argument))
                await query.answer(f"Joined {guild['name']}", show_alert=False)
                await _cb_edit(
                    query,
                    f"🏰 <b>GUILD JOINED</b>\n{RULE}\n\n<b>{escape(guild['name'])}</b>\n"
                    f"Level {guild['level']}\n\n{RULE}",
                    InlineKeyboardMarkup(
                        [[InlineKeyboardButton("🏰 Guild", callback_data="m:guild"), _hub_button()]]
                    ),
                    parse_mode="HTML",
                )
                return
            if action == "upgrade":
                result = await asyncio.to_thread(game.guild_upgrade, query.from_user.id)
                await query.answer(f"{result['name']} is now level {result['level']}")
            treasury = await asyncio.to_thread(game.guild_treasury, query.from_user.id)
            if action == "donate":
                await query.answer(
                    "Use /guild_donate <coins> to fill the treasury.", show_alert=True
                )
                return
            if action == "manage":
                await query.answer(
                    "Commands: /guild_promote /guild_demote /guild_kick /guild_rename /guild_disband",
                    show_alert=True,
                )
                return
            if action == "raid":
                await query.answer("Use /guild_raid to strike your guild boss.", show_alert=True)
                return
            await query.answer()
            lines = [
                f"🏦 <b>{escape(str(treasury['name']).upper())} · TREASURY</b>",
                RULE,
                f"Level <b>{treasury['level']}</b> · {treasury['xp']:,} XP",
                f"👥 {treasury['members']} members · Role {treasury['role']}",
                "",
                f"🏦 Vault <b>{treasury['coins']:,}</b> coins",
                f"⬆️ Upgrade cost <b>{treasury['upgrade_cost']:,}</b> coins",
                "",
                RULE,
                "💡 Donate with /guild_donate <coins>.",
            ]
            markup = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("⬆️ Upgrade Guild", callback_data="gl:upgrade"),
                        InlineKeyboardButton("💠 Donate", callback_data="gl:donate"),
                    ],
                    [
                        InlineKeyboardButton("🏰 Guild", callback_data="m:guild"),
                        _hub_button(),
                    ],
                ]
            )
            await _cb_edit(query, "\n".join(lines), markup, parse_mode="HTML")
            return
        if prefix == "rb":
            if rest == "attack":
                result = await asyncio.to_thread(game.raid_attack, query.from_user.id)
                await query.answer(f"Strike dealt {result['damage']:,} damage")
                status = await asyncio.to_thread(game.raid_status)
                boss = status["boss"]
                extra = " 💀 Boss defeated! A new boss has appeared." if result["defeated"] else ""
                await _cb_edit(
                    query,
                    f"⚔️ <b>STRIKE DELIVERED</b>\n{RULE}\n\n"
                    f"👹 {escape(str(result['boss']))}\n"
                    f"💥 <b>{result['damage']:,}</b> damage\n"
                    f"🪙 +{result['reward']:,} coins{extra}\n\n"
                    f"❤️ <code>{_bar(int(status['boss']['hp']), int(status['boss']['max_hp']), 12)}</code>\n"
                    f"   {int(status['boss']['hp']):,} / {int(status['boss']['max_hp']):,} HP",
                    InlineKeyboardMarkup(
                        [
                            [
                                InlineKeyboardButton("⚔️ Attack", callback_data="rb:attack"),
                                InlineKeyboardButton("📊 Rank", callback_data="rb:rank"),
                            ],
                            [_hub_button()],
                        ]
                    ),
                    parse_mode="HTML",
                )
                return
            status = await asyncio.to_thread(game.raid_status)
            if rest == "rank":
                boss = status["boss"]
                lines = [f"📊 <b>DAMAGE RANK · {escape(str(boss['name']).upper())}</b>", RULE, ""]
                if status["top"]:
                    lines.extend(
                        f"{i}. {escape(str(row['display_name']))} — <b>{int(row['total_damage']):,}</b> "
                        f"({row['hits']} hits)"
                        for i, row in enumerate(status["top"], 1)
                    )
                else:
                    lines.append("No damage recorded yet.")
                await query.answer()
                await _cb_edit(
                    query,
                    "\n".join(lines),
                    InlineKeyboardMarkup(
                        [[InlineKeyboardButton("◀️ Back", callback_data="rb:refresh"), _hub_button()]]
                    ),
                    parse_mode="HTML",
                )
                return
            boss = status["boss"]
            await query.answer()
            await _cb_edit(
                query,
                f"👹 <b>WORLD BOSS · {escape(str(boss['name']).upper())}</b>\n{RULE}\n\n"
                f"{int(boss['hp']):,} / {int(boss['max_hp']):,} HP · Cycle {boss['cycle']}\n\n"
                f"<code>{_bar(int(boss['hp']), int(boss['max_hp']), 12)}</code>",
                InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton("⚔️ Attack · 20⚡", callback_data="rb:attack"),
                            InlineKeyboardButton("📊 Rank", callback_data="rb:rank"),
                        ],
                        [_hub_button()],
                    ]
                ),
                parse_mode="HTML",
            )
            return
        if prefix == "cr":
            action, _, argument = rest.partition(":")
            if action == "bosses":
                lines = ["👹 <b>WORLD BOSS GALLERY</b>", RULE, ""]
                for index, boss in enumerate(WORLD_BOSSES, 1):
                    status = await asyncio.to_thread(game.raid_status)
                    lines.append(
                        f"{index}. <b>{boss['name']}</b>\n   <i>{boss['description']}</i>"
                    )
                lines.extend(["", RULE, f"Current: {escape(str(status['boss']['name']))}"])
                await query.answer()
                await _cb_edit(
                    query,
                    "\n".join(lines),
                    InlineKeyboardMarkup(
                        [[InlineKeyboardButton("◀️ Back", callback_data="m:crown"), _hub_button()]]
                    ),
                    parse_mode="HTML",
                )
                return
            collection = await asyncio.to_thread(game.gacha_collection, query.from_user.id)
            chars = collection["by_rank"].get(argument, [])
            lines = [f"👑 <b>{argument} COLLECTION</b>", RULE, ""]
            if chars:
                lines.extend(
                    f"• <b>{escape(str(char['name']))}</b> ×{char['copies']}\n"
                    f"   <i>{escape(str(char['title']))}</i>"
                    for char in chars
                )
            else:
                lines.append("No characters from this rank yet. Summon with /gacha.")
            await query.answer()
            await _cb_edit(
                query,
                "\n".join(lines),
                InlineKeyboardMarkup(
                    [[InlineKeyboardButton("◀️ Back", callback_data="m:crown"), _hub_button()]]
                ),
                parse_mode="HTML",
            )
            return
        if prefix == "adm":
            if not await _is_operator(update, context):
                await query.answer("Owner only.", show_alert=True)
                return
            if rest == "maint":
                enabled = await asyncio.to_thread(game.maintenance_enabled)
                await asyncio.to_thread(game.set_maintenance, query.from_user.id, not enabled)
                await query.answer(f"Maintenance {'OFF' if enabled else 'ON'}")
                await admin_command(update, context)
                return
            if rest == "audit":
                rows = await asyncio.to_thread(game.admin_audit, 10)
                lines = ["📜 <b>RECENT AUDIT</b>", RULE, ""]
                lines.extend(
                    f"• {escape(str(row['action']))} · {row['actor_id']} → {row['target_id'] or '—'}"
                    for row in rows
                ) if rows else lines.append("No audit entries.")
                await query.answer()
                await _cb_edit(
                    query,
                    "\n".join(lines),
                    InlineKeyboardMarkup(
                        [[InlineKeyboardButton("◀️ Back", callback_data="adm:stats"), _hub_button()]]
                    ),
                    parse_mode="HTML",
                )
                return
            if rest == "grant":
                await query.answer(
                    "Use /givecoins <player_id> <amount> to grant coins.", show_alert=True
                )
                return
            if rest == "close":
                await query.answer()
                await _cb_edit(query, "👑 Admin console closed.")
                return
            await query.answer()
            await admin_command(update, context)
            return
        if prefix == "mk":
            result = await asyncio.to_thread(
                context.application.bot_data["market"].buy, query.from_user.id, int(rest)
            )
            item_name = str(result.get("item") or result.get("item_name") or "item")
            await query.answer(
                f"Bought {result['quantity']}× {item_name}", show_alert=False
            )
            await _cb_edit(
                query,
                f"✅ <b>MARKET PURCHASE</b>\n{RULE}\n\n"
                f"📦 {result['quantity']}× {escape(item_name)}\n"
                f"🪙 −{result['price']:,} coins\n\n{RULE}",
                InlineKeyboardMarkup(
                    [[InlineKeyboardButton("🏪 Market", callback_data="m:market"), _hub_button()]]
                ),
                parse_mode="HTML",
            )
            return
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    except Exception:
        logger.exception("Feature callback failed; update=%s", update.update_id)
        try:
            await query.answer("Something went wrong. Try again.", show_alert=True)
        except BadRequest:
            pass
        return
    await query.answer("Unknown action.", show_alert=True)


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
    "economy_history": economy_history,
    "about": about_command,
    "userid": userid,
    "query": query_command,
    "bag": inventory,
    "top": leaderboard,
    "topexp": leaderboard,
    "toparena": leaderboard,
    "topwins": leaderboard,
    "topguild": leaderboard,
    "wealth": leaderboard,
    "myteam": team,
    "mycharacters": companions,
    "stats": stats_command,
    "data": data_command,
    "travel": travel_command,
    "explore": explore_command,
    "nickname": nickname_command,
    "links": links_command,
    "ping": ping_command,
    "ask": ask_command,
    "rules": rules_command,
    "achievements": achievements_command,
    "hunt": hunt,
    "skills": skills_command,
    "gacha": gacha_command,
    "bank": bank_command,
    "crown": crown_command,
    "skill": skills_command,
    "summon": gacha_command,
    "vault": bank_command,
    "collection": crown_command,
    "worldboss": raid,
    "wb": raid,
    "admin": admin_command,
    "adventure": hunt,
    "flee": flee,
    "escape": flee,
    "npc": npc,
    "status": profile,
    "id": userid,
    "inv": inventory,
    "pets": companions,
    "clan": guild,
    "boss": raid,
    "balance": balance_command,
    "stamina": stamina_command,
    "settings": settings_command,
    "unstuck": flee,
}

PUBLIC_COMMANDS = [
    ("start", "Create your hunter file"),
    ("menu", "Open the hunter hub"),
    ("profile", "View your hunter card"),
    ("class", "Choose your awakening class"),
    ("hunt", "Preview a wild enemy and fight"),
    ("battle", "Enter a dungeon"),
    ("explore", "Explore for coins and supplies"),
    ("travel", "View regions and unlocks"),
    ("daily", "Claim daily supplies"),
    ("quests", "View and claim daily objectives"),
    ("skills", "Skill codex and skill shop"),
    ("gacha", "Summon Solo Leveling characters"),
    ("inventory", "Open your satchel"),
    ("shop", "Buy hunter supplies"),
    ("market", "Trade with other hunters"),
    ("guild", "Guild hub and treasury"),
    ("raid", "World boss battle"),
    ("bank", "Store coins safely"),
    ("crown", "View your character collection"),
    ("companions", "View your echo allies"),
    ("team", "Manage your six-member team"),
    ("arena", "PvP rating leaderboard"),
    ("tournament", "Weekly arena standings"),
    ("leaderboard", "Top hunters and guilds"),
    ("codex", "Browse classes, dungeons and items"),
    ("links", "Channel, group and support"),
    ("help", "Commands and quick buttons"),
    ("about", "Learn about Monarch"),
]


async def _render_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    action = query.data[2:]
    if action == "profile":
        result = await asyncio.to_thread(context.application.bot_data["game"].profile, query.from_user.id)
        text = _profile_text(result)
    elif action == "dungeons":
        text = (
            "CHOOSE YOUR HUNT\n\nSelect a dungeon to begin. Higher tiers unlock as you grow.\n\n"
            "Or preview a wild enemy with /hunt gate, /hunt rift, or /hunt abyss."
        )
        buttons = [
            [
                InlineKeyboardButton(
                    f"{DUNGEONS[key]['name']} · Tier {DUNGEONS[key]['tier']} · {DUNGEONS[key]['energy']} energy",
                    callback_data=f"m:battle_{key}",
                )
            ]
            for key in ("gate", "rift", "abyss")
        ]
        buttons.append([InlineKeyboardButton("Back to Hub", callback_data="m:home")])
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return
    elif action in {"battle_gate", "battle_rift", "battle_abyss"}:
        dungeon_id = action.removeprefix("battle_")
        result = await asyncio.to_thread(
            context.application.bot_data["game"].start_battle, query.from_user.id, dungeon_id
        )
        await _show_battle(update, result, edit=True)
        return
    elif action == "home":
        await query.edit_message_text(
            "MONARCH HUNTER HUB\nChoose your next destination.", reply_markup=main_menu(context.application.bot_data["settings"])
        )
        return
    elif action == "about":
        text = BOT_DESCRIPTION
    elif action == "help":
        text = "Use /help for every command. Start with /class, /battle gate, /daily, and /codex."
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
        text = _inventory_text(rows)
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
    elif action == "explore":
        result = await asyncio.to_thread(
            context.application.bot_data["game"].explore, query.from_user.id
        )
        if result["outcome"] == "coins":
            found = f"You found {result['coins']} coins."
        elif result["outcome"] == "essence":
            found = f"You found {result['essence']} rift essence."
        elif result["outcome"] == "item":
            found = f"You found a {result['item']}."
        else:
            found = f"Rare find! {result['coins']} coins and {result['essence']} rift essence."
        level_text = f"\nLevel up ×{result['levels']}!" if result["levels"] else ""
        text = (
            f"EXPLORING THE RIFT\n\n{found}\n+{result['xp']} XP{level_text}\n\n"
            f"Energy remaining: {result['energy']}"
        )
    elif action == "raid":
        result = await asyncio.to_thread(context.application.bot_data["game"].raid_status)
        b = result["boss"]
        text = (
            f"WORLD RAID · {b['name']}\n{RULE}\n"
            f"❤️ {_bar(int(b['hp']), int(b['max_hp']), 12)}\n"
            f"   {b['hp']:,} / {b['max_hp']:,} HP · Cycle {b['cycle']}\n\n"
            f"<i>{escape(str(b.get('description', 'A rift terror awakens.')))}</i>"
        )
        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("⚔️ Attack · 20⚡", callback_data="rb:attack"),
                        InlineKeyboardButton("📊 Rank", callback_data="rb:rank"),
                    ],
                    [InlineKeyboardButton("🏠 Hub", callback_data="m:home")],
                ]
            ),
        )
        return
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
    await query.edit_message_text(
        text,
        reply_markup=main_menu(context.application.bot_data["settings"]),
        parse_mode="HTML",
    )


async def _menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    rich = {
        "m:travel": travel_command,
        "m:explore": explore_command,
        "m:hunt": hunt,
        "m:shop": shop,
        "m:quests": quests,
        "m:skills": skills_command,
        "m:gacha": gacha_command,
        "m:bank": bank_command,
        "m:crown": crown_command,
        "m:leaderboard": leaderboard,
    }
    if query.data in rich:
        try:
            await rich[query.data](update, context)
        except GameError as exc:
            if query.message:
                await query.message.reply_text(str(exc))
        except Exception:
            logger.exception("Rich menu callback failed; update=%s", update.update_id)
            if query.message:
                await query.message.reply_text("Could not load that section. Try again shortly.")
        return
    try:
        await _render_menu_callback(update, context)
    except GameError as exc:
        text = str(exc)
        try:
            await query.edit_message_text(text, reply_markup=main_menu(context.application.bot_data["settings"]))
        except BadRequest:
            if query.message:
                await query.message.reply_text(text, reply_markup=main_menu(context.application.bot_data["settings"]))
    except Exception:
        logger.exception("Menu callback failed; update=%s", update.update_id)
        text = "Could not load that section. Try the matching command or /menu again."
        try:
            await query.edit_message_text(text, reply_markup=main_menu(context.application.bot_data["settings"]))
        except BadRequest:
            if query.message:
                await query.message.reply_text(text, reply_markup=main_menu(context.application.bot_data["settings"]))


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
    for action, value in (
        ("set_my_commands", commands),
        ("set_my_name", BOT_NAME),
        ("set_my_short_description", BOT_SHORT_DESCRIPTION),
        ("set_my_description", BOT_DESCRIPTION),
    ):
        try:
            await getattr(application.bot, action)(value)
        except TelegramError:
            logger.warning("Could not run Telegram %s during startup", action, exc_info=True)


async def _on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Unhandled Telegram update failure", exc_info=context.error)


def build_application(settings: Settings, game: GameService) -> Application:
    application = (
        ApplicationBuilder()
        .token(settings.bot_token)
        .connect_timeout(30)
        .read_timeout(30)
        .write_timeout(30)
        .pool_timeout(30)
        .post_init(_post_init)
        .build()
    )
    application.bot_data["game"] = game
    application.bot_data["settings"] = settings
    application.bot_data["market"] = MarketplaceService(game)
    application.bot_data["tournament"] = TournamentService(game)
    application.bot_data["social"] = SocialService(game)
    for name, handler in COMMANDS.items():
        application.add_handler(CommandHandler(name, handler))
    application.add_handler(CallbackQueryHandler(battle_callback, pattern=r"^b:"))
    application.add_handler(CallbackQueryHandler(hunt_callback, pattern=r"^h:"))
    application.add_handler(CallbackQueryHandler(_menu_callback, pattern=r"^m:"))
    application.add_handler(CallbackQueryHandler(_codex_callback, pattern=r"^c:"))
    application.add_handler(
        CallbackQueryHandler(features_callback, pattern=r"^(d|q|s|i|sk|ga|bk|gl|rb|cr|adm|mk):")
    )
    application.add_error_handler(_on_error)
    return application
