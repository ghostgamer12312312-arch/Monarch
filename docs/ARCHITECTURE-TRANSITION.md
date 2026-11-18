# Monarch architecture transition

Monarch has no top-level legacy Python modules. The canonical runtime is the `monarchbot/` package, and `start.sh` now launches it with `python -m monarchbot` through the package-level `__main__.py` entrypoint.

The package responsibilities are intentionally separated. `main.py` owns process startup and the maintenance thread. `handlers.py` owns Telegram parsing and presentation. `service.py` owns player rules and transactional progression. `marketplace.py`, `social.py`, and `tournament.py` are active domain services, not legacy root scripts. `database.py` owns SQLite migrations and transaction boundaries. Tests import these package services directly.

The transition audit found no safe deletion candidate outside the package. Removing `marketplace.py` or `tournament.py` would break active imports and tests, so those modules remain as explicit domain boundaries. Generated coverage HTML and local coverage state are ignored and are never part of the runtime or deployment artifact.

## Verification

Run the package entrypoint check without a token by compiling the package:

```sh
PYTHONPATH=. .venv/bin/python -m compileall -q monarchbot
```

Run the full suite, including local Telegram handler integration tests:

```sh
PYTHONPATH=. .venv/bin/pytest -q
```

The local integration tests use fake Telegram updates and an `AsyncMock` bot. They verify command publication, profile description publication, `/about`, and maintenance gating without contacting Telegram. A real test bot is still required for network delivery, permissions, and chat-member behavior.
