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

HUNT_ENERGY_COST: dict[str, int] = {"gate": 5, "rift": 9, "abyss": 14}

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

CLASS_CRIT = {"vanguard": 0.08, "assassin": 0.20, "arcanist": 0.12}
CLASS_DODGE = {"vanguard": 0.05, "assassin": 0.18, "arcanist": 0.08}
SKILL_COOLDOWN_TURNS = 2
ENEMY_SPECIAL_EVERY = 3
EXPLORE_ENERGY_COST = 8

REGIONS: dict[str, dict[str, object]] = {
    "crimson_gate": {
        "name": "Crimson Gate",
        "dungeon": "gate",
        "level": 1,
        "description": "Scorched outpost where rift hounds prowl. A safe first hunt.",
    },
    "forgotten_rift": {
        "name": "Forgotten Rift",
        "dungeon": "rift",
        "level": 3,
        "description": "Cracked ruins leaking old magic and hollow stalkers.",
    },
    "abyssal_citadel": {
        "name": "Abyssal Citadel",
        "dungeon": "abyss",
        "level": 8,
        "description": "A black keep ruled by rift tyrants. Only veterans survive.",
    },
}


SKILLS: dict[str, dict[str, object]] = {
    "quick_strike": {
        "name": "Quick Strike",
        "rank": "E",
        "price": 0,
        "mana": 4,
        "multiplier": 1.20,
        "effect": "none",
        "description": "A fast opening slash. Free for every hunter.",
    },
    "shadow_cut": {
        "name": "Shadow Cut",
        "rank": "F",
        "price": 120,
        "mana": 6,
        "multiplier": 1.35,
        "effect": "lifesteal",
        "description": "Drain a little life from the target.",
    },
    "ember_slash": {
        "name": "Ember Slash",
        "rank": "C",
        "price": 300,
        "mana": 8,
        "multiplier": 1.50,
        "effect": "burn",
        "description": "Set the enemy ablaze for 3 turns.",
    },
    "venom_edge": {
        "name": "Venom Edge",
        "rank": "B",
        "price": 650,
        "mana": 9,
        "multiplier": 1.55,
        "effect": "poison",
        "description": "Coat your blade in rift venom for 3 turns.",
    },
    "blood_rush": {
        "name": "Blood Rush",
        "rank": "D",
        "price": 1100,
        "mana": 12,
        "multiplier": 1.80,
        "effect": "lifesteal",
        "description": "A reckless charge that heals you for the damage dealt.",
    },
    "quake_breaker": {
        "name": "Quake Breaker",
        "rank": "A",
        "price": 1800,
        "mana": 14,
        "multiplier": 2.00,
        "effect": "stun",
        "description": "Shatter the ground and stun the enemy.",
    },
    "frost_bind": {
        "name": "Frost Bind",
        "rank": "S",
        "price": 2800,
        "mana": 16,
        "multiplier": 2.00,
        "effect": "stun",
        "description": "Freeze the enemy in place for a turn.",
    },
    "soul_reap": {
        "name": "Soul Reap",
        "rank": "SS",
        "price": 4200,
        "mana": 18,
        "multiplier": 2.40,
        "effect": "lifesteal",
        "description": "Tear at the soul and recover a portion as HP.",
    },
    "void_pierce": {
        "name": "Void Pierce",
        "rank": "SSS",
        "price": 6000,
        "mana": 20,
        "multiplier": 2.60,
        "effect": "poison",
        "description": "A void-charged thrust that poisons deeply.",
    },
    "monarch_verdict": {
        "name": "Monarch's Verdict",
        "rank": "SR",
        "price": 9000,
        "mana": 24,
        "multiplier": 3.00,
        "effect": "burn",
        "description": "Pass judgement with overwhelming shadow flame.",
    },
    "shadow_domain": {
        "name": "Shadow Domain",
        "rank": "SP",
        "price": 14000,
        "mana": 28,
        "multiplier": 3.50,
        "effect": "stun",
        "description": "The sovereign's domain. Massive damage and a stun.",
    },
}
DEFAULT_SKILL = "quick_strike"

