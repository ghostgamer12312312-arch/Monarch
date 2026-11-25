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

Use `/start` to create the player profile; new hunters receive a short welcome card and a starter kit, returning hunters receive a compact welcome-back card. `/menu` opens the button-based game hub, which is the primary interface. `/class` lists awakening paths; `/class vanguard`, `/class assassin`, or `/class arcanist` chooses one once. `/profile` shows stats, bank balance, and collection progress. `/daily` claims the daily reward and shows claim/streak buttons. `/quests` lists objectives with inline claim buttons. `/codex` lists classes, dungeons, supplies, and companions.

`/skills` opens the skill codex and shop. Every hunter starts with **Quick Strike**; further skills are bought with coins and used from the **🌀 Skills** picker inside battle. `/gacha` summons Solo Leveling characters across ranks **E, F, C, B, D, A, S, SS, SSS, SR, SP** with 1×/10× pulls, a 50-pull S+ pity, drop rates, and a collection screen. Collected characters grant permanent HP and ATK. `/crown` shows the crown collection and world-boss gallery. `/bank` stores coins in a safe vault with deposit/withdraw presets. `/raid` shows the rotating world boss with picture, attack button, and damage ranking.

`/battle gate`, `/battle rift`, or `/battle abyss` starts a persistent encounter. Rift requires level 3 and Abyss requires level 8. During combat, tap **⚔️ Strike**, **🌀 Skills** (opens your owned-skills picker), **💨 Dodge**, **🧪 Tonic**, **🔷 Mana Vial**, or **🏃 Run**. A battle expires after 30 minutes; energy is spent on entry and is not refunded for defeat or retreat. `/hunt` previews a wild enemy with a picture and Fight/Run buttons.

`/shop` has inline **Buy 1 / Buy 5** buttons; `/inventory` has use and shop buttons. Healing Tonics and Mana Vials are used from battle buttons. `/market` lists player listings with **Buy** buttons per listing. `/companions` lists your echo allies, `/extract` spends 25 essence to summon one, and `/equip <companion_id|none>` sets its passive bonuses outside battle. `/leaderboard` displays top hunters with a 👑 crown for first place. `/guild` is a full button hub with members, treasury, donation, raid, and leader-only treasury upgrades. Administrators have a hidden `/admin` console (owner tools, maintenance toggle, audit) that is not published in the public command list.

`/raid` shows the shared boss. `/raid_attack` costs energy and has a 60-minute cooldown. `/guild_raid` opens the per-guild boss: every member can attack, team members add to raid power, and the contribution board ranks damage. Guild leaders can use `/guild_promote <id>`, `/guild_demote <id>`, `/guild_kick <id>`, and `/guild_rename <name>`; the bot validates roles transactionally. Guild raids reset once per UTC week from the in-process maintenance loop, with an idempotent reset marker. `/team` shows a six-slot team; `/team <player_id> <slot>` assigns a registered hunter and `/team_power` reports combined attack. `/duel <player_id>` and `/duel_accept <challenge_id>` run a server-authoritative PvP challenge, while `/arena` shows ratings, wins, and losses. `/tournament` shows the weekly arena and `/tournament_slots` shows the ten available slots; join with `/tournament_join`, schedule with `/tournament_start`, check in with `/tournament_checkin <match_id>`, view matches with `/tournament_matches`, and claim a past prize with `/tournament_claim`. Matches receive random start times within 24 hours, stay open for two days, award a present player against a no-show, and are resolved by the maintenance loop. The item market uses `/market [page]`, `/market_list <item_id> <quantity> <total_price>`, `/market_buy <listing_id>`, and `/market_cancel <listing_id>`. Market pages are numbered from 0, listings escrow items for up to seven days, and sales charge the seller a 5% fee.

Admins are configured with `ADMIN_IDS`. Their commands are intentionally omitted from `/help`, the public Telegram command menu, and player buttons. The hidden operator commands are `/admin_stats`, `/givecoins <player_id> <amount>`, `/ban <player_id> [minutes] [reason]`, `/unban <player_id>`, `/admin_add <player_id> [admin|moderator]`, `/admin_remove <player_id>`, `/admin_audit`, `/maintenance <on|off|status>`, `/admin_reset_raids`, and `/admin_resolve_matches`. Every command still checks configured or database-backed operator authorization; hiding a command is not treated as security. Configured IDs are immutable top-level operators, while database-backed roles are audited and can be revoked. Timed bans expire automatically, and maintenance mode blocks ordinary gameplay while keeping operator access available.

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

`monarchbot/handlers.py` is the Telegram interface and validates callback ownership through the game service. `service.py` contains game rules and transactional actions. `database.py` defines the SQLite schema and transaction boundaries. `marketplace.py`, `tournament.py`, and `social.py` contain independent social/economy services for escrow, weekly scores, duels, ratings, ten-slot tournaments, guild raids, and teams. The `economy_ledger` records bounded rewards, gifts, admin grants, and market sales, while `/economy_history` exposes a limited player audit view. `backup.py` creates online-consistent database copies. Player identity comes from Telegram updates; button data is never treated as authority without checking the persisted battle owner and turn. `/codex` also has inline keyboard navigation for classes, dungeons, items, and companions.

For local moderator testing and deployment, see [`docs/MODERATOR-TESTING-AND-DEPLOYMENT.md`](docs/MODERATOR-TESTING-AND-DEPLOYMENT.md). It covers disposable databases, role setup, timed bans, maintenance mode, economy history, Termux deployment, and coverage commands.

The package transition and local Telegram integration-test boundary are documented in [`docs/ARCHITECTURE-TRANSITION.md`](docs/ARCHITECTURE-TRANSITION.md).

The public Telegram UX comparison and resulting inline-navigation changes are documented in [`docs/TELEGRAM-BOT-UX-REVIEW-2026-11.md`](docs/TELEGRAM-BOT-UX-REVIEW-2026-11.md).

## Bot profile

The Telegram bot name is **Monarch: Rift Hunters**. Its short description is: “Turn-based hunter RPG with dungeons, guilds, PvP, raids, tournaments, and a fair player market.” The full About description is published automatically during application startup and is also available through `/about`. The latest Ultimate review and next-change priorities are recorded in [`docs/ULTIMATE-REVIEW-2026-11-19.md`](docs/ULTIMATE-REVIEW-2026-11-19.md).

The current implementation is deliberately an original genre-inspired game. Add only assets and story text you have permission to use. The next strong gameplay milestone is adding story chapters, deeper skill trees, equipment progression, guild unlocks, and a balance pass while keeping combat state server-authoritative.
