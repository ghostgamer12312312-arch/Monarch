from datetime import timedelta

import pytest

from monarchbot.service import GameError
from monarchbot.tournament import TournamentService


def test_arena_tracks_entered_players_and_awards_once(game):
    service, clock, _ = game
    arena = TournamentService(service)
    for user_id in (101, 202, 303):
        service.register(user_id, None, f"Hunter {user_id}")
        arena.join(user_id)
    battle = service.start_battle(101, "gate")
    while battle["status"] == "active":
        battle = service.battle_action(101, battle["id"], battle["turn"], "attack")
    status = arena.status(101)
    assert status["score"] == 100
    assert status["participants"] == 3
    # Add scores to determine each rank for the season.
    with service.db.transaction() as con:
        week = service._current_week()
        con.execute(
            "UPDATE tournament_entries SET score=300 WHERE tournament_week=? AND player_id=202", (week,)
        )
        con.execute(
            "UPDATE tournament_entries SET score=200 WHERE tournament_week=? AND player_id=303", (week,)
        )
    clock[0] += timedelta(days=7)
    awards = [arena.claim(user_id) for user_id in (202, 303, 101)]
    assert [x["place"] for x in awards] == [1, 2, 3]
    with pytest.raises(GameError, match="No unclaimed"):
        arena.claim(101)


def test_unentered_hunter_does_not_receive_arena_score(game):
    service, _, _ = game
    service.register(101, None, "Hunter")
    battle = service.start_battle(101, "gate")
    while battle["status"] == "active":
        battle = service.battle_action(101, battle["id"], battle["turn"], "attack")
    assert TournamentService(service).status(101)["score"] == 0
