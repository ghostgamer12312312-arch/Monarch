from __future__ import annotations

CLASSES: dict[str, dict[str, object]] = {
    "vanguard": {
        "name": "Vanguard",
        "hp": 120,
        "attack": 14,
        "mana": 40,
        "skill": "Aegis Break",
        "multiplier": 2.0,
    },
    "assassin": {
        "name": "Assassin",
        "hp": 92,
        "attack": 19,
        "mana": 45,
        "skill": "Shadowstep",
        "multiplier": 2.35,
    },
    "arcanist": {
        "name": "Arcanist",
        "hp": 100,
        "attack": 15,
        "mana": 60,
        "skill": "Rift Lance",
        "multiplier": 2.15,
    },
}

DUNGEONS: dict[str, dict[str, object]] = {
    "gate": {
        "name": "Crimson Gate",
        "tier": 1,
        "energy": 10,
        "enemy_hp": 72,
        "enemy_attack": 9,
        "xp": 35,
        "coins": 24,
        "essence": 1,
    },
    "rift": {
        "name": "Forgotten Rift",
        "tier": 2,
        "energy": 18,
        "enemy_hp": 118,
        "enemy_attack": 14,
        "xp": 70,
        "coins": 55,
        "essence": 2,
    },
    "abyss": {
        "name": "Abyssal Citadel",
        "tier": 3,
        "energy": 28,
        "enemy_hp": 190,
        "enemy_attack": 21,
        "xp": 125,
        "coins": 110,
        "essence": 4,
    },
}

ENEMIES: dict[str, tuple[dict[str, object], ...]] = {
    "gate": (
        {"name": "Rift Hound", "hp_scale": 0.9, "attack_scale": 0.85},
        {"name": "Ashbound Soldier", "hp_scale": 1.0, "attack_scale": 1.0},
        {"name": "Cinder Brute", "hp_scale": 1.2, "attack_scale": 1.1},
    ),
    "rift": (
        {"name": "Hollow Stalker", "hp_scale": 0.9, "attack_scale": 1.0},
        {"name": "Rift Warden", "hp_scale": 1.0, "attack_scale": 1.0},
        {"name": "Dusk Colossus", "hp_scale": 1.25, "attack_scale": 1.2},
    ),
    "abyss": (
        {"name": "Void Reaver", "hp_scale": 0.9, "attack_scale": 1.0},
        {"name": "Abyss Knight", "hp_scale": 1.0, "attack_scale": 1.0},
        {"name": "Rift Tyrant", "hp_scale": 1.3, "attack_scale": 1.25},
    ),
}

ITEMS: dict[str, dict[str, object]] = {
    "healing_tonic": {
        "name": "Healing Tonic",
        "description": "Restore 45 HP in a battle.",
        "effect": "heal",
        "amount": 45,
        "price": 35,
    },
    "mana_vial": {
        "name": "Mana Vial",
        "description": "Restore 20 mana in a battle.",
        "effect": "mana",
        "amount": 20,
        "price": 30,
    },
    "energy_shard": {
        "name": "Energy Shard",
        "description": "Restore 20 energy outside battle.",
        "effect": "energy",
        "amount": 20,
        "price": 25,
    },
}

COMPANIONS: dict[str, dict[str, object]] = {
    "ash_hound": {"name": "Ash Hound", "rarity": "Common", "hp": 12, "attack": 2},
    "rift_sentinel": {"name": "Rift Sentinel", "rarity": "Uncommon", "hp": 22, "attack": 3},
    "void_moth": {"name": "Void Moth", "rarity": "Rare", "hp": 8, "attack": 6},
    "ember_knight": {"name": "Ember Knight", "rarity": "Epic", "hp": 18, "attack": 7},
}
COMPANION_EXTRACT_COST = 25

DAILY_BASE_COINS = 75
DAILY_MAX_STREAK_BONUS = 75
SKILL_MANA_COST = 15
BATTLE_TIMEOUT_SECONDS = 30 * 60
RAID_COOLDOWN_SECONDS = 60 * 60
RAID_ENERGY_COST = 20
RAID_MAX_HP = 250_000
GUILD_MAX_MEMBERS = 30
GUILD_DONATION_MIN = 10
GUILD_DONATION_MAX = 10_000
MARKET_MAX_PRICE = 1_000_000
MARKET_FEE_PERCENT = 5
MARKET_TTL_SECONDS = 7 * 24 * 60 * 60
TOURNAMENT_ENTRY_FEE = 100
TOURNAMENT_PRIZES = (1000, 500, 250)
