# Monarch

**Monarch** is an original hunter-and-rift Telegram RPG starter. It focuses on a persistent, turn-based battle interface inside Telegram: the player taps actions and the bot edits one battle screen after each turn. The setting, names, and game text are original; this repository does not include official anime characters, art, music, or story text.

This is a playable starter release, not a claim of a finished commercial game or a guarantee of zero bugs. The current core includes persistent hunter profiles and ranks, three awakening classes, an original echo-companion collection (starter companion, essence-funded summons, duplicate fragments and passive stats), energy, daily claims and quests, three dungeon tiers, turn-by-turn attacks/guard/skills/items/retreat, levels and rewards, a shared raid boss, guilds, a weekly arena, an escrow-based market, moderation commands, SQLite backups, and tests.

## Requirements and first run

Use Python 3.11 or newer. Create a bot with BotFather and keep its token private. **If you previously pasted a bot token into a chat or public location, revoke it in BotFather and use a new one.** Never add the token to source code, screenshots, GitHub, or `.env.example`.

```sh
git clone https://github.com/ghostgamer12312312-arch/Monarch.git
cd Monarch
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` locally and set `BOT_TOKEN`. Optionally set `ADMIN_IDS` to comma-separated numeric Telegram IDs. Leave it blank if you do not need admin commands. Then start the bot:

```sh
python -m monarchbot.main
```

The SQLite database is created at `data/monarch.sqlite3` by default. Change `DATABASE_PATH` to choose another persistent path. Run exactly one long-polling process per bot token. For Docker, build with `docker build -t monarch:latest .`, then use a persistent named volume and inject secrets at runtime, for example `docker volume create monarch_data` followed by `docker run -d --name monarch --restart unless-stopped --env-file .env --mount source=monarch_data,target=/app/data monarch:latest`. The image runs as UID 10001; if you use a host bind mount instead, make sure the mounted directory is writable by UID 10001. Prefer your platform's secret store over a plain Docker env file on shared machines.

## Android Termux and tmux

Install Termux from one trusted distribution source and keep its add-ons from the same source. Open Termux and run:

```sh
pkg update -y
pkg install -y python git tmux
cd "$HOME"
git clone https://github.com/ghostgamer12312312-arch/Monarch.git
cd Monarch
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
nano .env
```

Enter the newly rotated bot token in `.env`, save, and close the editor. Start a named tmux session:

```sh
tmux new-session -s monarch
./start.sh
```

Detach without stopping the bot with **Ctrl+B**, then **D**. Return to its terminal with `tmux attach -t monarch`. Stop the bot with Ctrl+C inside that session, then type `exit`. Android battery management, force-stops, reboots, and network changes can still stop Termux. A phone is useful for development and testing, but it is not dependable 24/7 hosting. For optional wake-lock support, install the Termux:API package and its matching companion app from the same distribution source, then run `termux-wake-lock` in Termux.

## Commands

Use `/start` to create the player profile and `/menu` for the button-based game hub. `/class` lists awakening paths; `/class vanguard`, `/class assassin`, or `/class arcanist` chooses one once. `/profile` shows stats. `/daily_status` checks whether today's reward is claimed, your current and next streak, and energy; `/daily` claims it. `/quests` and `/quest_claim <quest_id>` handle objectives. `/codex` lists classes, dungeons, supplies, and companions; narrow it with `/codex classes`, `/codex dungeons`, `/codex items`, or `/codex companions`.

`/battle gate`, `/battle rift`, or `/battle abyss` starts a persistent encounter. Rift requires level 3 and Abyss requires level 8. During combat, tap **Quick Attack**, **Guard**, **Awakening Skill**, **Healing Tonic**, **Mana Vial**, or **Retreat**. `/battle_status` recreates the current screen if needed. A battle expires after 30 minutes; energy is spent on entry and is not refunded for defeat or retreat.

`/shop`, `/buy <item_id> [quantity]`, `/inventory`, and `/use energy_shard` manage items. Healing Tonics and Mana Vials are used from battle buttons. `/companions` lists your echo allies, `/extract` spends 25 essence to summon one, and `/equip <companion_id|none>` sets its passive bonuses outside battle. Duplicate extractions create fragments; three fragments level up that companion. `/leaderboard` displays top hunters. Guild commands are `/guild`, `/guild_create <name>`, `/guild_join <id>`, `/guild_leave`, `/guild_disband`, and `/guild_donate <coins>`.

`/raid` shows the shared boss. `/raid_attack` costs energy and has a 60-minute cooldown. `/guild_raid` opens the per-guild boss: every member can attack, and the contribution board ranks damage. `/team` shows a six-slot team; `/team <player_id> <slot>` assigns a registered hunter. `/duel <player_id>` and `/duel_accept <challenge_id>` run a server-authoritative PvP challenge, while `/arena` shows ratings, wins, and losses. `/tournament` shows the weekly arena and `/tournament_slots` shows the ten available slots; join with `/tournament_join` and claim a past prize with `/tournament_claim`. Tournament score counts dungeon clears and effective raid damage after entry. The item market uses `/market [page]`, `/market_list <item_id> <quantity> <total_price>`, `/market_buy <listing_id>`, and `/market_cancel <listing_id>`. Market pages are numbered from 0, listings escrow items for up to seven days, and sales charge the seller a 5% fee.

Admins are configured with `ADMIN_IDS`. Their commands are `/admin_stats`, `/givecoins <player_id> <amount>`, `/ban <player_id> [reason]`, and `/unban <player_id>`.

## Backups and development

Create a consistent, integrity-checked SQLite backup with:

```sh
python -m monarchbot.backup
python -m monarchbot.backup --destination backups/manual-copy.sqlite3
```

The tool refuses to overwrite an existing file. Backups contain player data and should remain private. Review [PRIVACY.md](PRIVACY.md) before inviting players; operators are responsible for access control and retention.

Install development tools and run the checks with:

```sh
python -m pip install -r requirements-dev.txt
python -m compileall -q monarchbot tests
python -m pytest -q
python -m ruff check monarchbot tests
python -m ruff format --check monarchbot tests
```

GitHub Actions runs these checks on Python 3.11 and 3.12. Tests use temporary databases and never contact Telegram. A passing test suite cannot guarantee that an external bot token, hosting provider, or Android background policy is configured correctly.

## Architecture

`monarchbot/handlers.py` is the Telegram interface and validates callback ownership through the game service. `service.py` contains game rules and transactional actions. `database.py` defines the SQLite schema and transaction boundaries. `marketplace.py`, `tournament.py`, and `social.py` contain independent social/economy services for escrow, weekly scores, duels, ratings, ten-slot tournaments, guild raids, and teams. `backup.py` creates online-consistent database copies. Player identity comes from Telegram updates; button data is never treated as authority without checking the persisted battle owner and turn. `/codex` also has inline keyboard navigation for classes, dungeons, items, and companions.

The current implementation is deliberately an original genre-inspired game. Add only assets and story text you have permission to use. The next strong gameplay milestone is adding story chapters, deeper skill trees, equipment progression, guild unlocks, and a balance pass while keeping combat state server-authoritative.
