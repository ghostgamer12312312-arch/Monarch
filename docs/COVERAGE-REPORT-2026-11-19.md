# Monarch coverage report — 2026-11-19

The full suite was executed from the repository root with branch coverage enabled:

```sh
PYTHONPATH=. .venv/bin/coverage erase
PYTHONPATH=. .venv/bin/coverage run --branch -m pytest -q
.venv/bin/coverage report -m
.venv/bin/coverage html -d coverage-html
```

Result: **40 passed in 2.78 seconds**. The report was generated with Coverage.py 7.x after installing the development dependency declared in `requirements-dev.txt`.

## Coverage summary

| Module | Statements | Missing | Branches | Partial branches | Coverage |
|---|---:|---:|---:|---:|---:|
| `monarchbot/database.py` | 64 | 1 | 12 | 1 | 97% |
| `monarchbot/service.py` | 682 | 144 | 210 | 57 | 74% |
| `monarchbot/marketplace.py` | 87 | 12 | 28 | 12 | 79% |
| `monarchbot/handlers.py` | 695 | 500 | 288 | 2 | 21% |
| `monarchbot/social.py` | 212 | 32 | 50 | 18 | 80% |
| `monarchbot/tournament.py` | 48 | 3 | 14 | 3 | 90% |
| `tests/test_telegram_integration.py` | 48 | 0 | 0 | 0 | 100% |
| **All measured Python files** | **2,419** | **716** | **634** | **102** | **63%** |

The handler percentage is improving because local integration tests now exercise bot metadata publication, `/about`, and maintenance gating. The suite still does not connect to Telegram or construct full `Update` objects for every command. The business rules behind the moderator commands are exercised in the service layer, where SQLite transactions and authorization decisions occur. Real Telegram delivery, chat administrator rights, command scopes, and network rate limits remain external integration work.

## New moderator and administration behavior covered

`tests/test_game.py::test_banned_player_cannot_play_until_unbanned` verifies permanent ban enforcement and unban recovery. `tests/test_game.py::test_timed_ban_expires_and_admin_controls_are_audited` verifies database-backed moderator roles, temporary ban enforcement, expiry after the controlled clock advances, durable maintenance mode, and an audit record. Existing marketplace tests verify that a banned seller's escrow is refunded and a banned listing cannot be purchased.

`tests/test_database.py::test_schema_initializes_idempotently_and_contains_core_tables` verifies the versioned schema and the presence of `admin_roles`, `admin_audit`, `bot_settings`, and `economy_ledger`. The database suite also checks idempotent initialization, unknown future-version rejection, shared in-memory persistence, and active-battle uniqueness.

`tests/test_handlers.py::test_all_commands_and_interactive_callbacks_are_registered` verifies that every command map entry is installed and that public commands include `/about` and `/economy_history`. `tests/test_telegram_integration.py` verifies profile metadata publication, the `/about` response, and maintenance gating with fake Telegram objects. These tests do not claim live Telegram execution.

The social and marketplace tests cover the economy ledger records created by weekly rewards, player gifts, and market sales. The new ledger records are transactional with the balance mutation, and `/economy_history` limits queries to 50 rows even when a larger limit is supplied.

## Full terminal report

```text
Name                        Stmts   Miss Branch BrPart  Cover
-----------------------------------------------------------------------
monarchbot/__init__.py          1      0      0      0   100%
monarchbot/backup.py           52     18     12      6    62%
monarchbot/catalog.py          22      0      0      0   100%
monarchbot/config.py           31      6       6      3    76%
monarchbot/database.py         64      1      12      1    97%
monarchbot/handlers.py        695    500     288      2    21%
monarchbot/marketplace.py      87     12      28     12    79%
monarchbot/service.py         682    144     210     57    74%
monarchbot/social.py          212     32      50     18    80%
monarchbot/tournament.py       48      3      14      3    90%
-----------------------------------------------------------------------
tests/test_telegram_integration.py 48      0       0      0   100%
TOTAL                        2419    716     634    102    63%
```

The generated interactive HTML report is intentionally not committed because it contains machine-generated files. Recreate it with `coverage html -d coverage-html` and open `coverage-html/index.html` locally.
