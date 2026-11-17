# Reference review: PokeRo and Telegram RPG patterns

The prior task asked for a PokeRo review. The exact repository name was ambiguous, so the review checked the accessible GitHub search results and the closest Telegram Pokémon RPG reference, [`tengattack/telegram-pokemon-bot`](https://github.com/tengattack/telegram-pokemon-bot). Its README documents an explicit group-join approval flow (`/join`) and a custom keyboard (`/keyboard`).

Monarch does not copy that project or its assets. The useful patterns are applied as original code: server-side membership checks, explicit operator-controlled administration, and inline keyboard navigation. Monarch keeps its original hunter-and-rift setting, configured `ADMIN_IDS`, guild roles, transactional state changes, and `/menu`/`/codex` keyboards.

The reference is inspiration only. It is not a dependency, and its code is not included in this repository.
