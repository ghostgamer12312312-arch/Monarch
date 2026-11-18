# Moderator command testing and local deployment

This guide explains how to test Monarch's moderator and administrator commands on a local SQLite database before connecting a Telegram bot. It covers database-backed moderator roles, timed bans, audit history, maintenance mode, economy history, and operational recovery. The commands are safe to exercise locally because the test database is disposable and no real bot token is required for the service-level tests.

## Local setup

From a fresh clone, create a virtual environment and install development dependencies:

```sh
git clone https://github.com/ghostgamer12312312-arch/Monarch.git
cd Monarch
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
cp .env.example .env
```

For unit and service tests, leave `BOT_TOKEN` empty; the tests construct `Settings` with a non-production placeholder token. For a real local Telegram run, edit `.env` and set a newly rotated test-bot token and numeric `ADMIN_IDS`, for example `ADMIN_IDS=123456789`. Never commit `.env`, a database file, or a real token.

## Run the automated moderator tests

Always run from the repository root with the package path enabled:

```sh
PYTHONPATH=. .venv/bin/pytest -q
PYTHONPATH=. .venv/bin/python -m compileall -q monarchbot tests
.venv/bin/ruff check monarchbot tests
.venv/bin/ruff format --check monarchbot tests
```

The focused service tests cover a temporary ban and automatic expiry, role grants, maintenance persistence, audit rows, schema tables, weekly reward and gift ledger records, and market-sale ledger records. The handler registration test verifies that every command in the command map is installed and that `/about` and `/economy_history` are published. `tests/test_telegram_integration.py` uses fake Telegram updates and `AsyncMock` methods to verify profile metadata publication, `/about`, and maintenance gating without contacting Telegram.

To run only the moderator and economy tests while iterating:

```sh
PYTHONPATH=. .venv/bin/pytest -q tests/test_game.py -k 'ban or admin or maintenance'
PYTHONPATH=. .venv/bin/pytest -q tests/test_database.py tests/test_marketplace.py tests/test_social.py
```

## Exercise the service locally without Telegram

The following smoke test creates an in-memory database, registers two hunters, grants a moderator role, applies a one-minute ban, verifies the restriction, advances the deterministic clock, and checks the economy ledger. It does not contact Telegram.

```sh
PYTHONPATH=. .venv/bin/python - <<'PY'
from monarchbot.database import Database
from monarchbot.service import GameError, GameService

db = Database(":memory:")
db.initialize()
game = GameService(db)
game.register(1001, "owner", "Local Owner")
game.register(1002, "mod", "Local Moderator")
game.register(1003, "hunter", "Local Hunter")

game.grant_admin_role(1001, 1002, "moderator")
assert game.has_admin_role(1002, frozenset(), "moderator")
game.admin_ban(1003, "local smoke test", 1002, duration_minutes=1)
try:
    game.profile(1003)
except GameError as exc:
    assert "restricted" in str(exc)
else:
    raise AssertionError("the timed ban was not enforced")

game.give_coins(1003, 1002, 1)  # expected to fail while the ban is active
PY
```

The last line is intentionally expected to raise `GameError`; remove it or advance `game.epoch` in a custom script if you want the smoke test to finish successfully. A simpler successful check is available in `tests/test_game.py::test_timed_ban_expires_and_admin_controls_are_audited`.

## Run the bot locally with a disposable database

Set a separate local database path in `.env`:

```dotenv
BOT_TOKEN=your_rotated_test_bot_token
DATABASE_PATH=data/monarch-local.sqlite3
ADMIN_IDS=your_numeric_telegram_user_id
LOG_LEVEL=INFO
```

Start the bot from the repository root:

```sh
. .venv/bin/activate
./start.sh
```

Use a private chat with the test bot first. Send `/start`, then create a second test profile if you have a second Telegram account or use service-level tests for the target-user behavior. Do not test bans or maintenance in a production group. Keep one polling process per bot token; do not start a second process with the same token.

## Moderator command walkthrough

A configured ID in `ADMIN_IDS` can grant a database-backed role after the target has used `/start`:

```text
/admin_add <player_id> moderator
/admin_add <player_id> admin
/admin_remove <player_id>
```

A moderator can inspect and reverse moderation actions. The optional numeric argument after the target is a duration in minutes; zero or an omitted duration creates a permanent ban. The service caps temporary bans at 43,200 minutes (30 days):

```text
/ban <player_id> 60 repeated spam
/unban <player_id>
/admin_audit
```

`/admin_audit` shows the most recent durable audit events. It records role changes, bans, unbans, maintenance changes, and operational actions. A timed ban is removed automatically when the affected player next attempts a game action after its expiry. Existing market escrow is refunded when a seller is banned, and an expired ban no longer blocks a valid seller listing.

Only configured `ADMIN_IDS` can change maintenance mode:

```text
/maintenance status
/maintenance on
/maintenance off
```

While maintenance is enabled, ordinary game commands receive a maintenance response. Operators remain able to inspect or change maintenance mode. The flag is stored in SQLite, so it survives a process restart. Turn it off after migrations or maintenance work.

The economy history command is available to every active player and is bounded to 50 rows:

```text
/economy_history
/economy_history 10
```

It shows weekly reward, gift, administrative grant, and marketplace sale events recorded in the economy ledger. It is an audit view, not a balance mutation command.

## Coverage and interpretation

Generate a terminal report and an HTML report with:

```sh
PYTHONPATH=. .venv/bin/coverage run --branch -m pytest -q
.venv/bin/coverage report -m
.venv/bin/coverage html
```

The report measures Python line and branch execution. It does not prove that Telegram delivered a real update, that the bot had administrator rights in a group, or that network rate limits were handled in production. A controlled test-bot run is still required for Telegram command scopes, real callback behavior, and chat membership changes.

## Deployment checklist

Before deployment, run a SQLite backup, review `git diff`, confirm `.env` is ignored, and verify that `DATABASE_PATH` points to persistent storage. Install the same `requirements-dev.txt` or production requirements in the target environment, run the migration through `Database.initialize()`, and execute the full test suite before starting the bot.

For Termux, use a persistent `tmux` session and optionally a Termux wake lock. Android can stop background processes after battery optimization, force-stop, reboot, or network changes, so a VPS with persistent storage is safer for continuous hosting. After deployment, check `/maintenance status`, `/admin_audit`, `/raid`, and `/tournament_matches` from a controlled operator account. Do not claim live Telegram verification from local tests alone.

## Design references

The second review of [PokeRo](https://github.com/ghostgamer12312312-arch/PokeRo) showed daily earning caps, persistent sessions, atomic increments, and explicit reward bookkeeping. The second review of [Ultimate](https://github.com/ghostgamer12312312-arch/Ultimate) showed a market schema, guarded currency spending, trade records, and durable clan/raid state. Monarch adopts the safe subset: escrowed market listings, bounded transfers, transactional economy ledger rows, and expiry-aware moderation. It does not copy code, franchise data, unrestricted grants, arbitrary SQL, or high-impact deployment controls.
