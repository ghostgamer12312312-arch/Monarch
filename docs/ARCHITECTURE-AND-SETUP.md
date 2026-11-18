# Monarch architecture and setup handoff

## Runtime architecture

Telegram updates enter `monarchbot/handlers.py`. Handlers validate command arguments, call services in worker threads, and render text or inline keyboards. `GameService` owns player registration, progression, dungeon combat, quests, inventory, guilds, and the shared raid. `SocialService` owns PvP challenges, arena ratings, ten-slot tournament registration, six-slot teams, and per-guild raid contribution rankings. `MarketplaceService` and `TournamentService` keep escrow and legacy weekly prize logic isolated.

SQLite is the durable store. Every state-changing operation uses one transaction, foreign keys, uniqueness constraints, and bounded values. Callback actions carry the battle ID and expected turn; the service verifies the persisted owner and turn before committing. Tokens and admin IDs are read from `.env`, never source code.

## Gameplay loops

1. `/start` creates a hunter profile, starter supplies, and an Echo companion. `/daily` and `/weekly` provide bounded recurring rewards; `/give` transfers a bounded amount of coins in one transaction.
2. `/menu` opens the inline hub; `/codex` opens inline navigation for classes, dungeons, items, and companions.
3. `/battle gate|rift|abyss` starts a turn-based encounter; actions are committed atomically.
4. `/guild_create`, `/guild_join`, and `/guild_donate` build the social layer. `/guild_raid` records every member's damage and ranks the contribution board.
5. `/duel <player_id>` creates a challenge; the opponent accepts with `/duel_accept <id>`. Ratings update only once on completion.
6. `/tournament_join` allocates one of ten unique weekly slots; `/tournament_slots` displays the roster. `/tournament_start` creates randomized match times within 24 hours, `/tournament_checkin <match_id>` records attendance, and `/tournament_matches` shows the player's schedule. Matches remain open for two days; a present hunter wins against a no-show, and the hourly maintenance loop resolves due matches.
7. `/team <player_id> <slot>` maintains up to six configured team positions, and `/team_power` reports their combined attack. Configured teammates contribute to guild-raid damage.

## Termux deployment

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

Set a newly rotated `BOT_TOKEN` and optional comma-separated `ADMIN_IDS`. Start the process in a named session:

```sh
tmux new-session -s monarch
./start.sh
# detach: Ctrl+B, then D
tmux attach -t monarch
```

Android may stop background processes after battery optimization, force-stop, reboot, or network changes. A matching Termux:API installation can provide `termux-wake-lock`, but a VPS with persistent storage is safer for 24/7 operation. Run one polling process per token.

The bot's maintenance thread checks once per hour. It resolves due tournament matches and calls the idempotent UTC-week guild-raid reset. Admins can invoke `/admin_reset_raids` and `/admin_resolve_matches` for operational recovery.

Configured `ADMIN_IDS` are the immutable top-level operators. They can grant or revoke database-backed `admin` and `moderator` roles with `/admin_add` and `/admin_remove`. `/admin_audit` reads the durable audit trail. `/ban` accepts an optional duration in minutes, and expired bans are removed when the player next acts. `/maintenance on|off|status` stores a durable maintenance flag; ordinary commands are rejected while operators retain access. These controls were selected from the repository comparison because they are safer and more maintainable than unrestricted database reset, code-pull, or arbitrary query commands.

## Validation

From the repository root:

```sh
PYTHONPATH=. python -m pytest -q
PYTHONPATH=. python -m compileall -q monarchbot tests
python -m ruff check monarchbot tests
python -m ruff format --check monarchbot tests
```
