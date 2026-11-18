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

    def start_tournament(self, player_id: int) -> dict:
        week = self.game._tournament_week()
        now = int(self.game.epoch())
        with self.db.transaction() as con:
            self.game._require_player(con, player_id)
            entrants = con.execute(
                "SELECT player_id FROM tournament_slots WHERE tournament_week=? ORDER BY slot", (week,)
            ).fetchall()
            if len(entrants) < 2:
                raise GameError("At least two hunters must join before the tournament can start.")
            existing = con.execute(
                "SELECT COUNT(*) FROM tournament_matches WHERE tournament_week=?", (week,)
            ).fetchone()[0]
            if existing:
                raise GameError("This tournament has already been scheduled.")
            ids = [int(row["player_id"]) for row in entrants]
            self.game.rng.shuffle(ids)
            matches = []
            for index in range(0, len(ids) - 1, 2):
                scheduled = now + self.game.rng.randint(0, 86400)
                match_id = uuid.uuid4().hex[:10]
                con.execute(
                    "INSERT INTO tournament_matches(id,tournament_week,round,player_a,player_b,scheduled_at,deadline_at) VALUES(?,?,?,?,?,?,?)",
                    (match_id, week, 1, ids[index], ids[index + 1], scheduled, scheduled + 172800),
                )
                matches.append(match_id)
            if len(ids) % 2:
                bye = ids[-1]
                con.execute(
                    "UPDATE tournament_slots SET wins=wins+1,score=score+500 WHERE tournament_week=? AND player_id=?",
                    (week, bye),
                )
            return {"week": week, "matches": matches, "scheduled_within_hours": 24, "duration_days": 2}

    def checkin_tournament(self, player_id: int, match_id: str) -> dict:
        now = int(self.game.epoch())
        with self.db.transaction() as con:
            self.game._require_player(con, player_id)
            match = con.execute(
                "SELECT * FROM tournament_matches WHERE id=? AND status='scheduled'", (match_id,)
            ).fetchone()
            if match is None or player_id not in {int(match["player_a"]), int(match["player_b"])}:
                raise GameError("That scheduled match is not yours or is already resolved.")
            if now > int(match["deadline_at"]):
                raise GameError("The match check-in window has closed.")
            column = "present_a" if player_id == int(match["player_a"]) else "present_b"
            con.execute(
                f"UPDATE tournament_matches SET {column}=1,status=CASE WHEN present_a+present_b+1>=2 THEN 'active' ELSE status END WHERE id=?",
                (match_id,),
            )
            return {
                "id": match_id,
                "player_id": player_id,
                "status": "active"
                if int(match["present_a"]) + int(match["present_b"]) + 1 >= 2
                else "scheduled",
            }

    def resolve_tournaments(self) -> int:
        now = int(self.game.epoch())
        resolved = 0
        with self.db.transaction() as con:
            due = con.execute(
                "SELECT * FROM tournament_matches WHERE status IN ('scheduled','active') AND (scheduled_at<=? OR deadline_at<=?)",
                (now, now),
            ).fetchall()
            for match in due:
                a, b = int(match["player_a"]), int(match["player_b"])
                present_a, present_b = int(match["present_a"]), int(match["present_b"])
                if now < int(match["deadline_at"]) and not (present_a and present_b):
                    continue
                if present_a and not present_b:
                    winner = a
                elif present_b and not present_a:
                    winner = b
                elif not present_a and not present_b:
                    con.execute("UPDATE tournament_matches SET status='cancelled' WHERE id=?", (match["id"],))
                    resolved += 1
                    continue
                else:
                    winner = a if self._combined_power(con, a) >= self._combined_power(con, b) else b
                loser = b if winner == a else a
                con.execute(
                    "UPDATE tournament_matches SET status='completed',winner_id=? WHERE id=?",
                    (winner, match["id"]),
                )
                con.execute(
                    "UPDATE tournament_slots SET wins=wins+1,score=score+500 WHERE tournament_week=? AND player_id=?",
                    (match["tournament_week"], winner),
                )
                con.execute(
                    "UPDATE tournament_slots SET score=score+100 WHERE tournament_week=? AND player_id=?",
                    (match["tournament_week"], loser),
                )
                resolved += 1
        return resolved

    def tournament_matches(self, player_id: int) -> list[dict]:
        week = self.game._tournament_week()
        with self.db.reader() as con:
            self.game._require_player(con, player_id)
            return [
                dict(row)
                for row in con.execute(
                    "SELECT * FROM tournament_matches WHERE tournament_week=? AND (player_a=? OR player_b=?) ORDER BY scheduled_at",
                    (week, player_id, player_id),
                ).fetchall()
            ]

    def _combined_power(self, con, player_id: int) -> int:
        player = con.execute("SELECT * FROM players WHERE telegram_id=?", (player_id,)).fetchone()
        if player is None:
            return 0
        power = self.game._stats_with_companion(con, player)[1]
        members = con.execute("SELECT member_id FROM player_teams WHERE player_id=?", (player_id,)).fetchall()
        for member in members:
            teammate = con.execute(
                "SELECT * FROM players WHERE telegram_id=?", (member["member_id"],)
            ).fetchone()
            if teammate is not None:
                power += self.game._stats_with_companion(con, teammate)[1]
        return power

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
            damage = max(1, self._combined_power(con, player_id) * 2)
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

    def reset_weekly_raids(self) -> int:
        """Reset every guild raid once per UTC ISO week; safe to call hourly."""
        week = self.game._current_week()
        now = int(self.game.epoch())
        with self.db.transaction() as con:
            if con.execute("SELECT 1 FROM guild_raid_resets WHERE week=?", (week,)).fetchone():
                return 0
            con.execute("UPDATE guild_raid_state SET hp=max_hp,cycle=cycle+1,updated_at=?", (now,))
            con.execute("DELETE FROM guild_raid_contributions")
            con.execute("INSERT INTO guild_raid_resets(week,reset_at) VALUES(?,?)", (week, now))
            return con.execute("SELECT COUNT(*) FROM guild_raid_state").fetchone()[0]

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

    def team_power(self, player_id: int) -> dict:
        with self.db.reader() as con:
            self.game._require_player(con, player_id)
            count = con.execute(
                "SELECT COUNT(*) FROM player_teams WHERE player_id=?", (player_id,)
            ).fetchone()[0]
            return {"members": int(count) + 1, "power": self._combined_power(con, player_id)}
