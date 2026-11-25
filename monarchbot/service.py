from __future__ import annotations

import json
import random
import re
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from .catalog import (
    BATTLE_TIMEOUT_SECONDS,
    CLASS_CRIT,
    CLASS_DODGE,
    CLASSES,
    COMPANION_EXTRACT_COST,
    COMPANIONS,
    DAILY_BASE_COINS,
    DAILY_MAX_STREAK_BONUS,
    DUNGEONS,
    ENEMY_SPECIAL_EVERY,
    EXPLORE_ENERGY_COST,
    ENEMIES,
    GUILD_DONATION_MAX,
    GUILD_DONATION_MIN,
    GUILD_MAX_MEMBERS,
    ITEMS,
    RAID_COOLDOWN_SECONDS,
    RAID_ENERGY_COST,
    SKILL_COOLDOWN_TURNS,
    SKILL_MANA_COST,
    HUNT_ENERGY_COST,
    BANK_MAX_TRANSFER,
    BANK_MIN_TRANSFER,
    DEFAULT_SKILL,
    GACHA_BONUS,
    GACHA_CHARACTERS,
    GACHA_COST,
    GACHA_PITY,
    GACHA_RANKS,
    GACHA_TEN_COST,
    GACHA_WEIGHTS,
    GUILD_UPGRADE_BASE,
    SKILLS,
    WORLD_BOSSES,
)
from .database import Database


class GameError(Exception):
    """A safe, user-facing game-rule error."""


