from __future__ import annotations

from .catalog import TOURNAMENT_ENTRY_FEE, TOURNAMENT_PRIZES
from .service import GameError, GameService


class TournamentService:
    def __init__(self, game: GameService):
        self.game = game
        self.db = game.db

    def join(self, player_id: int) -> dict:
        week = self.game._tournament_week()
        with self.db.transaction() as con:
            player = self.game._require_player(con, player_id)
            if con.execute(
                "SELECT 1 FROM tournament_entries WHERE tournament_week=? AND player_id=?", (week, player_id)
            ).fetchone():
                raise GameError("You have already entered this week's arena.")
            if int(player["coins"]) < TOURNAMENT_ENTRY_FEE:
                raise GameError(f"Entry costs {TOURNAMENT_ENTRY_FEE} coins.")
            con.execute(
                "UPDATE players SET coins=coins-? WHERE telegram_id=?", (TOURNAMENT_ENTRY_FEE, player_id)
            )
            con.execute(
                "INSERT INTO tournament_entries(tournament_week,player_id) VALUES(?,?)", (week, player_id)
            )
            return {"week": week, "fee": TOURNAMENT_ENTRY_FEE}

    def status(self, player_id: int) -> dict:
        week = self.game._tournament_week()
        with self.db.reader() as con:
            self.game._require_player(con, player_id)
            entry = con.execute(
                "SELECT score FROM tournament_entries WHERE tournament_week=? AND player_id=?",
                (week, player_id),
            ).fetchone()
            rows = con.execute(
                "SELECT p.display_name,e.score,e.player_id FROM tournament_entries e JOIN players p ON p.telegram_id=e.player_id WHERE e.tournament_week=? ORDER BY e.score DESC,e.player_id ASC LIMIT 10",
                (week,),
            ).fetchall()
            return {
                "week": week,
                "joined": entry is not None,
                "score": int(entry["score"]) if entry else 0,
                "participants": con.execute(
                    "SELECT COUNT(*) FROM tournament_entries WHERE tournament_week=?", (week,)
                ).fetchone()[0],
                "leaders": [dict(row) for row in rows],
                "prizes": TOURNAMENT_PRIZES,
            }

    @staticmethod
    def _settle(con, current_week: str) -> None:
        weeks = con.execute(
            "SELECT DISTINCT tournament_week FROM tournament_entries WHERE tournament_week<? ORDER BY tournament_week",
            (current_week,),
        ).fetchall()
        for item in weeks:
            week = item["tournament_week"]
            if con.execute(
                "SELECT 1 FROM tournament_awards WHERE tournament_week=? LIMIT 1", (week,)
            ).fetchone():
                continue
            winners = con.execute(
                "SELECT player_id FROM tournament_entries WHERE tournament_week=? AND score>0 ORDER BY score DESC,player_id ASC LIMIT 3",
                (week,),
            ).fetchall()
            for place, (winner, prize) in enumerate(zip(winners, TOURNAMENT_PRIZES), start=1):
                con.execute(
                    "INSERT INTO tournament_awards(tournament_week,player_id,place,prize) VALUES(?,?,?,?)",
                    (week, winner["player_id"], place, prize),
                )

    def claim(self, player_id: int) -> dict:
        week = self.game._tournament_week()
        with self.db.transaction() as con:
            self.game._require_player(con, player_id)
            self._settle(con, week)
            award = con.execute(
                "SELECT tournament_week,place,prize FROM tournament_awards WHERE player_id=? AND claimed=0 ORDER BY tournament_week,place LIMIT 1",
                (player_id,),
            ).fetchone()
            if award is None:
                raise GameError("No unclaimed arena prize is available.")
            updated = con.execute(
                "UPDATE tournament_awards SET claimed=1 WHERE tournament_week=? AND player_id=? AND claimed=0",
                (award["tournament_week"], player_id),
            )
            if updated.rowcount != 1:
                raise GameError("That arena prize was already claimed.")
            con.execute("UPDATE players SET coins=coins+? WHERE telegram_id=?", (award["prize"], player_id))
            return {
                "week": award["tournament_week"],
                "place": int(award["place"]),
                "prize": int(award["prize"]),
            }