GACHA_RANKS: tuple[str, ...] = ("E", "F", "C", "B", "D", "A", "S", "SS", "SSS", "SR", "SP")
GACHA_WEIGHTS: dict[str, float] = {
    "E": 30.0,
    "F": 21.0,
    "C": 15.0,
    "B": 11.0,
    "D": 8.0,
    "A": 7.0,
    "S": 4.0,
    "SS": 2.4,
    "SSS": 1.0,
    "SR": 0.4,
    "SP": 0.2,
}
GACHA_BONUS: dict[str, tuple[int, int]] = {
    "E": (1, 3),
    "F": (2, 5),
    "C": (3, 8),
    "B": (5, 12),
    "D": (7, 18),
    "A": (10, 26),
    "S": (15, 40),
    "SS": (22, 60),
    "SSS": (32, 90),
    "SR": (45, 130),
    "SP": (65, 200),
}
GACHA_COST = 25
GACHA_TEN_COST = 225
GACHA_PITY = 50

GACHA_CHARACTERS: dict[str, dict[str, object]] = {
    # E
    "shadow_soldier": {"name": "Shadow Soldier", "rank": "E", "title": "Silent ranks"},
    "shadow_beast": {"name": "Shadow Beast", "rank": "E", "title": "Feral echo"},
    "low_orc": {"name": "Low Orc", "rank": "E", "title": "Gate fodder"},
    "goblin_scout": {"name": "Goblin Scout", "rank": "E", "title": "Twitchy scout"},
    "steel_fang": {"name": "Steel Fang", "rank": "E", "title": "Iron-jawed hound"},
    # F
    "e_rank_hunter": {"name": "E-Rank Hunter", "rank": "F", "title": "Fresh awaken"},
    "shadow_archer": {"name": "Shadow Archer", "rank": "F", "title": "Rooftop volley"},
    "cave_bat": {"name": "Cave Bat", "rank": "F", "title": "Echo swarm"},
    "rift_slime": {"name": "Rift Slime", "rank": "F", "title": "Corrosive blob"},
    "stone_golem": {"name": "Stone Golem", "rank": "F", "title": "Slow but sturdy"},
    # C
    "kang_taeshik": {"name": "Kang Taeshik", "rank": "C", "title": "C-Rank brawler"},
    "kim_sangshik": {"name": "Kim Sangshik", "rank": "C", "title": "Guild striker"},
    "hwang_dongsuk": {"name": "Hwang Dongsuk", "rank": "C", "title": "Raid saboteur"},
    "shadow_assassin": {"name": "Shadow Assassin", "rank": "C", "title": "Killing intent"},
    "baruka": {"name": "Baruka", "rank": "C", "title": "Ice elf chief"},
    # B
    "lee_joohee": {"name": "Lee Joohee", "rank": "B", "title": "Healer resolve"},
    "park_heejin": {"name": "Park Heejin", "rank": "B", "title": "Steady support"},
    "han_songyi": {"name": "Han Song-Yi", "rank": "B", "title": "Fast learner"},
    "yoo_jinho": {"name": "Yoo Jinho", "rank": "B", "title": "Loyal tank"},
    "kim_chul": {"name": "Kim Chul", "rank": "B", "title": "White Tiger blade"},
    "shadow_mage": {"name": "Shadow Mage", "rank": "B", "title": "Arcane echo"},
    # D
    "hwang_dongsoo": {"name": "Hwang Dongsoo", "rank": "D", "title": "Brutal hunter"},
    "goto_ryuji": {"name": "Goto Ryuji", "rank": "D", "title": "Rival swordsman"},
    "min_byunggu": {"name": "Min Byung-Gu", "rank": "D", "title": "Sacred healer"},
    "woo_jinchul": {"name": "Woo Jinchul", "rank": "D", "title": "Association shield"},
    "shadow_knight": {"name": "Shadow Knight", "rank": "D", "title": "Armored echo"},
    "tusk": {"name": "Tusk", "rank": "D", "title": "Orc shaman"},
    # A
    "cha_haein": {"name": "Cha Hae-In", "rank": "A", "title": "Dancer of blades"},
    "baek_yoonho": {"name": "Baek Yoonho", "rank": "A", "title": "White Tiger lord"},
    "choi_jongin": {"name": "Choi Jong-In", "rank": "A", "title": "Flame mage"},
    "go_gunhee": {"name": "Go Gunhee", "rank": "A", "title": "Association chairman"},
    "lim_taegyu": {"name": "Lim Tae-Gyu", "rank": "A", "title": "Shadows of speed"},
    "kaisel": {"name": "Kaisel", "rank": "A", "title": "Wyvern mount"},
    # S
    "sung_jinwoo": {"name": "Sung Jinwoo", "rank": "S", "title": "Shadow monarch"},
    "thomas_andre": {"name": "Thomas Andre", "rank": "S", "title": "Goliath"},
    "liu_zhigang": {"name": "Liu Zhigang", "rank": "S", "title": "China's strongest"},
    "christopher_reed": {"name": "Christopher Reed", "rank": "S", "title": "Ruler's vessel"},
    "lennart_niermann": {"name": "Lennart Niermann", "rank": "S", "title": "Northern blade"},
    "siddharth_bachchan": {"name": "Siddharth Bachchan", "rank": "S", "title": "Wind sage"},
    "adam_white": {"name": "Adam White", "rank": "S", "title": "Hunter's analyst"},
    # SS
    "igris": {"name": "Igris", "rank": "SS", "title": "Blood-red knight"},
    "beru": {"name": "Beru", "rank": "SS", "title": "Ant king's shadow"},
    "iron": {"name": "Iron", "rank": "SS", "title": "Taekwondo shadow"},
    "tank": {"name": "Tank", "rank": "SS", "title": "Bear guardian"},
    "fangs": {"name": "Fangs", "rank": "SS", "title": "Alpha shadow"},
    "jima": {"name": "Jima", "rank": "SS", "title": "Naga healer"},
    # SSS
    "bellion": {"name": "Bellion", "rank": "SSS", "title": "Shadow grand marshal"},
    "greed": {"name": "Greed", "rank": "SSS", "title": "Devouring armor"},
    "kandiaru": {"name": "Kandiaru", "rank": "SSS", "title": "System architect"},
    "ant_king": {"name": "Ant King", "rank": "SSS", "title": "Island terror"},
    # SR
    "kamish": {"name": "Kamish", "rank": "SR", "title": "Dragon of disaster"},
    "baran": {"name": "Baran", "rank": "SR", "title": "Demon king"},
    "ashborn": {"name": "Ashborn", "rank": "SR", "title": "True shadow monarch"},
    # SP
    "monarch_of_shadows": {"name": "Monarch of Shadows", "rank": "SP", "title": "Absolute sovereign"},
    "shadow_sovereign": {"name": "Shadow Sovereign", "rank": "SP", "title": "Beyond limits"},
    "ruler_of_fragments": {"name": "Ruler of Fragments", "rank": "SP", "title": "Forbidden apex"},
}

WORLD_BOSSES: tuple[dict[str, object], ...] = (
    {
        "key": "ant_king",
        "name": "Ant King",
        "art": "boss_ant_king",
        "base_hp": 250_000,
        "hp_scale": 1.0,
        "description": "A winged terror from the Jeju island gate.",
    },
    {
        "key": "kamish",
        "name": "Kamish",
        "art": "boss_kamish",
        "base_hp": 300_000,
        "hp_scale": 1.1,
        "description": "The dragon that erased a city in one breath.",
    },
    {
        "key": "baran",
        "name": "Baran",
        "art": "boss_baran",
        "base_hp": 350_000,
        "hp_scale": 1.2,
        "description": "Demon king of the red gate, wreathed in storm.",
    },
    {
        "key": "monarch_of_shadows",
        "name": "Monarch of Shadows",
        "art": "boss_monarch",
        "base_hp": 400_000,
        "hp_scale": 1.3,
        "description": "The absolute sovereign of the shadow army.",
    },
)
RAID_BASE_HP = 250_000

GUILD_UPGRADE_BASE = 500
BANK_MIN_TRANSFER = 100
BANK_MAX_TRANSFER = 1_000_000
