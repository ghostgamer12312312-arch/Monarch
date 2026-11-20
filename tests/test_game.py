from datetime import timedelta

import pytest

from monarchbot.service import GameError


def register(game, user_id=101):
    service, _, _ = game
    return service.register(user_id, f"hunter{user_id}", f"Hunter {user_id}")


def win_battle(service, user_id, battle):
    while battle["status"] == "active":
        battle = service.battle_action(user_id, battle["id"], battle["turn"], "attack")
    return battle


def test_registration_is_idempotent_and_grants_starter_inventory(game):
    service, _, _ = game
    player, created = register(game)
    assert created
    assert player["coins"] == 100
    assert service.inventory(101)[0]["quantity"] == 2
    assert service.profile(101)["companion_name"] == "Ash Hound"
    assert service.profile(101)["attack"] == 16
    same, created = service.register(101, "new_name", "Updated Hunter")
    assert not created
    assert same["display_name"] == "Updated Hunter"
    assert service.inventory(101)[0]["quantity"] == 2


def test_daily_streak_energy_and_duplicate_claim(game):
    service, clock, _ = game
    register(game)
    before = service.daily_status(101)
    assert not before["claimed_today"] and before["next_streak"] == 1
    first = service.daily(101)
    assert first["coins"] == 75 and first["streak"] == 1
    after = service.daily_status(101)
    assert after["claimed_today"] and after["streak"] == 1 and after["energy"] == 100
    with pytest.raises(GameError, match="already claimed"):
        service.daily(101)
    clock[0] += timedelta(days=1)
    assert service.daily_status(101)["next_streak"] == 2
    second = service.daily(101)
    assert second["coins"] == 80 and second["streak"] == 2
    clock[0] += timedelta(days=2)
    assert service.daily_status(101)["next_streak"] == 1


def test_class_can_be_chosen_once_and_harder_dungeon_has_level_gate(game):
    service, _, _ = game
    register(game)
    chosen = service.choose_class(101, "assassin")
    assert chosen["name"] == "Assassin"
    with pytest.raises(GameError, match="already chosen"):
        service.choose_class(101, "arcanist")
    with pytest.raises(GameError, match="level 3"):
        service.start_battle(101, "rift")


def test_start_battle_charges_energy_and_creates_persistent_turns(game):
    service, _, _ = game
    register(game)
    battle = service.start_battle(101, "gate")
    assert battle["status"] == "active"
    assert battle["player_id"] == 101
    assert service.profile(101)["energy"] == 90
    assert service.battle_status(101)["id"] == battle["id"]
    with pytest.raises(GameError, match="already have an active"):
        service.start_battle(101, "gate")


def test_turn_battle_pays_rewards_once_and_records_quest(game):
    service, _, _ = game
    register(game)
    battle = win_battle(service, 101, service.start_battle(101, "gate"))
    assert battle["status"] == "victory"
    assert battle["result"]["reward"]["coins"] > 0
    assert service.battle_status(101) is None
    assert service.profile(101)["coins"] > 100
    quest = next(q for q in service.quests(101) if q["id"] == "clear_gate")
    assert quest["progress"] == 1
    with pytest.raises(GameError, match="already ended"):
        service.battle_action(101, battle["id"], battle["turn"], "attack")


def test_non_gate_victory_does_not_claim_gate_quest(game):
    service, _, _ = game
    register(game)
    with service.db.transaction() as con:
        con.execute("UPDATE players SET level=3 WHERE telegram_id=101")
    battle = win_battle(service, 101, service.start_battle(101, "rift"))
    assert battle["status"] == "victory"
    quest = next(q for q in service.quests(101) if q["id"] == "clear_gate")
    assert quest["progress"] == 0


def test_battle_turn_and_owner_checks_prevent_replay_or_theft(game):
    service, _, _ = game
    register(game, 101)
    register(game, 202)
    battle = service.start_battle(101, "gate")
    with pytest.raises(GameError, match="does not belong"):
        service.battle_action(202, battle["id"], battle["turn"], "attack")
    with pytest.raises(GameError, match="out of date"):
        service.battle_action(101, battle["id"], battle["turn"] + 1, "attack")
    updated = service.battle_action(101, battle["id"], battle["turn"], "guard")
    with pytest.raises(GameError, match="out of date"):
        service.battle_action(101, battle["id"], battle["turn"], "attack")
    assert updated["turn"] == battle["turn"] + 1


