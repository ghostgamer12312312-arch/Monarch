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
    CLASSES,
    COMPANION_EXTRACT_COST,
    COMPANIONS,
    DAILY_BASE_COINS,
    DAILY_MAX_STREAK_BONUS,
    DUNGEONS,
    ENEMIES,
    GUILD_DONATION_MAX,
    GUILD_DONATION_MIN,
    GUILD_MAX_MEMBERS,
    ITEMS,
    RAID_COOLDOWN_SECONDS,
    RAID_ENERGY_COST,
    SKILL_MANA_COST,
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
        return hp, attack, mana, class_name

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
            return dict(
                con.execute("SELECT * FROM players WHERE telegram_id = ?", (player_id,)).fetchone()
            ), created

    def profile(self, player_id: int) -> dict:
        with self.db.reader() as con:
            p = self._require_player(con, player_id)
            hp, attack, mana, class_name = self._stats_with_companion(con, p)
            guild = con.execute(
                "SELECT g.id,g.name,g.level FROM guild_members m JOIN guilds g ON g.id=m.guild_id WHERE m.player_id=?",
                (player_id,),
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
            if p["class_locked"]:
                raise GameError("Your awakening class is already chosen and locked.")
            active = con.execute(
                "SELECT 1 FROM active_battles WHERE player_id=? AND status='active'", (player_id,)
            ).fetchone()
            if active:
                raise GameError("Finish or leave your current battle before choosing an awakening class.")
            con.execute(
                "UPDATE players SET class_id=?,class_locked=1 WHERE telegram_id=?", (class_id, player_id)
            )
            return {"id": class_id, "name": CLASSES[class_id]["name"]}

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

    def start_battle(self, player_id: int, dungeon_id: str) -> dict:
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
            cost = int(dungeon["energy"])
            if int(p["energy"]) < cost:
                raise GameError(
                    f"You need {cost} energy for this dungeon. Claim /daily or use an energy item."
                )
            con.execute("UPDATE players SET energy=energy-? WHERE telegram_id=?", (cost, player_id))
            self._increment_quest(con, player_id, "spend_energy", cost)
            hp, _, mana, _ = self._stats_with_companion(con, p)
            enemy_def = self.rng.choice(ENEMIES[dungeon_id])
            scale_hp = float(enemy_def["hp_scale"])
            scale_attack = float(enemy_def["attack_scale"])
            enemy_hp = max(1, int((int(dungeon["enemy_hp"]) + (int(p["level"]) - 1) * 8) * scale_hp))
            enemy_attack = max(
                1, int((int(dungeon["enemy_attack"]) + (int(p["level"]) - 1) * 1.5) * scale_attack)
            )
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
            return self._battle_dict(
                con.execute("SELECT * FROM active_battles WHERE id=?", (battle_id,)).fetchone()
            )

    @staticmethod
    def _battle_dict(row) -> dict:
        result = dict(row)
        result["result"] = json.loads(result.pop("result_json") or "{}")
        return result

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
            return self._battle_dict(row)

    def battle_action(self, player_id: int, battle_id: str, expected_turn: int, action: str) -> dict:
        if action not in {"attack", "guard", "skill", "potion", "mana", "flee"}:
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
                    (json.dumps({"log": ["Battle expired."], "reward": {}}), battle_id),
                )
                return self._battle_dict(
                    con.execute("SELECT * FROM active_battles WHERE id=?", (battle_id,)).fetchone()
                )
            if int(battle["turn"]) != expected_turn:
                raise GameError("That battle screen is out of date. Use /battle_status to refresh it.")
            player_hp, enemy_hp, mana = int(battle["player_hp"]), int(battle["enemy_hp"]), int(battle["mana"])
            pmax = int(battle["player_max_hp"])
            player = p
            _, attack, _, _ = self._stats_with_companion(con, player)
            logs: list[str] = []
            guarded = False
            if action == "flee":
                con.execute("UPDATE active_battles SET status='escaped' WHERE id=?", (battle_id,))
                logs.append("You retreated. No dungeon rewards were earned.")
            else:
                if action == "attack":
                    damage = max(1, int(attack * self.rng.uniform(0.85, 1.15)))
                    enemy_hp = max(0, enemy_hp - damage)
                    logs.append(f"You strike for {damage} damage.")
                elif action == "guard":
                    guarded = True
                    logs.append("You brace for impact. The next hit is reduced.")
                elif action == "skill":
                    if mana < SKILL_MANA_COST:
                        raise GameError(f"You need {SKILL_MANA_COST} mana to use your awakening skill.")
                    class_data = CLASSES[p["class_id"]]
                    damage = max(
                        1, int(attack * float(class_data["multiplier"]) * self.rng.uniform(0.9, 1.1))
                    )
                    enemy_hp = max(0, enemy_hp - damage)
                    mana -= SKILL_MANA_COST
                    logs.append(f"{class_data['skill']} hits for {damage} damage.")
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
                    restored = min(20, int(battle["max_mana"]) - mana)
                    if restored <= 0:
                        raise GameError("Your mana is already full.")
                    con.execute(
                        "UPDATE inventory SET quantity=quantity-1 WHERE player_id=? AND item_id='mana_vial'",
                        (player_id,),
                    )
                    mana += restored
                    logs.append(f"Mana Vial restores {restored} mana.")

                if enemy_hp > 0:
                    hit = max(1, int(int(battle["enemy_attack"]) * self.rng.uniform(0.8, 1.2)))
                    if guarded:
                        hit = max(1, hit // 3)
                    player_hp = max(0, player_hp - hit)
                    logs.append(f"{battle['enemy_name']} hits you for {hit} damage.")

                status = "active"
                reward: dict[str, int] = {}
                if enemy_hp <= 0:
                    status = "victory"
                    d = DUNGEONS[battle["dungeon_id"]]
                    coins = int(d["coins"]) + int(p["level"]) * 4
                    xp = int(d["xp"]) + int(p["level"]) * 8
                    essence = int(d["essence"])
                    self._add_coins(con, player_id, coins)
                    self._add_essence(con, player_id, essence)
                    levels = self._award_xp(con, player_id, xp)
                    self._increment_quest(con, player_id, "clear_gate", 1)
                    self._tournament_score(con, player_id, int(d["tier"]) * 100)
                    self._guild_xp(con, player_id, max(1, int(d["tier"]) * 8))
                    reward = {"coins": coins, "xp": xp, "essence": essence, "levels": levels}
                    logs.append(f"Victory! Rewards: {coins} coins, {xp} XP, {essence} essence.")
                elif player_hp <= 0:
                    status = "defeat"
                    logs.append("You were defeated. Catch your breath and try again.")
                next_turn = int(battle["turn"]) + 1
                con.execute(
                    """UPDATE active_battles SET enemy_hp=?,player_hp=?,mana=?,turn=?,status=?,result_json=?
                       WHERE id=? AND status='active'""",
                    (
                        enemy_hp,
                        player_hp,
                        mana,
                        next_turn,
                        status,
                        json.dumps({"log": logs, "reward": reward}),
                        battle_id,
                    ),
                )
            updated = con.execute("SELECT * FROM active_battles WHERE id=?", (battle_id,)).fetchone()
            result = self._battle_dict(updated)
            if action == "flee":
                result["result"] = {"log": logs, "reward": {}}
            return result

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
        with self.db.reader() as con:
            return [
                dict(row)
                for row in con.execute(
                    "SELECT display_name,level,xp,coins,class_id FROM players ORDER BY level DESC,xp DESC,coins DESC LIMIT 10"
                ).fetchall()
            ]

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
                "SELECT g.id,g.name,g.level,g.xp,g.leader_id FROM guild_members m JOIN guilds g ON g.id=m.guild_id WHERE m.player_id=?",
                (player_id,),
            ).fetchone()
            if membership is None:
                rows = con.execute(
                    "SELECT g.id,g.name,g.level,COUNT(m.player_id) AS members FROM guilds g LEFT JOIN guild_members m ON m.guild_id=g.id GROUP BY g.id ORDER BY g.level DESC,members DESC LIMIT 10"
                ).fetchall()
                return {"guild": None, "guilds": [dict(row) for row in rows]}
            members = con.execute(
                "SELECT p.display_name,m.player_id FROM guild_members m JOIN players p ON p.telegram_id=m.player_id WHERE m.guild_id=? ORDER BY (m.player_id=?) DESC,p.display_name COLLATE NOCASE LIMIT 30",
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
            return {"boss": dict(boss), "top": [dict(row) for row in top]}

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
                con.execute(
                    "UPDATE raid_state SET hp=max_hp,cycle=cycle+1,updated_at=CURRENT_TIMESTAMP WHERE id=1"
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
            }

    def _tournament_score(self, con, player_id: int, score: int) -> None:
        con.execute(
            "UPDATE tournament_entries SET score=score+? WHERE tournament_week=? AND player_id=?",
            (max(0, score), self._current_week(), player_id),
        )

    def _tournament_week(self) -> str:
        iso = self.clock().astimezone(UTC).date().isocalendar()
        return f"{iso.year}-W{iso.week:02d}"

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