class GameService:
    def __init__(
        self,
        db: Database,
        *,
        rng: random.Random | None = None,
        clock: Callable[[], datetime] | None = None,
        epoch: Callable[[], float] | None = None,
    ) -> None:
        self.db = db
        self.rng = rng or random.Random()
        self.clock = clock or (lambda: datetime.now(UTC))
        self.epoch = epoch or time.time

    def _today(self) -> str:
        return self.clock().astimezone(UTC).date().isoformat()

    def _current_week(self) -> str:
        iso = self.clock().astimezone(UTC).date().isocalendar()
        return f"{iso.year}-W{iso.week:02d}"

    def _require_player(self, con, player_id: int):
        row = con.execute("SELECT * FROM players WHERE telegram_id = ?", (player_id,)).fetchone()
        if row is None:
            raise GameError("You do not have a hunter profile yet. Use /start first.")
        banned = con.execute(
            "SELECT reason,expires_at FROM banned_players WHERE telegram_id = ?", (player_id,)
        ).fetchone()
        if banned:
            if banned["expires_at"] is not None and int(banned["expires_at"]) <= int(self.epoch()):
                con.execute("DELETE FROM banned_players WHERE telegram_id=?", (player_id,))
                return row
            raise GameError("This account is restricted from game actions. Contact the bot administrator.")
        return row

    @staticmethod
    def _audit(
        con, actor_id: int, action: str, target_id: int | None = None, reason: str = "", detail: str = ""
    ) -> None:
        con.execute(
            "INSERT INTO admin_audit(actor_id,action,target_id,reason,detail) VALUES(?,?,?,?,?)",
            (actor_id, action, target_id, reason[:240], detail[:500]),
        )

    @staticmethod
    def _ledger(
        con,
        actor_id: int,
        target_id: int | None,
        currency: str,
        amount: int,
        action: str,
        reference: str = "",
        detail: str = "",
    ) -> None:
        if currency not in {"coins", "energy", "item"} or amount <= 0:
            raise GameError("Invalid economy ledger entry.")
        con.execute(
            "INSERT INTO economy_ledger(actor_id,target_id,currency,amount,action,reference,detail) VALUES(?,?,?,?,?,?,?)",
            (actor_id, target_id, currency, amount, action, reference[:80], detail[:240]),
        )

    def economy_history(self, player_id: int, limit: int = 20) -> list[dict]:
        limit = max(1, min(50, int(limit)))
        with self.db.reader() as con:
            self._require_player(con, player_id)
            rows = con.execute(
                "SELECT * FROM economy_ledger WHERE actor_id=? OR target_id=? ORDER BY id DESC LIMIT ?",
                (player_id, player_id, limit),
            ).fetchall()
            return [dict(row) for row in rows]

    def has_admin_role(
        self, player_id: int, configured_admins: frozenset[int], minimum: str = "admin"
    ) -> bool:
        if player_id in configured_admins:
            return True
        with self.db.reader() as con:
            row = con.execute("SELECT role FROM admin_roles WHERE player_id=?", (player_id,)).fetchone()
            return row is not None and (minimum == "moderator" or row["role"] == "admin")

    def grant_admin_role(self, actor_id: int, target_id: int, role: str) -> dict:
        if role not in {"admin", "moderator"}:
            raise GameError("Role must be admin or moderator.")
        with self.db.transaction() as con:
            self._require_player(con, target_id)
            con.execute(
                "INSERT INTO admin_roles(player_id,role,granted_by) VALUES(?,?,?) ON CONFLICT(player_id) DO UPDATE SET role=excluded.role,granted_by=excluded.granted_by,granted_at=CURRENT_TIMESTAMP",
                (target_id, role, actor_id),
            )
            self._audit(con, actor_id, "grant_admin_role", target_id, detail=role)
            return {"target_id": target_id, "role": role}

    def revoke_admin_role(self, actor_id: int, target_id: int) -> bool:
        with self.db.transaction() as con:
            cur = con.execute("DELETE FROM admin_roles WHERE player_id=?", (target_id,))
            if cur.rowcount == 0:
                raise GameError("That hunter has no database-backed admin role.")
            self._audit(con, actor_id, "revoke_admin_role", target_id)
            return True

    def admin_audit(self, limit: int = 20) -> list[dict]:
        limit = max(1, min(100, int(limit)))
        with self.db.reader() as con:
            return [
                dict(row)
                for row in con.execute(
                    "SELECT * FROM admin_audit ORDER BY id DESC LIMIT ?", (limit,)
                ).fetchall()
            ]

    def set_maintenance(self, actor_id: int, enabled: bool) -> bool:
        with self.db.transaction() as con:
            self._audit(con, actor_id, "maintenance", detail="on" if enabled else "off")
            con.execute(
                "INSERT INTO bot_settings(key,value) VALUES('maintenance',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                ("1" if enabled else "0",),
            )
            return enabled

    def maintenance_enabled(self) -> bool:
        with self.db.reader() as con:
            row = con.execute("SELECT value FROM bot_settings WHERE key='maintenance'").fetchone()
            return row is not None and row["value"] == "1"

    @staticmethod
    def _stats(player) -> tuple[int, int, int, str]:
        archetype = CLASSES.get(player["class_id"], CLASSES["vanguard"])
        level = int(player["level"])
        hp = int(archetype["hp"]) + (level - 1) * 10
        attack = int(archetype["attack"]) + (level - 1) * 2
        mana = int(archetype["mana"]) + (level - 1) * 3
        return hp, attack, mana, str(archetype["name"])

    @staticmethod
    def _rank(level: int) -> str:
        return next(
            rank
            for threshold, rank in ((50, "S"), (35, "A"), (20, "B"), (10, "C"), (5, "D"), (1, "E"))
            if level >= threshold
        )

    def _stats_with_companion(self, con, player) -> tuple[int, int, int, str]:
        hp, attack, mana, class_name = self._stats(player)
        companion_id = player["equipped_companion"]
        companion = COMPANIONS.get(companion_id)
        if companion is not None:
            owned = con.execute(
                "SELECT level FROM player_companions WHERE player_id=? AND companion_id=?",
                (player["telegram_id"], companion_id),
            ).fetchone()
            if owned is not None:
                companion_level = int(owned["level"])
                hp += int(companion["hp"]) + (companion_level - 1) * 3
                attack += int(companion["attack"]) + (companion_level - 1)
        char_hp, char_atk = self._character_bonus(con, int(player["telegram_id"]))
        return hp + char_hp, attack + char_atk, mana, class_name

    @staticmethod
    def _character_bonus(con, player_id: int) -> tuple[int, int]:
        rows = con.execute(
            "SELECT character_id FROM player_characters WHERE player_id=?", (player_id,)
        ).fetchall()
        hp = atk = 0
        for row in rows:
            char = GACHA_CHARACTERS.get(row["character_id"])
            if char is None:
                continue
            bonus_hp, bonus_atk = GACHA_BONUS[char["rank"]]
            hp += bonus_hp
            atk += bonus_atk
        return hp, atk

    @staticmethod
    def _add_coins(con, player_id: int, amount: int) -> None:
        if amount > 0:
            con.execute("UPDATE players SET coins = coins + ? WHERE telegram_id = ?", (amount, player_id))

    @staticmethod
    def _add_essence(con, player_id: int, amount: int) -> None:
        if amount > 0:
            con.execute("UPDATE players SET essence = essence + ? WHERE telegram_id = ?", (amount, player_id))

    @staticmethod
    def _award_xp(con, player_id: int, amount: int) -> int:
        row = con.execute("SELECT level, xp FROM players WHERE telegram_id = ?", (player_id,)).fetchone()
        level, xp = int(row["level"]), int(row["xp"]) + max(0, amount)
        levels = 0
        while xp >= level * 100:
            xp -= level * 100
            level += 1
            levels += 1
        con.execute(
            "UPDATE players SET level = ?, xp = ?, max_energy = max_energy + ?, energy = min(max_energy + ?, energy + ?) WHERE telegram_id = ?",
            (level, xp, levels * 5, levels * 5, levels * 5, player_id),
        )
        return levels

    @staticmethod
    def _guild_xp(con, player_id: int, amount: int) -> int:
        membership = con.execute(
            "SELECT guild_id FROM guild_members WHERE player_id = ?", (player_id,)
        ).fetchone()
        if membership is None:
            return 0
        guild = con.execute("SELECT level, xp FROM guilds WHERE id = ?", (membership["guild_id"],)).fetchone()
        xp = int(guild["xp"]) + max(0, amount)
        level, gained = int(guild["level"]), 0
        while xp >= level * 100:
            xp -= level * 100
            level += 1
            gained += 1
        con.execute("UPDATE guilds SET level = ?, xp = ? WHERE id = ?", (level, xp, membership["guild_id"]))
        return gained

    def register(self, player_id: int, username: str | None, display_name: str) -> tuple[dict, bool]:
        if player_id <= 0:
            raise GameError("Telegram account ID is invalid.")
        name = " ".join((display_name or "Hunter").split())[:64] or "Hunter"
        with self.db.transaction() as con:
            if con.execute("SELECT 1 FROM banned_players WHERE telegram_id = ?", (player_id,)).fetchone():
                raise GameError("This account is restricted from game actions.")
            cur = con.execute(
                "INSERT OR IGNORE INTO players(telegram_id,username,display_name) VALUES(?,?,?)",
                (player_id, username, name),
            )
            created = cur.rowcount == 1
            if not created:
                con.execute(
                    "UPDATE players SET username = ?, display_name = ? WHERE telegram_id = ?",
                    (username, name, player_id),
                )
            if created:
                con.execute(
                    "INSERT INTO inventory(player_id,item_id,quantity) VALUES(?,?,?)",
                    (player_id, "healing_tonic", 2),
                )
                con.execute(
                    "INSERT INTO player_companions(player_id,companion_id) VALUES(?, 'ash_hound')",
                    (player_id,),
                )
                con.execute(
                    "INSERT INTO player_skills(player_id,skill_id) VALUES(?,?)",
                    (player_id, DEFAULT_SKILL),
                )
            return dict(
                con.execute("SELECT * FROM players WHERE telegram_id = ?", (player_id,)).fetchone()
            ), created

    def nickname(self, player_id: int, name: str) -> str:
        cleaned = " ".join((name or "").split())[:32]
        if len(cleaned) < 2:
            raise GameError("Choose a nickname with at least 2 characters.")
        if len(cleaned.split()) > 3:
            raise GameError("Nicknames can be at most three words.")
        with self.db.transaction() as con:
            self._require_player(con, player_id)
            con.execute("UPDATE players SET display_name=? WHERE telegram_id=?", (cleaned, player_id))
        return cleaned

    def explore(self, player_id: int) -> dict:
        with self.db.transaction() as con:
            p = self._require_player(con, player_id)
            if int(p["energy"]) < EXPLORE_ENERGY_COST:
                raise GameError(
                    f"Exploring costs {EXPLORE_ENERGY_COST} energy. Claim /daily or use an Energy Shard."
                )
            con.execute(
                "UPDATE players SET energy=energy-? WHERE telegram_id=?", (EXPLORE_ENERGY_COST, player_id)
            )
            self._increment_quest(con, player_id, "spend_energy", EXPLORE_ENERGY_COST)
            level = int(p["level"])
            roll = self.rng.random()
            xp = 0
            if roll < 0.50:
                coins = max(10, 20 + level * 2) + self.rng.randint(0, 20)
                self._add_coins(con, player_id, coins)
                xp = 6 + level
                result = {"outcome": "coins", "coins": coins, "xp": xp}
            elif roll < 0.70:
                essence = 1 + (1 if level >= 5 else 0)
                self._add_essence(con, player_id, essence)
                xp = 8 + level
                result = {"outcome": "essence", "essence": essence, "xp": xp}
            elif roll < 0.90:
                item_id = self.rng.choice(["healing_tonic", "mana_vial", "energy_shard"])
                con.execute(
                    "INSERT INTO inventory(player_id,item_id,quantity) VALUES(?,?,1) ON CONFLICT(player_id,item_id) DO UPDATE SET quantity=quantity+1",
                    (player_id, item_id),
                )
                xp = 5 + level
                result = {"outcome": "item", "item": ITEMS[item_id]["name"], "xp": xp}
            else:
                coins = max(20, 45 + level * 4) + self.rng.randint(0, 30)
                essence = 2
                self._add_coins(con, player_id, coins)
                self._add_essence(con, player_id, essence)
                xp = 12 + level * 2
                result = {"outcome": "rare", "coins": coins, "essence": essence, "xp": xp}
            result["levels"] = self._award_xp(con, player_id, xp)
            result["energy"] = int(p["energy"]) - EXPLORE_ENERGY_COST
            return result

    def profile(self, player_id: int) -> dict:
        with self.db.reader() as con:
            p = self._require_player(con, player_id)
            hp, attack, mana, class_name = self._stats_with_companion(con, p)
            guild = con.execute(
                "SELECT g.id,g.name,g.level FROM guild_members m JOIN guilds g ON g.id=m.guild_id WHERE m.player_id=?",
                (player_id,),
            ).fetchone()
            arena = con.execute(
                "SELECT rating,wins,losses FROM arena_ratings WHERE player_id=?", (player_id,)
            ).fetchone()
            return {
                **dict(p),
                "class_name": class_name,
                "rank": self._rank(int(p["level"])),
                "companion_name": COMPANIONS.get(p["equipped_companion"], {}).get("name", "None"),
                "base_hp": hp,
                "attack": attack,
                "max_mana": mana,
                "guild": dict(guild) if guild else None,
                "arena_rating": int(arena["rating"]) if arena else 1000,
                "wins": int(arena["wins"]) if arena else 0,
                "losses": int(arena["losses"]) if arena else 0,
            }

    def companions(self, player_id: int) -> list[dict]:
        with self.db.reader() as con:
            player = self._require_player(con, player_id)
            rows = con.execute(
                "SELECT companion_id,level,fragments FROM player_companions WHERE player_id=? ORDER BY companion_id",
                (player_id,),
            ).fetchall()
            equipped = str(player["equipped_companion"])
            return [
                {
                    **dict(row),
                    **COMPANIONS[row["companion_id"]],
                    "equipped": row["companion_id"] == equipped,
                }
                for row in rows
                if row["companion_id"] in COMPANIONS
            ]

    def extract_companion(self, player_id: int) -> dict:
        with self.db.transaction() as con:
            player = self._require_player(con, player_id)
            if int(player["essence"]) < COMPANION_EXTRACT_COST:
                raise GameError(f"You need {COMPANION_EXTRACT_COST} essence for an echo extraction.")
            companion_id = self.rng.choice(tuple(COMPANIONS))
            owned = con.execute(
                "SELECT level,fragments FROM player_companions WHERE player_id=? AND companion_id=?",
                (player_id, companion_id),
            ).fetchone()
            level_up = False
            if owned is None:
                con.execute(
                    "INSERT INTO player_companions(player_id,companion_id) VALUES(?,?)",
                    (player_id, companion_id),
                )
                level, fragments = 1, 0
                duplicate = False
            else:
                duplicate = True
                level, fragments = int(owned["level"]), int(owned["fragments"])
                if level < 20:
                    fragments += 1
                    if fragments >= 3:
                        level += 1
                        fragments = 0
                        level_up = True
                else:
                    fragments = min(2, fragments + 1)
                con.execute(
                    "UPDATE player_companions SET level=?,fragments=? WHERE player_id=? AND companion_id=?",
                    (level, fragments, player_id, companion_id),
                )
            con.execute(
                "UPDATE players SET essence=essence-? WHERE telegram_id=?",
                (COMPANION_EXTRACT_COST, player_id),
            )
            return {
                "id": companion_id,
                "name": COMPANIONS[companion_id]["name"],
                "rarity": COMPANIONS[companion_id]["rarity"],
                "duplicate": duplicate,
                "level": level,
                "fragments": fragments,
                "level_up": level_up,
                "cost": COMPANION_EXTRACT_COST,
            }

    def equip_companion(self, player_id: int, companion_id: str) -> dict:
        companion_id = companion_id.lower()
        with self.db.transaction() as con:
            self._require_player(con, player_id)
            active = con.execute(
                "SELECT 1 FROM active_battles WHERE player_id=? AND status='active'", (player_id,)
            ).fetchone()
            if active:
                raise GameError("Finish or retreat from your active battle before changing companions.")
            if companion_id == "none":
                con.execute("UPDATE players SET equipped_companion='none' WHERE telegram_id=?", (player_id,))
                return {"id": "none", "name": "None"}
            if companion_id not in COMPANIONS:
                raise GameError("Unknown companion. Check /companions.")
            owned = con.execute(
                "SELECT 1 FROM player_companions WHERE player_id=? AND companion_id=?",
                (player_id, companion_id),
            ).fetchone()
            if owned is None:
                raise GameError(
                    "You do not own that companion. Use /extract after collecting enough essence."
                )
            con.execute(
                "UPDATE players SET equipped_companion=? WHERE telegram_id=?",
                (companion_id, player_id),
            )
            return {"id": companion_id, "name": COMPANIONS[companion_id]["name"]}

    def choose_class(self, player_id: int, class_id: str) -> dict:
        if class_id not in CLASSES:
            raise GameError("Choose one of: vanguard, assassin, arcanist.")
        with self.db.transaction() as con:
            p = self._require_player(con, player_id)
            active = con.execute(
                "SELECT 1 FROM active_battles WHERE player_id=? AND status='active'", (player_id,)
            ).fetchone()
            if active:
                raise GameError("Finish or leave your current battle before choosing an awakening class.")
            changes = int(p["class_changes"] or 0)
            previous = str(p["class_id"])
            if not p["class_locked"]:
                con.execute(
                    "UPDATE players SET class_id=?,class_locked=1,class_changes=0 WHERE telegram_id=?",
                    (class_id, player_id),
                )
                return {
                    "id": class_id,
                    "name": CLASSES[class_id]["name"],
                    "changed": False,
                    "changes": 0,
                    "remaining": 1 + (1 if int(p["level"]) >= 20 else 0),
                }
            if class_id == previous:
                raise GameError("That is already your awakening class.")
            if changes >= 1 and int(p["level"]) < 20:
                raise GameError(
                    "You already used your one free respec. Reach level 20 to unlock another class change."
                )
            if changes >= 2:
                raise GameError("You have used both class changes: one free respec and the level 20 respec.")
            con.execute(
                "UPDATE players SET class_id=?,class_changes=? WHERE telegram_id=?",
                (class_id, changes + 1, player_id),
            )
            return {
                "id": class_id,
                "name": CLASSES[class_id]["name"],
                "changed": True,
                "changes": changes + 1,
                "remaining": (1 if int(p["level"]) >= 20 else 0) if changes + 1 < 2 else 0,
            }

    def daily(self, player_id: int) -> dict:
        today = self._today()
        with self.db.transaction() as con:
            p = self._require_player(con, player_id)
            if p["daily_date"] == today:
                raise GameError("You have already claimed today's reward.")
            yesterday = (self.clock().astimezone(UTC).date() - timedelta(days=1)).isoformat()
            streak = int(p["daily_streak"]) + 1 if p["daily_date"] == yesterday else 1
            bonus = min(DAILY_MAX_STREAK_BONUS, (streak - 1) * 5)
            coins = DAILY_BASE_COINS + bonus
            before = int(p["energy"])
            energy = min(int(p["max_energy"]), before + 30)
            con.execute(
                "UPDATE players SET daily_date=?,daily_streak=?,coins=coins+?,energy=? WHERE telegram_id=?",
                (today, streak, coins, energy, player_id),
            )
            return {"coins": coins, "streak": streak, "energy_gained": energy - before, "energy": energy}

    def daily_status(self, player_id: int) -> dict:
        today = self._today()
        yesterday = (self.clock().astimezone(UTC).date() - timedelta(days=1)).isoformat()
        with self.db.reader() as con:
            player = self._require_player(con, player_id)
            claimed_today = player["daily_date"] == today
            current_streak = int(player["daily_streak"])
            next_streak = (
                current_streak
                if claimed_today
                else current_streak + 1
                if player["daily_date"] == yesterday
                else 1
            )
            return {
                "claimed_today": claimed_today,
                "streak": current_streak,
                "next_streak": next_streak,
                "energy": int(player["energy"]),
                "max_energy": int(player["max_energy"]),
                "daily_date": player["daily_date"],
            }

    def weekly(self, player_id: int) -> dict:
        week = self._current_week()
        with self.db.transaction() as con:
            player = self._require_player(con, player_id)
            if player["weekly_date"] == week:
                raise GameError("You have already claimed this week's reward.")
            reward = 1000 + int(player["level"]) * 50
            con.execute(
                "UPDATE players SET weekly_date=?,coins=coins+?,energy=min(max_energy,energy+50) WHERE telegram_id=?",
                (week, reward, player_id),
            )
            self._ledger(con, player_id, None, "coins", reward, "weekly_reward", week)
            return {"week": week, "coins": reward}

    def give_coins(self, sender_id: int, target_id: int, amount: int) -> dict:
        if sender_id == target_id:
            raise GameError("You cannot send coins to yourself.")
        if not 1 <= amount <= 5000:
            raise GameError("You can give between 1 and 5,000 coins.")
        with self.db.transaction() as con:
            sender = self._require_player(con, sender_id)
            target = self._require_player(con, target_id)
            if int(sender["coins"]) < amount:
                raise GameError("You do not have enough coins.")
            con.execute("UPDATE players SET coins=coins-? WHERE telegram_id=?", (amount, sender_id))
            con.execute("UPDATE players SET coins=coins+? WHERE telegram_id=?", (amount, target_id))
            self._ledger(con, sender_id, target_id, "coins", amount, "gift")
            return {"amount": amount, "target": target["display_name"]}

    def quests(self, player_id: int) -> list[dict]:
        today = self._today()
        definitions = (
            ("clear_gate", "Clear any dungeon", 1, 50, 35),
            ("use_skill", "Use a special skill in battle", 2, 55, 40),
            ("spend_energy", "Spend energy in dungeons", 30, 70, 45),
        )
        with self.db.transaction() as con:
            self._require_player(con, player_id)
            for quest_id, _, _, _, _ in definitions:
                con.execute(
                    "INSERT OR IGNORE INTO daily_quests(player_id,quest_date,quest_id) VALUES(?,?,?)",
                    (player_id, today, quest_id),
                )
            rows = con.execute(
                "SELECT quest_id,progress,claimed FROM daily_quests WHERE player_id=? AND quest_date=?",
                (player_id, today),
            ).fetchall()
            values = {row["quest_id"]: dict(row) for row in rows}
            return [
                {
                    "id": qid,
                    "name": name,
                    "goal": goal,
                    "coins": coins,
                    "xp": xp,
                    **values.get(qid, {"progress": 0, "claimed": 0}),
                }
                for qid, name, goal, coins, xp in definitions
            ]

    @staticmethod
    def _progress_quest(con, player_id: int, quest_id: str, amount: int = 1) -> None:
        today = datetime.now(UTC).date().isoformat()
        # Caller-provided game clock is used by creating a date-specific row before progress separately.
        con.execute(
            "UPDATE daily_quests SET progress=progress+? WHERE player_id=? AND quest_date=? AND quest_id=?",
            (amount, player_id, today, quest_id),
        )

    def claim_quest(self, player_id: int, quest_id: str) -> dict:
        definitions = {
            "clear_gate": (1, 50, 35),
            "use_skill": (2, 55, 40),
            "spend_energy": (30, 70, 45),
        }
        if quest_id not in definitions:
            raise GameError("Unknown quest. Check /quests.")
        goal, coins, xp = definitions[quest_id]
        today = self._today()
        with self.db.transaction() as con:
            self._require_player(con, player_id)
            row = con.execute(
                "SELECT progress,claimed FROM daily_quests WHERE player_id=? AND quest_date=? AND quest_id=?",
                (player_id, today, quest_id),
            ).fetchone()
            if row is None or row["progress"] < goal:
                raise GameError("That quest is not complete yet.")
            if row["claimed"]:
                raise GameError("That quest reward has already been claimed.")
            con.execute(
                "UPDATE daily_quests SET claimed=1 WHERE player_id=? AND quest_date=? AND quest_id=?",
                (player_id, today, quest_id),
            )
            self._add_coins(con, player_id, coins)
            levels = self._award_xp(con, player_id, xp)
            return {"coins": coins, "xp": xp, "levels": levels}

    def _enemy_stats(self, p, dungeon_id: str, enemy_def: dict) -> tuple[int, int]:
        dungeon = DUNGEONS[dungeon_id]
        scale_hp = float(enemy_def["hp_scale"])
        scale_attack = float(enemy_def["attack_scale"])
        enemy_hp = max(1, int((int(dungeon["enemy_hp"]) + (int(p["level"]) - 1) * 8) * scale_hp))
        enemy_attack = max(
            1, int((int(dungeon["enemy_attack"]) + (int(p["level"]) - 1) * 1.5) * scale_attack)
        )
        return enemy_hp, enemy_attack

    def hunt_preview(self, player_id: int, dungeon_id: str | None = None) -> dict:
        now = int(self.epoch())
        with self.db.transaction() as con:
            p = self._require_player(con, player_id)
            con.execute(
                "UPDATE active_battles SET status='expired' WHERE player_id=? AND status='active' AND expires_at<=?",
                (player_id, now),
            )
            active = con.execute(
                "SELECT id FROM active_battles WHERE player_id=? AND status='active'", (player_id,)
            ).fetchone()
            if active:
                raise GameError("Finish your active battle first. Use /battle_status or /flee.")
            minimum = {"gate": 1, "rift": 3, "abyss": 8}
            unlocked = [key for key, level in minimum.items() if int(p["level"]) >= level]
            if dungeon_id is None:
                dungeon_id = self.rng.choice(unlocked)
            if dungeon_id not in DUNGEONS:
                raise GameError("Choose /hunt gate, /hunt rift, or /hunt abyss.")
            if dungeon_id not in unlocked:
                raise GameError(f"You need hunter level {minimum[dungeon_id]} to hunt in this region.")
            enemies = ENEMIES[dungeon_id]
            index = self.rng.randrange(len(enemies))
            enemy_def = enemies[index]
            enemy_hp, enemy_attack = self._enemy_stats(p, dungeon_id, enemy_def)
            cost = int(HUNT_ENERGY_COST[dungeon_id])
            return {
                "dungeon_id": dungeon_id,
                "dungeon_name": DUNGEONS[dungeon_id]["name"],
                "tier": int(DUNGEONS[dungeon_id]["tier"]),
                "enemy_name": enemy_def["name"],
                "enemy_index": index,
                "enemy_hp": enemy_hp,
                "enemy_attack": enemy_attack,
                "level": int(p["level"]),
                "energy": cost,
                "player_energy": int(p["energy"]),
            }

    def start_battle(
        self,
        player_id: int,
        dungeon_id: str,
        enemy_index: int | None = None,
        energy_cost: int | None = None,
    ) -> dict:
        if dungeon_id not in DUNGEONS:
            raise GameError("Choose /battle gate, /battle rift, or /battle abyss.")
        now = int(self.epoch())
        dungeon = DUNGEONS[dungeon_id]
        with self.db.transaction() as con:
            p = self._require_player(con, player_id)
            minimum_level = {"gate": 1, "rift": 3, "abyss": 8}[dungeon_id]
            if int(p["level"]) < minimum_level:
                raise GameError(f"You need hunter level {minimum_level} to enter this dungeon.")
            con.execute(
                "UPDATE active_battles SET status='expired' WHERE player_id=? AND status='active' AND expires_at<=?",
                (player_id, now),
            )
            active = con.execute(
                "SELECT id FROM active_battles WHERE player_id=? AND status='active'", (player_id,)
            ).fetchone()
            if active:
                raise GameError("You already have an active battle. Use /battle_status or finish it first.")
            cost = int(energy_cost if energy_cost is not None else dungeon["energy"])
            if int(p["energy"]) < cost:
                raise GameError(
                    f"You need {cost} energy for this hunt. Claim /daily or use an energy item."
                )
            con.execute("UPDATE players SET energy=energy-? WHERE telegram_id=?", (cost, player_id))
            self._increment_quest(con, player_id, "spend_energy", cost)
            hp, _, mana, _ = self._stats_with_companion(con, p)
            enemies = ENEMIES[dungeon_id]
            if enemy_index is not None and not 0 <= int(enemy_index) < len(enemies):
                raise GameError("That enemy is no longer available. Try /hunt again.")
            enemy_def = enemies[int(enemy_index)] if enemy_index is not None else self.rng.choice(enemies)
            enemy_hp, enemy_attack = self._enemy_stats(p, dungeon_id, enemy_def)
            battle_id = uuid.uuid4().hex[:12]
            con.execute(
                """INSERT INTO active_battles
                   (id,player_id,dungeon_id,enemy_name,enemy_attack,enemy_hp,enemy_max_hp,
                    player_hp,player_max_hp,mana,max_mana,started_at,expires_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    battle_id,
                    player_id,
                    dungeon_id,
                    enemy_def["name"],
                    enemy_attack,
                    enemy_hp,
                    enemy_hp,
                    hp,
                    hp,
                    mana,
                    mana,
                    now,
                    now + BATTLE_TIMEOUT_SECONDS,
                ),
            )
            return self._with_player(
                self._battle_dict(
                    con.execute("SELECT * FROM active_battles WHERE id=?", (battle_id,)).fetchone()
                ),
                p,
            )

    @staticmethod
    def _battle_dict(row) -> dict:
        result = dict(row)
        result["result"] = json.loads(result.pop("result_json") or "{}")
        return result

    @staticmethod
    def _with_player(battle: dict, p) -> dict:
        battle["player_name"] = str(p["display_name"])
        battle["player_level"] = int(p["level"])
        return battle

    def battle_status(self, player_id: int) -> dict | None:
        now = int(self.epoch())
        with self.db.transaction() as con:
            self._require_player(con, player_id)
            row = con.execute(
                "SELECT * FROM active_battles WHERE player_id=? AND status='active'", (player_id,)
            ).fetchone()
            if row is None:
                return None
            if int(row["expires_at"]) <= now:
                con.execute("UPDATE active_battles SET status='expired' WHERE id=?", (row["id"],))
                return None
            return self._with_player(self._battle_dict(row), self._require_player(con, player_id))

    def battle_action(self, player_id: int, battle_id: str, expected_turn: int, action: str) -> dict:
        if action not in {"attack", "guard", "dodge", "skill", "potion", "mana", "flee"} and not action.startswith(
            "skill:"
        ):
            raise GameError("Unknown battle action.")
        now = int(self.epoch())
        with self.db.transaction() as con:
            p = self._require_player(con, player_id)
            battle = con.execute("SELECT * FROM active_battles WHERE id=?", (battle_id,)).fetchone()
            if battle is None or int(battle["player_id"]) != player_id:
                raise GameError("That battle does not belong to you or no longer exists.")
            if battle["status"] != "active":
                raise GameError("That battle has already ended.")
            if int(battle["expires_at"]) <= now:
                con.execute(
                    "UPDATE active_battles SET status='expired',result_json=? WHERE id=?",
                    (json.dumps({"log": ["Battle expired."], "reward": {}, "state": {}}), battle_id),
                )
                return self._with_player(
                    self._battle_dict(
                        con.execute("SELECT * FROM active_battles WHERE id=?", (battle_id,)).fetchone()
                    ),
                    p,
                )
            if int(battle["turn"]) != expected_turn:
                raise GameError("That battle screen is out of date. Use /battle_status to refresh it.")
            battle_data = json.loads(battle["result_json"] or "{}")
            state = battle_data.get("state") or {}
            player_eff = {"burn": 0, "poison": 0, "shield": 0.0, "stun": 0, **(state.get("player") or {})}
            enemy_eff = {"burn": 0, "poison": 0, "stun": 0, **(state.get("enemy") or {})}
            skill_cd = max(0, int(state.get("skill_cd") or 0))
            player_hp, enemy_hp = int(battle["player_hp"]), int(battle["enemy_hp"])
            mana, max_mana = int(battle["mana"]), int(battle["max_mana"])
            pmax, emax = int(battle["player_max_hp"]), int(battle["enemy_max_hp"])
            turn_no = int(battle["turn"])
            class_id = str(p["class_id"])
            _, attack, _, _ = self._stats_with_companion(con, p)
            logs: list[str] = []
            status = "active"
            reward: dict[str, int] = {}
            guarded = False
            evasive = False

            for effect, label in (("burn", "burn"), ("poison", "poison")):
                stacks = int(player_eff.get(effect) or 0)
                if stacks > 0:
                    damage = 1 if effect == "poison" else 2
                    damage = max(damage, int(pmax * (0.04 if effect == "poison" else 0.05)))
                    player_hp = max(0, player_hp - damage)
                    player_eff[effect] = stacks - 1
                    logs.append(f"You suffer {damage} {label} damage.")
            if player_hp <= 0:
                status = "defeat"
                logs.append("Your wounds overwhelm you. You were defeated.")
            elif action == "flee":
                status = "escaped"
                logs.append("You retreated. No dungeon rewards were earned.")
            else:
                if action == "attack":
                    crit_chance = float(CLASS_CRIT.get(class_id, 0.08))
                    crit = self.rng.random() < crit_chance
                    damage = max(1, int(attack * self.rng.uniform(0.85, 1.15)))
                    if crit:
                        damage = int(damage * 1.6)
                    enemy_hp = max(0, enemy_hp - damage)
                    logs.append(f"You strike for {damage} damage.{' CRITICAL HIT!' if crit else ''}")
                elif action == "guard":
                    guarded = True
                    player_eff["shield"] = max(float(player_eff.get("shield") or 0), 0.5)
                    logs.append("You brace for impact. Incoming damage is reduced.")
                elif action == "dodge":
                    evasive = True
                    logs.append("You prepare to evade the next attack.")
                elif action.startswith("skill:"):
                    skill_id = action.split(":", 1)[1]
                    skill = SKILLS.get(skill_id)
                    if skill is None:
                        raise GameError("That skill does not exist.")
                    owned = con.execute(
                        "SELECT 1 FROM player_skills WHERE player_id=? AND skill_id=?",
                        (player_id, skill_id),
                    ).fetchone()
                    if owned is None and skill_id != DEFAULT_SKILL:
                        raise GameError("You do not own that skill. Visit /skills.")
                    cost = int(skill["mana"])
                    if mana < cost:
                        raise GameError(f"You need {cost} mana to use {skill['name']}.")
                    damage = max(
                        1, int(attack * float(skill["multiplier"]) * self.rng.uniform(0.9, 1.1))
                    )
                    enemy_hp = max(0, enemy_hp - damage)
                    mana -= cost
                    logs.append(f"{skill['name']} hits for {damage} damage.")
                    effect = str(skill["effect"])
                    if effect == "burn":
                        enemy_eff["burn"] = max(int(enemy_eff.get("burn") or 0), 3)
                        logs.append("The enemy is set ablaze for 3 turns.")
                    elif effect == "poison":
                        enemy_eff["poison"] = max(int(enemy_eff.get("poison") or 0), 3)
                        logs.append("Venom courses through the enemy for 3 turns.")
                    elif effect == "stun":
                        enemy_eff["stun"] = max(int(enemy_eff.get("stun") or 0), 1)
                        logs.append("The enemy is stunned.")
                    elif effect == "lifesteal":
                        healed = min(damage // 2, pmax - player_hp)
                        if healed > 0:
                            player_hp += healed
                            logs.append(f"You drain {healed} HP.")
                    self._increment_quest(con, player_id, "use_skill", 1)
                elif action == "skill":
                    if skill_cd > 0:
                        raise GameError(f"Your awakening skill recharges in {skill_cd} turn(s).")
                    if mana < SKILL_MANA_COST:
                        raise GameError(f"You need {SKILL_MANA_COST} mana to use your awakening skill.")
                    class_data = CLASSES[class_id]
                    crit = self.rng.random() < float(CLASS_CRIT.get(class_id, 0.08))
                    damage = max(
                        1, int(attack * float(class_data["multiplier"]) * self.rng.uniform(0.9, 1.1))
                    )
                    if crit:
                        damage = int(damage * 1.6)
                    enemy_hp = max(0, enemy_hp - damage)
                    mana -= SKILL_MANA_COST
                    skill_cd = SKILL_COOLDOWN_TURNS + 1
                    logs.append(f"{class_data['skill']} hits for {damage} damage.{' CRITICAL HIT!' if crit else ''}")
                    if class_id == "vanguard":
                        enemy_eff["stun"] = max(int(enemy_eff.get("stun") or 0), 1)
                        player_eff["shield"] = max(float(player_eff.get("shield") or 0), 0.5)
                        logs.append("Aegis Break stuns the enemy and raises your guard.")
                    elif class_id == "assassin":
                        enemy_eff["poison"] = max(int(enemy_eff.get("poison") or 0), 3)
                        logs.append("Shadowstep poisons the enemy for 3 turns.")
                    else:
                        enemy_eff["burn"] = max(int(enemy_eff.get("burn") or 0), 3)
                        logs.append("Rift Lance burns the enemy for 3 turns.")
                    self._increment_quest(con, player_id, "use_skill", 1)
                elif action == "potion":
                    item = con.execute(
                        "SELECT quantity FROM inventory WHERE player_id=? AND item_id='healing_tonic'",
                        (player_id,),
                    ).fetchone()
                    if item is None or int(item["quantity"]) < 1:
                        raise GameError("You have no Healing Tonic. Buy one with /shop.")
                    healed = min(45, pmax - player_hp)
                    if healed <= 0:
                        raise GameError("Your HP is already full.")
                    con.execute(
                        "UPDATE inventory SET quantity=quantity-1 WHERE player_id=? AND item_id='healing_tonic'",
                        (player_id,),
                    )
                    player_hp += healed
                    logs.append(f"Healing Tonic restores {healed} HP.")
                elif action == "mana":
                    item = con.execute(
                        "SELECT quantity FROM inventory WHERE player_id=? AND item_id='mana_vial'",
                        (player_id,),
                    ).fetchone()
                    if item is None or int(item["quantity"]) < 1:
                        raise GameError("You have no Mana Vial. Buy one with /shop.")
                    restored = min(20, max_mana - mana)
                    if restored <= 0:
                        raise GameError("Your mana is already full.")
                    con.execute(
                        "UPDATE inventory SET quantity=quantity-1 WHERE player_id=? AND item_id='mana_vial'",
                        (player_id,),
                    )
                    mana += restored
                    logs.append(f"Mana Vial restores {restored} mana.")

                for effect, label in (("burn", "burn"), ("poison", "poison")):
                    stacks = int(enemy_eff.get(effect) or 0)
                    if enemy_hp > 0 and stacks > 0:
                        damage = max(2, int(emax * (0.04 if effect == "poison" else 0.05)))
                        enemy_hp = max(0, enemy_hp - damage)
                        enemy_eff[effect] = stacks - 1
                        logs.append(f"{battle['enemy_name']} suffers {damage} {label} damage.")

                enemy_stunned = int(enemy_eff.get("stun") or 0) > 0
                if enemy_hp > 0 and enemy_stunned:
                    enemy_eff["stun"] = int(enemy_eff.get("stun") or 0) - 1
                    logs.append(f"{battle['enemy_name']} is stunned and cannot act.")
                elif enemy_hp > 0:
                    special = turn_no % ENEMY_SPECIAL_EVERY == 0
                    hit = max(1, int(int(battle["enemy_attack"]) * self.rng.uniform(0.8, 1.2)))
                    if special:
                        hit = int(hit * 1.4)
                    if evasive and self.rng.random() < min(0.85, 0.65 + float(CLASS_DODGE.get(class_id, 0.05))):
                        logs.append(f"You dodge {battle['enemy_name']}'s attack completely.")
                        hit = 0
                    elif guarded or float(player_eff.get("shield") or 0) > 0:
                        reduction = 0.66 if guarded else float(player_eff.get("shield") or 0)
                        hit = max(1, int(hit * (1 - reduction)))
                        player_eff["shield"] = 0.0
                        logs.append(f"{battle['enemy_name']} hits you for {hit} damage through your guard.")
                    else:
                        logs.append(f"{battle['enemy_name']} hits you for {hit} damage.")
                    player_hp = max(0, player_hp - hit)
                    if special and player_hp > 0:
                        effect = "poison" if battle["dungeon_id"] == "gate" else "burn"
                        stacks = 2 if battle["dungeon_id"] != "abyss" else 3
                        player_eff[effect] = max(int(player_eff.get(effect) or 0), stacks)
                        logs.append(f"{battle['enemy_name']} uses a rift technique: you are {effect}ed for {stacks} turns.")

                mana = min(max_mana, mana + 3)
                if mana > int(battle["mana"]):
                    logs.append("You recover 3 mana.")

                if enemy_hp <= 0:
                    status = "victory"
                    d = DUNGEONS[battle["dungeon_id"]]
                    coins = int(d["coins"]) + int(p["level"]) * 4
                    xp = int(d["xp"]) + int(p["level"]) * 8
                    essence = int(d["essence"])
                    if player_hp >= pmax:
                        coins = int(coins * 1.25)
                        logs.append("Flawless victory bonus: +25% coins.")
                    self._add_coins(con, player_id, coins)
                    self._add_essence(con, player_id, essence)
                    levels = self._award_xp(con, player_id, xp)
                    if battle["dungeon_id"] == "gate":
                        self._increment_quest(con, player_id, "clear_gate", 1)
                    self._tournament_score(con, player_id, int(d["tier"]) * 100)
                    self._guild_xp(con, player_id, max(1, int(d["tier"]) * 8))
                    reward = {"coins": coins, "xp": xp, "essence": essence, "levels": levels}
                    logs.append(f"Victory! Rewards: {coins} coins, {xp} XP, {essence} essence.")
                elif player_hp <= 0:
                    status = "defeat"
                    logs.append("You were defeated. Catch your breath and try again.")

            next_turn = turn_no + 1
            state.update(
                {
                    "player": player_eff,
                    "enemy": enemy_eff,
                    "skill_cd": max(0, skill_cd - 1),
                }
            )
            history = (battle_data.get("log") or [])[-20:]
            combined = (history + logs)[-30:]
            con.execute(
                """UPDATE active_battles SET enemy_hp=?,player_hp=?,mana=?,turn=?,status=?,result_json=?
                   WHERE id=? AND status='active'""",
                (
                    enemy_hp,
                    player_hp,
                    mana,
                    next_turn,
                    status,
                    json.dumps({"log": combined, "reward": reward, "state": state}),
                    battle_id,
                ),
            )
            return self._with_player(
                self._battle_dict(
                    con.execute("SELECT * FROM active_battles WHERE id=?", (battle_id,)).fetchone()
                ),
                p,
            )

    def _increment_quest(self, con, player_id: int, quest_id: str, amount: int) -> None:
        today = self._today()
        con.execute(
            "INSERT OR IGNORE INTO daily_quests(player_id,quest_date,quest_id) VALUES(?,?,?)",
            (player_id, today, quest_id),
        )
        con.execute(
            "UPDATE daily_quests SET progress=progress+? WHERE player_id=? AND quest_date=? AND quest_id=?",
            (amount, player_id, today, quest_id),
        )

    def buy_item(self, player_id: int, item_id: str, quantity: int = 1) -> dict:
        if item_id not in ITEMS:
            raise GameError("Unknown shop item. Check /shop.")
        if not 1 <= quantity <= 99:
            raise GameError("Choose a quantity from 1 to 99.")
        item = ITEMS[item_id]
        total = int(item["price"]) * quantity
        with self.db.transaction() as con:
            p = self._require_player(con, player_id)
            if int(p["coins"]) < total:
                raise GameError(f"You need {total} coins for that purchase.")
            con.execute("UPDATE players SET coins=coins-? WHERE telegram_id=?", (total, player_id))
            con.execute(
                "INSERT INTO inventory(player_id,item_id,quantity) VALUES(?,?,?) ON CONFLICT(player_id,item_id) DO UPDATE SET quantity=quantity+excluded.quantity",
                (player_id, item_id, quantity),
            )
            return {"item": item["name"], "quantity": quantity, "cost": total}

    def use_energy_item(self, player_id: int, item_id: str) -> dict:
        if item_id != "energy_shard":
            raise GameError(
                "Only Energy Shards can be used outside battle. Use Healing Tonics during a battle."
            )
        with self.db.transaction() as con:
            p = self._require_player(con, player_id)
            row = con.execute(
                "SELECT quantity FROM inventory WHERE player_id=? AND item_id=?", (player_id, item_id)
            ).fetchone()
            if row is None or int(row["quantity"]) <= 0:
                raise GameError("You do not have an Energy Shard.")
            gained = min(int(ITEMS[item_id]["amount"]), int(p["max_energy"]) - int(p["energy"]))
            if gained <= 0:
                raise GameError("Your energy is already full.")
            con.execute(
                "UPDATE inventory SET quantity=quantity-1 WHERE player_id=? AND item_id=?",
                (player_id, item_id),
            )
            con.execute("UPDATE players SET energy=energy+? WHERE telegram_id=?", (gained, player_id))
            return {"energy": int(p["energy"]) + gained, "gained": gained}

    def inventory(self, player_id: int) -> list[dict]:
        with self.db.reader() as con:
            self._require_player(con, player_id)
            rows = con.execute(
                "SELECT item_id,quantity FROM inventory WHERE player_id=? AND quantity>0 ORDER BY item_id",
                (player_id,),
            ).fetchall()
            return [
                {**dict(row), "name": ITEMS[row["item_id"]]["name"]}
                for row in rows
                if row["item_id"] in ITEMS
            ]

    def shop(self) -> list[dict]:
        return [{"id": key, **value} for key, value in ITEMS.items()]

    def leaderboard(self) -> list[dict]:
        return self.leaderboards("level", limit=10)

    def leaderboards(self, category: str = "level", limit: int = 10) -> list[dict]:
        queries = {
            "level": (
                "SELECT display_name AS name, level AS value, xp AS detail FROM players "
                "ORDER BY level DESC, xp DESC, coins DESC LIMIT ?",
                "Lv {value} · {detail} XP",
            ),
            "xp": (
                "SELECT display_name AS name, xp AS value, level AS detail FROM players "
                "ORDER BY xp DESC, level DESC LIMIT ?",
                "{value} XP · Lv {detail}",
            ),
            "coins": (
                "SELECT display_name AS name, coins AS value, level AS detail FROM players "
                "ORDER BY coins DESC, level DESC LIMIT ?",
                "{value:,} coins · Lv {detail}",
            ),
            "arena": (
                "SELECT p.display_name AS name, a.rating AS value, a.wins AS detail "
                "FROM arena_ratings a JOIN players p ON p.telegram_id=a.player_id "
                "ORDER BY a.rating DESC, a.wins DESC LIMIT ?",
                "{value} rating · {detail} wins",
            ),
            "wins": (
                "SELECT p.display_name AS name, a.wins AS value, a.rating AS detail "
                "FROM arena_ratings a JOIN players p ON p.telegram_id=a.player_id "
                "ORDER BY a.wins DESC, a.rating DESC LIMIT ?",
                "{value} wins · {detail} rating",
            ),
            "guild": (
                "SELECT name AS name, level AS value, xp AS detail FROM guilds "
                "ORDER BY level DESC, xp DESC LIMIT ?",
                "Lv {value} · {detail} guild XP",
            ),
        }
        if category not in queries:
            category = "level"
        query, _ = queries[category]
        with self.db.reader() as con:
            return [dict(row) for row in con.execute(query, (limit,)).fetchall()]

    def create_guild(self, player_id: int, name: str) -> dict:
        name = " ".join(name.split())
        if not 3 <= len(name) <= 24 or not re.fullmatch(r"[\w -]+", name, flags=re.UNICODE):
            raise GameError("Guild names must be 3–24 letters, numbers, spaces, '_' or '-'.")
        with self.db.transaction() as con:
            self._require_player(con, player_id)
            if con.execute("SELECT 1 FROM guild_members WHERE player_id=?", (player_id,)).fetchone():
                raise GameError("Leave your current guild before creating another.")
            try:
                cur = con.execute("INSERT INTO guilds(name,leader_id) VALUES(?,?)", (name, player_id))
            except Exception as exc:
                if "UNIQUE" in str(exc).upper():
                    raise GameError("That guild name is already taken.") from None
                raise
            guild_id = int(cur.lastrowid)
            con.execute("INSERT INTO guild_members(player_id,guild_id) VALUES(?,?)", (player_id, guild_id))
            con.execute(
                "INSERT INTO guild_roles(guild_id,player_id,role) VALUES(?,?,?)",
                (guild_id, player_id, "leader"),
            )
            return {"id": guild_id, "name": name, "level": 1}

    def join_guild(self, player_id: int, guild_id: int) -> dict:
        with self.db.transaction() as con:
            self._require_player(con, player_id)
            if con.execute("SELECT 1 FROM guild_members WHERE player_id=?", (player_id,)).fetchone():
                raise GameError("You are already in a guild.")
            guild = con.execute("SELECT id,name,level FROM guilds WHERE id=?", (guild_id,)).fetchone()
            if guild is None:
                raise GameError("Guild not found. Use /guild to browse guilds.")
            count = con.execute(
                "SELECT COUNT(*) FROM guild_members WHERE guild_id=?", (guild_id,)
            ).fetchone()[0]
            if count >= GUILD_MAX_MEMBERS:
                raise GameError("That guild is full.")
            con.execute("INSERT INTO guild_members(player_id,guild_id) VALUES(?,?)", (player_id, guild_id))
            con.execute(
                "INSERT INTO guild_roles(guild_id,player_id,role) VALUES(?,?,?)",
                (guild_id, player_id, "member"),
            )
            return dict(guild)

    def guild_info(self, player_id: int) -> dict:
        with self.db.reader() as con:
            self._require_player(con, player_id)
            membership = con.execute(
                "SELECT g.id,g.name,g.level,g.xp,g.leader_id,g.coins FROM guild_members m JOIN guilds g ON g.id=m.guild_id WHERE m.player_id=?",
                (player_id,),
            ).fetchone()
            if membership is None:
                rows = con.execute(
                    "SELECT g.id,g.name,g.level,COUNT(m.player_id) AS members FROM guilds g LEFT JOIN guild_members m ON m.guild_id=g.id GROUP BY g.id ORDER BY g.level DESC,members DESC LIMIT 10"
                ).fetchall()
                return {"guild": None, "guilds": [dict(row) for row in rows]}
            members = con.execute(
                "SELECT p.display_name,m.player_id,COALESCE(r.role,'member') AS role FROM guild_members m JOIN players p ON p.telegram_id=m.player_id LEFT JOIN guild_roles r ON r.guild_id=m.guild_id AND r.player_id=m.player_id WHERE m.guild_id=? ORDER BY (m.player_id=?) DESC,p.display_name COLLATE NOCASE LIMIT 30",
                (membership["id"], membership["leader_id"]),
            ).fetchall()
            return {"guild": dict(membership), "members": [dict(row) for row in members]}

    def leave_guild(self, player_id: int) -> str:
        with self.db.transaction() as con:
            self._require_player(con, player_id)
            membership = con.execute(
                "SELECT guild_id FROM guild_members WHERE player_id=?", (player_id,)
            ).fetchone()
            if membership is None:
                raise GameError("You are not in a guild.")
            guild = con.execute(
                "SELECT name,leader_id FROM guilds WHERE id=?", (membership["guild_id"],)
            ).fetchone()
            if int(guild["leader_id"]) == player_id:
                raise GameError("Guild leaders must use /guild_disband.")
            con.execute("DELETE FROM guild_members WHERE player_id=?", (player_id,))
            con.execute(
                "DELETE FROM guild_roles WHERE guild_id=? AND player_id=?",
                (membership["guild_id"], player_id),
            )
            return str(guild["name"])

    def disband_guild(self, player_id: int) -> str:
        with self.db.transaction() as con:
            self._require_player(con, player_id)
            guild = con.execute("SELECT id,name FROM guilds WHERE leader_id=?", (player_id,)).fetchone()
            if guild is None:
                raise GameError("Only a guild leader can disband a guild.")
            con.execute("DELETE FROM guilds WHERE id=?", (guild["id"],))
            return str(guild["name"])

    def _guild_role(self, con, player_id: int) -> tuple[int, str]:
        row = con.execute("SELECT guild_id,role FROM guild_roles WHERE player_id=?", (player_id,)).fetchone()
        if row is None:
            raise GameError("You are not in a guild.")
        return int(row["guild_id"]), str(row["role"])

    def guild_promote(self, actor_id: int, target_id: int) -> str:
        with self.db.transaction() as con:
            self._require_player(con, actor_id)
            self._require_player(con, target_id)
            guild_id, role = self._guild_role(con, actor_id)
            if role != "leader":
                raise GameError("Only the guild leader can promote officers.")
            target = con.execute(
                "SELECT role FROM guild_roles WHERE guild_id=? AND player_id=?", (guild_id, target_id)
            ).fetchone()
            if target is None:
                raise GameError("That hunter is not in your guild.")
            con.execute(
                "UPDATE guild_roles SET role='officer' WHERE guild_id=? AND player_id=?",
                (guild_id, target_id),
            )
            return "officer"

    def guild_demote(self, actor_id: int, target_id: int) -> str:
        with self.db.transaction() as con:
            self._require_player(con, actor_id)
            self._require_player(con, target_id)
            guild_id, role = self._guild_role(con, actor_id)
            if role != "leader":
                raise GameError("Only the guild leader can demote officers.")
            target = con.execute(
                "SELECT role FROM guild_roles WHERE guild_id=? AND player_id=?", (guild_id, target_id)
            ).fetchone()
            if target is None:
                raise GameError("That hunter is not in your guild.")
            con.execute(
                "UPDATE guild_roles SET role='member' WHERE guild_id=? AND player_id=? AND role='officer'",
                (guild_id, target_id),
            )
            return "member"

    def guild_kick(self, actor_id: int, target_id: int) -> str:
        with self.db.transaction() as con:
            self._require_player(con, actor_id)
            self._require_player(con, target_id)
            guild_id, role = self._guild_role(con, actor_id)
            if role not in {"leader", "officer"}:
                raise GameError("Only guild leaders and officers can remove members.")
            target = con.execute(
                "SELECT role FROM guild_roles WHERE guild_id=? AND player_id=?", (guild_id, target_id)
            ).fetchone()
            if target is None:
                raise GameError("That hunter is not in your guild.")
            if target["role"] in {"leader", "officer"} and role != "leader":
                raise GameError("Officers cannot remove guild officers.")
            con.execute("DELETE FROM guild_members WHERE guild_id=? AND player_id=?", (guild_id, target_id))
            con.execute("DELETE FROM guild_roles WHERE guild_id=? AND player_id=?", (guild_id, target_id))
            return str(target_id)

    def guild_rename(self, actor_id: int, name: str) -> str:
        name = " ".join(name.split())
        if not 3 <= len(name) <= 24 or not re.fullmatch(r"[\w -]+", name, flags=re.UNICODE):
            raise GameError("Guild names must be 3–24 letters, numbers, spaces, '_' or '-'.")
        with self.db.transaction() as con:
            self._require_player(con, actor_id)
            guild_id, role = self._guild_role(con, actor_id)
            if role != "leader":
                raise GameError("Only the guild leader can rename the guild.")
            try:
                con.execute("UPDATE guilds SET name=? WHERE id=?", (name, guild_id))
            except Exception as exc:
                if "UNIQUE" in str(exc).upper():
                    raise GameError("That guild name is already taken.") from None
                raise
            return name

    def donate_guild(self, player_id: int, amount: int) -> dict:
        if not GUILD_DONATION_MIN <= amount <= GUILD_DONATION_MAX or amount % 10:
            raise GameError(
                f"Donation must be {GUILD_DONATION_MIN}–{GUILD_DONATION_MAX} coins in multiples of 10."
            )
        with self.db.transaction() as con:
            p = self._require_player(con, player_id)
            if int(p["coins"]) < amount:
                raise GameError("You do not have enough coins.")
            membership = con.execute(
                "SELECT g.id,g.name FROM guild_members m JOIN guilds g ON g.id=m.guild_id WHERE m.player_id=?",
                (player_id,),
            ).fetchone()
            if membership is None:
                raise GameError("Join a guild before donating.")
            con.execute("UPDATE players SET coins=coins-? WHERE telegram_id=?", (amount, player_id))
            con.execute("UPDATE guilds SET coins=coins+? WHERE id=?", (amount, membership["id"]))
            gained = self._guild_xp(con, player_id, amount // 10)
            guild = con.execute("SELECT level,xp FROM guilds WHERE id=?", (membership["id"],)).fetchone()
            return {
                "name": membership["name"],
                "donation": amount,
                "xp_gained": amount // 10,
                "level": guild["level"],
                "xp": guild["xp"],
                "levels": gained,
            }

    def raid_status(self) -> dict:
        with self.db.reader() as con:
            boss = con.execute("SELECT * FROM raid_state WHERE id=1").fetchone()
            top = con.execute(
                "SELECT p.display_name,c.total_damage,c.hits FROM raid_contributions c JOIN players p ON p.telegram_id=c.player_id ORDER BY c.total_damage DESC LIMIT 5"
            ).fetchall()
            meta = WORLD_BOSSES[(int(boss["cycle"]) - 1) % len(WORLD_BOSSES)]
            return {"boss": {**dict(boss), "art": meta["art"], "description": meta["description"]}, "top": [dict(row) for row in top]}

    def raid_attack(self, player_id: int) -> dict:
        now = int(self.epoch())
        with self.db.transaction() as con:
            p = self._require_player(con, player_id)
            remain = RAID_COOLDOWN_SECONDS - (now - int(p["last_raid_at"]))
            if remain > 0:
                raise GameError(f"Your raid strike recovers in {remain // 60 + 1} minute(s).")
            if int(p["energy"]) < RAID_ENERGY_COST:
                raise GameError(f"You need {RAID_ENERGY_COST} energy to attack the raid boss.")
            boss = con.execute("SELECT * FROM raid_state WHERE id=1").fetchone()
            if int(boss["hp"]) <= 0:
                con.execute("UPDATE raid_state SET hp=max_hp,cycle=cycle+1 WHERE id=1")
                boss = con.execute("SELECT * FROM raid_state WHERE id=1").fetchone()
            _, attack, _, _ = self._stats_with_companion(con, p)
            raw = attack * self.rng.randint(3, 6)
            effective = min(raw, int(boss["hp"]))
            remaining = int(boss["hp"]) - effective
            defeated = remaining <= 0
            con.execute(
                "UPDATE players SET energy=energy-?,last_raid_at=? WHERE telegram_id=?",
                (RAID_ENERGY_COST, now, player_id),
            )
            con.execute(
                "INSERT INTO raid_contributions(player_id,total_damage,hits) VALUES(?,?,1) ON CONFLICT(player_id) DO UPDATE SET total_damage=total_damage+excluded.total_damage,hits=hits+1",
                (player_id, effective),
            )
            self._tournament_score(con, player_id, effective)
            guild_levels = self._guild_xp(con, player_id, max(1, effective // 100))
            reward = 15 + effective // 50 + (250 if defeated else 0)
            self._add_coins(con, player_id, reward)
            if defeated:
                next_meta = WORLD_BOSSES[(int(boss["cycle"])) % len(WORLD_BOSSES)]
                next_hp = int(next_meta["base_hp"])
                con.execute(
                    "UPDATE raid_state SET name=?,hp=?,max_hp=?,cycle=cycle+1,updated_at=CURRENT_TIMESTAMP WHERE id=1",
                    (next_meta["name"], next_hp, next_hp),
                )
                con.execute("DELETE FROM raid_contributions")
            else:
                con.execute(
                    "UPDATE raid_state SET hp=?,updated_at=CURRENT_TIMESTAMP WHERE id=1", (remaining,)
                )
            return {
                "boss": boss["name"],
                "damage": effective,
                "raw_damage": raw,
                "hp": remaining,
                "max_hp": boss["max_hp"],
                "reward": reward,
                "defeated": defeated,
                "guild_levels": guild_levels,
                "art": WORLD_BOSSES[(int(boss["cycle"]) - 1) % len(WORLD_BOSSES)]["art"],
            }

    def _tournament_score(self, con, player_id: int, score: int) -> None:
        con.execute(
            "UPDATE tournament_entries SET score=score+? WHERE tournament_week=? AND player_id=?",
            (max(0, score), self._current_week(), player_id),
        )

    def _tournament_week(self) -> str:
        iso = self.clock().astimezone(UTC).date().isocalendar()
        return f"{iso.year}-W{iso.week:02d}"

    def skill_shop(self) -> list[dict]:
        return [{"id": key, **value} for key, value in SKILLS.items()]

    def skills(self, player_id: int) -> list[dict]:
        with self.db.reader() as con:
            self._require_player(con, player_id)
            owned = {
                row["skill_id"]
                for row in con.execute(
                    "SELECT skill_id FROM player_skills WHERE player_id=?", (player_id,)
                ).fetchall()
            }
            owned.add(DEFAULT_SKILL)
            return [
                {"id": key, **value, "owned": key in owned} for key, value in SKILLS.items()
            ]

    def learn_skill(self, player_id: int, skill_id: str) -> dict:
        skill = SKILLS.get(skill_id)
        if skill is None:
            raise GameError("Unknown skill. Check /skills.")
        price = int(skill["price"])
        with self.db.transaction() as con:
            p = self._require_player(con, player_id)
            owned = con.execute(
                "SELECT 1 FROM player_skills WHERE player_id=? AND skill_id=?", (player_id, skill_id)
            ).fetchone()
            if owned is not None or skill_id == DEFAULT_SKILL:
                raise GameError("You already own that skill.")
            if int(p["coins"]) < price:
                raise GameError(f"You need {price:,} coins to learn {skill['name']}.")
            con.execute("UPDATE players SET coins=coins-? WHERE telegram_id=?", (price, player_id))
            con.execute(
                "INSERT INTO player_skills(player_id,skill_id) VALUES(?,?)", (player_id, skill_id)
            )
            return {"name": skill["name"], "price": price, "rank": skill["rank"]}

    def _roll_rank(self, minimum: str | None = None) -> str:
        ranks = [rank for rank in GACHA_RANKS if minimum is None or GACHA_RANKS.index(rank) >= GACHA_RANKS.index(minimum)]
        weights = [GACHA_WEIGHTS[rank] for rank in ranks]
        return self.rng.choices(ranks, weights=weights, k=1)[0]

    def gacha_pull(self, player_id: int, count: int = 1) -> dict:
        if count not in (1, 10):
            raise GameError("You can summon 1 or 10 characters at a time.")
        cost = GACHA_COST if count == 1 else GACHA_TEN_COST
        with self.db.transaction() as con:
            p = self._require_player(con, player_id)
            if int(p["essence"]) < cost:
                raise GameError(f"You need {cost} essence for this summon.")
            con.execute("UPDATE players SET essence=essence-? WHERE telegram_id=?", (cost, player_id))
            pity = int(p["gacha_pity"])
            results = []
            for _ in range(count):
                pity += 1
                if pity >= GACHA_PITY:
                    rank = self._roll_rank("S")
                    pity = 0
                else:
                    rank = self._roll_rank()
                pool = [cid for cid, value in GACHA_CHARACTERS.items() if value["rank"] == rank]
                character_id = self.rng.choice(pool)
                character = GACHA_CHARACTERS[character_id]
                con.execute(
                    """INSERT INTO player_characters(player_id,character_id,copies) VALUES(?,?,1)
                       ON CONFLICT(player_id,character_id) DO UPDATE SET copies=copies+1""",
                    (player_id, character_id),
                )
                copies = con.execute(
                    "SELECT copies FROM player_characters WHERE player_id=? AND character_id=?",
                    (player_id, character_id),
                ).fetchone()["copies"]
                results.append(
                    {
                        "id": character_id,
                        "name": character["name"],
                        "rank": character["rank"],
                        "title": character["title"],
                        "new": int(copies) == 1,
                    }
                )
                if rank in {"SR", "SP"}:
                    pity = 0
            con.execute("UPDATE players SET gacha_pity=? WHERE telegram_id=?", (pity, player_id))
            bonus_hp, bonus_atk = self._character_bonus(con, player_id)
            return {
                "results": results,
                "cost": cost,
                "pity": pity,
                "essence": int(p["essence"]) - cost,
                "bonus_hp": bonus_hp,
                "bonus_atk": bonus_atk,
            }

    def gacha_collection(self, player_id: int) -> dict:
        with self.db.reader() as con:
            p = self._require_player(con, player_id)
            rows = con.execute(
                "SELECT character_id,copies FROM player_characters WHERE player_id=?", (player_id,)
            ).fetchall()
            owned = {row["character_id"]: int(row["copies"]) for row in rows}
            by_rank = {rank: [] for rank in GACHA_RANKS}
            for character_id, character in GACHA_CHARACTERS.items():
                if character_id in owned:
                    by_rank[character["rank"]].append(
                        {
                            "id": character_id,
                            "name": character["name"],
                            "title": character["title"],
                            "copies": owned[character_id],
                        }
                    )
            bonus_hp, bonus_atk = self._character_bonus(con, player_id)
            return {
                "by_rank": by_rank,
                "owned": len(owned),
                "total": len(GACHA_CHARACTERS),
                "bonus_hp": bonus_hp,
                "bonus_atk": bonus_atk,
                "pity": int(p["gacha_pity"]),
                "essence": int(p["essence"]),
            }

    def bank_status(self, player_id: int) -> dict:
        with self.db.reader() as con:
            p = self._require_player(con, player_id)
            return {
                "coins": int(p["coins"]),
                "bank": int(p["bank_coins"]),
                "total": int(p["coins"]) + int(p["bank_coins"]),
            }

    def bank_deposit(self, player_id: int, amount: int) -> dict:
        if not BANK_MIN_TRANSFER <= amount <= BANK_MAX_TRANSFER:
            raise GameError(
                f"Deposit between {BANK_MIN_TRANSFER:,} and {BANK_MAX_TRANSFER:,} coins."
            )
        with self.db.transaction() as con:
            p = self._require_player(con, player_id)
            if int(p["coins"]) < amount:
                raise GameError("You do not have enough coins to deposit.")
            con.execute(
                "UPDATE players SET coins=coins-?,bank_coins=bank_coins+? WHERE telegram_id=?",
                (amount, amount, player_id),
            )
            return {"amount": amount, "coins": int(p["coins"]) - amount, "bank": int(p["bank_coins"]) + amount}

    def bank_withdraw(self, player_id: int, amount: int) -> dict:
        if not BANK_MIN_TRANSFER <= amount <= BANK_MAX_TRANSFER:
            raise GameError(
                f"Withdraw between {BANK_MIN_TRANSFER:,} and {BANK_MAX_TRANSFER:,} coins."
            )
        with self.db.transaction() as con:
            p = self._require_player(con, player_id)
            if int(p["bank_coins"]) < amount:
                raise GameError("You do not have that many coins in the bank.")
            con.execute(
                "UPDATE players SET coins=coins+?,bank_coins=bank_coins-? WHERE telegram_id=?",
                (amount, amount, player_id),
            )
            return {"amount": amount, "coins": int(p["coins"]) + amount, "bank": int(p["bank_coins"]) - amount}

    def guild_treasury(self, player_id: int) -> dict:
        with self.db.reader() as con:
            self._require_player(con, player_id)
            row = con.execute(
                "SELECT g.id,g.name,g.level,g.xp,g.coins,COALESCE(r.role,'member') AS role FROM guild_members m JOIN guilds g ON g.id=m.guild_id LEFT JOIN guild_roles r ON r.guild_id=g.id AND r.player_id=m.player_id WHERE m.player_id=?",
                (player_id,),
            ).fetchone()
            if row is None:
                raise GameError("Join a guild first.")
            members = con.execute(
                "SELECT COUNT(*) FROM guild_members WHERE guild_id=?", (row["id"],)
            ).fetchone()[0]
            return {
                **dict(row),
                "members": int(members),
                "upgrade_cost": GUILD_UPGRADE_BASE * int(row["level"]),
            }

    def guild_upgrade(self, player_id: int) -> dict:
        with self.db.transaction() as con:
            self._require_player(con, player_id)
            row = con.execute(
                "SELECT g.id,g.name,g.level,g.coins,COALESCE(r.role,'member') AS role FROM guild_members m JOIN guilds g ON g.id=m.guild_id LEFT JOIN guild_roles r ON r.guild_id=g.id AND r.player_id=m.player_id WHERE m.player_id=?",
                (player_id,),
            ).fetchone()
            if row is None:
                raise GameError("Join a guild first.")
            if str(row["role"]) != "leader":
                raise GameError("Only the guild leader can upgrade the guild.")
            cost = GUILD_UPGRADE_BASE * int(row["level"])
            if int(row["coins"]) < cost:
                raise GameError(f"Your guild treasury needs {cost:,} coins for this upgrade.")
            con.execute(
                "UPDATE guilds SET coins=coins-?,level=level+1 WHERE id=?", (cost, row["id"])
            )
            return {"name": row["name"], "level": int(row["level"]) + 1, "cost": cost}

    def admin_stats(self) -> dict:
        with self.db.reader() as con:
            return {
                "players": con.execute("SELECT COUNT(*) FROM players").fetchone()[0],
                "guilds": con.execute("SELECT COUNT(*) FROM guilds").fetchone()[0],
                "coins": con.execute("SELECT COALESCE(SUM(coins),0) FROM players").fetchone()[0],
            }

    def admin_grant(self, player_id: int, amount: int) -> bool:
        if not 1 <= amount <= 100_000:
            raise GameError("Amount must be between 1 and 100,000 coins.")
        with self.db.transaction() as con:
            self._require_player(con, player_id)
            self._add_coins(con, player_id, amount)
            self._ledger(con, player_id, None, "coins", amount, "admin_grant")
            return True

    def admin_ban(self, target_id: int, reason: str, admin_id: int, duration_minutes: int = 0) -> bool:
        if target_id <= 0:
            raise GameError("Target Telegram ID must be positive.")
        reason = " ".join(reason.split())[:160] or "No reason provided"
        if duration_minutes < 0 or duration_minutes > 30 * 24 * 60:
            raise GameError("Ban duration must be between 0 and 43,200 minutes.")
        expires_at = int(self.epoch()) + duration_minutes * 60 if duration_minutes else None
        with self.db.transaction() as con:
            if con.execute("SELECT 1 FROM players WHERE telegram_id=?", (target_id,)).fetchone() is None:
                raise GameError("That player has no game profile.")
            con.execute(
                "INSERT INTO banned_players(telegram_id,reason,banned_by,expires_at) VALUES(?,?,?,?) ON CONFLICT(telegram_id) DO UPDATE SET reason=excluded.reason,banned_by=excluded.banned_by,expires_at=excluded.expires_at,created_at=CURRENT_TIMESTAMP",
                (target_id, reason, admin_id, expires_at),
            )
            self._audit(con, admin_id, "ban", target_id, reason, f"expires_at={expires_at or 'never'}")
            listings = con.execute(
                "SELECT id,item_id,quantity FROM market_listings WHERE seller_id=? AND status='active'",
                (target_id,),
            ).fetchall()
            for listing in listings:
                con.execute(
                    "INSERT INTO inventory(player_id,item_id,quantity) VALUES(?,?,?) ON CONFLICT(player_id,item_id) DO UPDATE SET quantity=quantity+excluded.quantity",
                    (target_id, listing["item_id"], listing["quantity"]),
                )
                con.execute(
                    "UPDATE market_listings SET status='cancelled' WHERE id=? AND status='active'",
                    (listing["id"],),
                )
            return True

    def admin_unban(self, target_id: int) -> bool:
        with self.db.transaction() as con:
            cur = con.execute("DELETE FROM banned_players WHERE telegram_id=?", (target_id,))
            if cur.rowcount == 0:
                raise GameError("That player is not currently banned.")
            self._audit(con, 0, "unban", target_id)
            return True
