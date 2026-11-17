from __future__ import annotations

import uuid
from dataclasses import dataclass

from .service import GameError, GameService


@dataclass(frozen=True)
class DuelResult:
    challenge_id: str
    winner_id: int
    loser_id: int
    winner_rating: int
    loser_rating: int
    reward: int


class SocialService:
    """Server-authoritative social combat and guild systems."""

    def __init__(self, game: GameService):
        self.game = game
        self.db = game.db

    def _rating(self, con, player_id: int) -> dict:
        con.execute("INSERT OR IGNORE INTO arena_ratings(player_id) VALUES(?)", (player_id,))
        return dict(con.execute("SELECT * FROM arena_ratings WHERE player_id=?", (player_id,)).fetchone())

    def challenge(self, challenger_id: int, opponent_id: int) -> dict:
        if challenger_id == opponent_id:
            raise GameError("You cannot duel yourself.")
        now = int(self.game.epoch())
        with self.db.transaction() as con:
            self.game._require_player(con, challenger_id)
            self.game._require_player(con, opponent_id)
            pending = con.execute(
                "SELECT 1 FROM duel_challenges WHERE challenger_id=? AND status='pending'", (challenger_id,)
            ).fetchone()
            if pending:
                raise GameError("You already have a pending duel challenge.")
            duel_id = uuid.uuid4().hex[:12]
            con.execute(
                "INSERT INTO duel_challenges(id,challenger_id,opponent_id,created_at,expires_at) VALUES(?,?,?,?,?)",
                (duel_id, challenger_id, opponent_id, now, now + 900),
            )
            self._rating(con, challenger_id)
            self._rating(con, opponent_id)
            return {"id": duel_id, "opponent": opponent_id, "expires": 15}

    def accept(self, player_id: int, challenge_id: str) -> DuelResult:
        now = int(self.game.epoch())
        with self.db.transaction() as con:
            duel = con.execute("SELECT * FROM duel_challenges WHERE id=?", (challenge_id,)).fetchone()
            if duel is None or int(duel["opponent_id"]) != player_id or duel["status"] != "pending":
                raise GameError("That duel challenge is invalid or already handled.")
            if int(duel["expires_at"]) <= now:
                con.execute("UPDATE duel_challenges SET status='expired' WHERE id=?", (challenge_id,))
                raise GameError("That duel challenge has expired.")
            self.game._require_player(con, player_id)
            challenger, opponent = int(duel["challenger_id"]), int(duel["opponent_id"])
            c = self.game._stats_with_companion(
                con, con.execute("SELECT * FROM players WHERE telegram_id=?", (challenger,)).fetchone()
            )
            o = self.game._stats_with_companion(
                con, con.execute("SELECT * FROM players WHERE telegram_id=?", (opponent,)).fetchone()
            )
            c_power, o_power = c[1] + c[0] // 10, o[1] + o[0] // 10
            # Deterministic tie-break keeps repeated commands safe and auditable.
            winner, loser = (
                (challenger, opponent)
                if (c_power, -challenger) >= (o_power, -opponent)
                else (opponent, challenger)
            )
            wr, lr = self._rating(con, winner), self._rating(con, loser)
            expected = 1 / (1 + 10 ** ((int(lr["rating"]) - int(wr["rating"])) / 400))
            delta = max(10, round(32 * (1 - expected)))
            con.execute(
                "UPDATE arena_ratings SET rating=rating+?,wins=wins+1 WHERE player_id=?", (delta, winner)
            )
            con.execute(
                "UPDATE arena_ratings SET rating=max(0,rating-?),losses=losses+1 WHERE player_id=?",
                (delta, loser),
            )
            con.execute(
                "UPDATE duel_challenges SET status='completed',winner_id=? WHERE id=?", (winner, challenge_id)
            )
            con.execute("UPDATE players SET coins=coins+? WHERE telegram_id=?", (100, winner))
            ratings = {
                int(r["player_id"]): int(r["rating"])
                for r in con.execute(
                    "SELECT player_id,rating FROM arena_ratings WHERE player_id IN (?,?)", (winner, loser)
                ).fetchall()
            }
            return DuelResult(challenge_id, winner, loser, ratings[winner], ratings[loser], 100)

    def leaderboard(self, limit: int = 10) -> list[dict]:
        with self.db.reader() as con:
            return [
                dict(r)
                for r in con.execute(
                    "SELECT p.display_name,a.player_id,a.rating,a.wins,a.losses FROM arena_ratings a JOIN players p ON p.telegram_id=a.player_id ORDER BY a.rating DESC,a.wins DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            ]

    def pending(self, player_id: int) -> list[dict]:
        with self.db.reader() as con:
            self.game._require_player(con, player_id)
            return [
                dict(r)
                for r in con.execute(
                    "SELECT id,challenger_id,created_at FROM duel_challenges WHERE opponent_id=? AND status='pending' ORDER BY created_at",
                    (player_id,),
                ).fetchall()
            ]

    def join_tournament(self, player_id: int) -> dict:
        week = self.game._tournament_week()
        with self.db.transaction() as con:
            self.game._require_player(con, player_id)
            existing = con.execute(
                "SELECT slot FROM tournament_slots WHERE tournament_week=? AND player_id=?", (week, player_id)
            ).fetchone()
            if existing:
                raise GameError(f"You are already in slot {existing['slot']}.")
            slot = con.execute(
                "SELECT slot FROM tournament_slots WHERE tournament_week=? ORDER BY slot", (week,)
            ).fetchall()
            used = {int(r["slot"]) for r in slot}
            free = next((x for x in range(1, 11) if x not in used), None)
            if free is None:
                raise GameError("This tournament is full (10/10 slots).")
            con.execute(
                "INSERT INTO tournament_slots(tournament_week,slot,player_id) VALUES(?,?,?)",
                (week, free, player_id),
            )
            return {"week": week, "slot": free, "participants": len(used) + 1}

    def tournament_slots(self, player_id: int) -> dict:
        week = self.game._tournament_week()
        with self.db.reader() as con:
            self.game._require_player(con, player_id)
            rows = con.execute(
                "SELECT s.slot,p.display_name,s.player_id,s.wins,s.score FROM tournament_slots s JOIN players p ON p.telegram_id=s.player_id WHERE s.tournament_week=? ORDER BY s.slot",
                (week,),
            ).fetchall()
            return {"week": week, "slots": [dict(r) for r in rows]}

    def guild_raid(self, player_id: int) -> dict:
        now = int(self.game.epoch())
        with self.db.transaction() as con:
            self.game._require_player(con, player_id)
            membership = con.execute(
                "SELECT guild_id FROM guild_members WHERE player_id=?", (player_id,)
            ).fetchone()
            if not membership:
                raise GameError("Join a guild before entering its raid.")
            gid = int(membership["guild_id"])
            state = con.execute("SELECT * FROM guild_raid_state WHERE guild_id=?", (gid,)).fetchone()
            if not state:
                con.execute(
                    "INSERT INTO guild_raid_state(guild_id,boss_name,hp,max_hp,updated_at) VALUES(?,?,?,?,?)",
                    (gid, "The Guild Devourer", 100000, 100000, now),
                )
                state = con.execute("SELECT * FROM guild_raid_state WHERE guild_id=?", (gid,)).fetchone()
            damage = max(
                1,
                self.game._stats_with_companion(
                    con, con.execute("SELECT * FROM players WHERE telegram_id=?", (player_id,)).fetchone()
                )[1]
                * 2,
            )
            damage = min(damage, int(state["hp"]))
            con.execute(
                "UPDATE guild_raid_state SET hp=hp-?,updated_at=? WHERE guild_id=?", (damage, now, gid)
            )
            con.execute(
                "INSERT INTO guild_raid_contributions(guild_id,player_id,damage,hits) VALUES(?,?,?,1) ON CONFLICT(guild_id,player_id) DO UPDATE SET damage=damage+excluded.damage,hits=hits+1",
                (gid, player_id, damage),
            )
            after = con.execute("SELECT * FROM guild_raid_state WHERE guild_id=?", (gid,)).fetchone()
            defeated = int(after["hp"]) == 0
            if defeated:
                con.execute("UPDATE guild_raid_state SET hp=max_hp,cycle=cycle+1 WHERE guild_id=?", (gid,))
                con.execute("UPDATE players SET coins=coins+? WHERE telegram_id=?", (250, player_id))
            top = [
                dict(r)
                for r in con.execute(
                    "SELECT p.display_name,c.player_id,c.damage FROM guild_raid_contributions c JOIN players p ON p.telegram_id=c.player_id WHERE c.guild_id=? ORDER BY c.damage DESC LIMIT 10",
                    (gid,),
                ).fetchall()
            ]
            fresh = con.execute("SELECT * FROM guild_raid_state WHERE guild_id=?", (gid,)).fetchone()
            return {
                "boss": fresh["boss_name"],
                "hp": fresh["hp"],
                "max_hp": fresh["max_hp"],
                "cycle": fresh["cycle"],
                "damage": damage,
                "defeated": defeated,
                "top": top,
            }

    def add_team_member(self, player_id: int, member_id: int, slot: int) -> dict:
        if not 1 <= slot <= 6:
            raise GameError("Team slots are numbered 1 through 6.")
        with self.db.transaction() as con:
            self.game._require_player(con, player_id)
            self.game._require_player(con, member_id)
            con.execute(
                "INSERT INTO player_teams(player_id,slot,member_id) VALUES(?,?,?) ON CONFLICT(player_id,slot) DO UPDATE SET member_id=excluded.member_id",
                (player_id, slot, member_id),
            )
            return {"slot": slot, "member_id": member_id}

    def team(self, player_id: int) -> list[dict]:
        with self.db.reader() as con:
            self.game._require_player(con, player_id)
            return [
                dict(r)
                for r in con.execute(
                    "SELECT t.slot,t.member_id,p.display_name FROM player_teams t JOIN players p ON p.telegram_id=t.member_id WHERE t.player_id=? ORDER BY t.slot",
                    (player_id,),
                ).fetchall()
            ]
