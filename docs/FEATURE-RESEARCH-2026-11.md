# Monarch feature research — November 2026

The updated public repositories `PokeRo`, `Ultimate`, `Drgo`, and `telegame` were reviewed read-only, alongside the official [Telegram Bot API](https://core.telegram.org/bots/api), [Telegram Bot FAQ](https://core.telegram.org/bots/faq), and [python-telegram-bot documentation](https://docs.python-telegram-bot.org/en/stable/). The goal was to identify compatible behavior, not to copy code, assets, tokens, or franchise content.

## Findings applied to Monarch

PokeRo's strongest reusable pattern is a centralized permission and moderation layer with owner/admin separation, timed sanctions, auditability, and early guards. Its high-impact database reset, code-pull, arbitrary query, and content-generation controls were deliberately not copied. Ultimate and Drgo show the value of modular battle services, persistent progress, economy ledgers, admin dashboards, and explicit clan roles, but their public snapshots also contain stale README claims and schema/handler mismatches. telegame provides a compact, user-facing daily/weekly loop, gifting, team views, clan roles, and a shared raid pattern.

Telegram's official documentation confirms that command menus are discoverability only; every action still needs server-side authorization. Callback payloads are client-supplied and limited to 1–64 bytes, so Monarch should keep using short identifiers and validate ownership/state from SQLite. `answerCallbackQuery` should be called promptly. Current membership and administrator rights should be checked with `getChatMember` when Monarch adds group moderation. Telegram does not provide a general-purpose cron API, so scheduled work belongs in the bot process or a durable worker.

The current implementation adopts the safest compatible slice: database-backed `admin`/`moderator` roles, immutable configured operators, timed bans, durable `admin_audit` records, durable maintenance mode, centralized maintenance enforcement, bounded weekly rewards, bounded coin gifting, and team power reporting. It intentionally avoids unrestricted `/query`, `/dbreset`, `/git_pull`, arbitrary grants, automatic permanent CAPTCHA bans, and external Pokémon/franchise data.

## Sources

- [PokeRo repository](https://github.com/ghostgamer12312312-arch/PokeRo)
- [Ultimate repository](https://github.com/ghostgamer12312312-arch/Ultimate)
- [Drgo repository](https://github.com/ghostgamer12312312-arch/Drgo)
- [telegame repository](https://github.com/ghostgamer12312312-arch/telegame)
- [Telegram Bot API](https://core.telegram.org/bots/api)
- [Telegram Bot FAQ](https://core.telegram.org/bots/faq)
- [python-telegram-bot stable documentation](https://docs.python-telegram-bot.org/en/stable/)
- [python-telegram-bot JobQueue](https://docs.python-telegram-bot.org/en/stable/telegram.ext.jobqueue.html)
