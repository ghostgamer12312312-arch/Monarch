from __future__ import annotations

import pytest

from monarchbot.service import GameError
from monarchbot.social import SocialService


def test_duel_updates_ratings_and_pays_winner(game):
    service, _, _ = game
    service.register(101, "one", "One")
    service.register(202, "two", "Two")
    social = SocialService(service)
    challenge = social.challenge(101, 202)
    result = social.accept(202, challenge["id"])
    assert result.winner_id == 101
    assert result.reward == 100
    assert social.leaderboard()[0]["wins"] == 1


def test_tournament_has_ten_unique_slots(game):
    service, _, _ = game
    social = SocialService(service)
    for player_id in range(101, 111):
        service.register(player_id, str(player_id), str(player_id))
        assert social.join_tournament(player_id)["participants"] == player_id - 100
    service.register(111, "111", "Eleventh")
    with pytest.raises(GameError, match="full"):
        social.join_tournament(111)


def test_guild_raid_tracks_contribution_and_team(game):
    service, _, _ = game
    service.register(101, "one", "One")
    service.register(202, "two", "Two")
    social = SocialService(service)
    guild = service.create_guild(101, "Raid Team")
    service.join_guild(202, guild["id"])
    social.add_team_member(101, 202, 1)
    assert social.team(101)[0]["member_id"] == 202
    first = social.guild_raid(101)
    second = social.guild_raid(202)
    assert first["damage"] > 0 and second["damage"] > 0
    assert {row["player_id"] for row in second["top"]} == {101, 202}


def test_guild_officers_and_weekly_reset_are_idempotent(game):
    service, _, _ = game
    service.register(101, "one", "One")
    service.register(202, "two", "Two")
    social = SocialService(service)
    guild = service.create_guild(101, "Wardens")
    service.join_guild(202, guild["id"])
    assert service.guild_promote(101, 202) == "officer"
    assert service.guild_demote(101, 202) == "member"
    assert service.guild_kick(101, 202) == "202"
    # Exercise the reset marker twice using the leader who remains in the guild.
    social.guild_raid(101)
    assert social.reset_weekly_raids() == 1
    assert social.reset_weekly_raids() == 0