def test_skill_and_potion_are_consumed_and_quest_progress_is_clocked(game):
    service, clock, _ = game
    register(game)
    battle = service.start_battle(101, "gate")
    after_skill = service.battle_action(101, battle["id"], battle["turn"], "skill")
    assert after_skill["mana"] < battle["mana"]
    quest = next(q for q in service.quests(101) if q["id"] == "use_skill")
    assert quest["progress"] == 1
    # Change the controlled date and ensure the next day gets a fresh quest row.
    clock[0] += timedelta(days=1)
    assert next(q for q in service.quests(101) if q["id"] == "use_skill")["progress"] == 0


def test_battle_expiry_commits_and_releases_player_slot(game):
    service, _, epoch = game
    register(game)
    battle = service.start_battle(101, "gate")
    epoch[0] += 31 * 60
    expired = service.battle_action(101, battle["id"], battle["turn"], "attack")
    assert expired["status"] == "expired"
    fresh = service.start_battle(101, "gate")
    assert fresh["status"] == "active"


def test_daily_quests_are_claimed_once_and_spend_energy_counts_on_entry(game):
    service, _, _ = game
    register(game)
    battle = service.start_battle(101, "gate")
    win_battle(service, 101, battle)
    q = {x["id"]: x for x in service.quests(101)}
    assert q["spend_energy"]["progress"] == 10
    with pytest.raises(GameError, match="not complete"):
        service.claim_quest(101, "spend_energy")
    # Complete the cumulative 30-energy objective with two more runs after restoring energy.
    for _ in range(2):
        with service.db.transaction() as con:
            con.execute("UPDATE players SET energy=100 WHERE telegram_id=101")
        battle = service.start_battle(101, "gate")
        win_battle(service, 101, battle)
    reward = service.claim_quest(101, "spend_energy")
    assert reward["coins"] == 70
    with pytest.raises(GameError, match="already been claimed"):
        service.claim_quest(101, "spend_energy")


def test_guild_lifecycle_and_donations(game):
    service, _, _ = game
    register(game, 101)
    register(game, 202)
    guild = service.create_guild(101, "Rift Wardens")
    service.join_guild(202, guild["id"])
    donated = service.donate_guild(101, 100)
    assert donated["xp_gained"] == 10
    assert service.guild_info(202)["guild"]["name"] == "Rift Wardens"
    assert service.leave_guild(202) == "Rift Wardens"
    assert service.disband_guild(101) == "Rift Wardens"


def test_raid_caps_overkill_and_rewards_effective_damage(game):
    service, _, _epoch = game
    register(game)
    with service.db.transaction() as con:
        con.execute("UPDATE players SET last_raid_at=0 WHERE telegram_id=101")
        con.execute("UPDATE raid_state SET hp=1 WHERE id=1")
    result = service.raid_attack(101)
    assert result["damage"] == 1
    assert result["raw_damage"] > 1
    assert result["defeated"]
    assert service.raid_status()["boss"]["cycle"] == 2


def test_banned_player_cannot_play_until_unbanned(game):
    service, _, _ = game
    register(game)
    service.admin_ban(101, "spam", 999)
    with pytest.raises(GameError, match="restricted"):
        service.profile(101)
    assert service.admin_unban(101) is True
    assert service.profile(101)["display_name"] == "Hunter 101"


def test_timed_ban_expires_and_admin_controls_are_audited(game):
    service, _, epoch = game
    register(game, 101)
    register(game, 202)
    assert service.grant_admin_role(101, 202, "moderator")["role"] == "moderator"
    assert service.has_admin_role(202, frozenset(), "moderator")
    service.admin_ban(101, "short test", 202, duration_minutes=1)
    with pytest.raises(GameError, match="restricted"):
        service.profile(101)
    epoch[0] += 61
    assert service.profile(101)["telegram_id"] == 101
    service.set_maintenance(202, True)
    assert service.maintenance_enabled()
    assert any(row["action"] == "maintenance" for row in service.admin_audit())


def test_companion_extraction_duplicate_fragments_and_equipment(game):
    service, _, _ = game
    register(game)
    with service.db.transaction() as con:
        con.execute("UPDATE players SET essence=75 WHERE telegram_id=101")
    first = service.extract_companion(101)
    second = service.extract_companion(101)
    third = service.extract_companion(101)
    assert first["duplicate"] and second["fragments"] == 2
    assert third["level_up"] and third["level"] == 2
    assert service.profile(101)["attack"] == 17
    battle = service.start_battle(101, "gate")
    with pytest.raises(GameError, match="active battle"):
        service.equip_companion(101, "none")
    service.battle_action(101, battle["id"], battle["turn"], "flee")
    assert service.equip_companion(101, "none")["id"] == "none"
    assert service.profile(101)["companion_name"] == "None"
