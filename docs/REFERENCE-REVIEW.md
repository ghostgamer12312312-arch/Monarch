# Reference review: PokeRo and Telegram RPG patterns

The prior task asked for a PokeRo review. The exact accessible repository is [`ghostgamer12312312-arch/PokeRo`](https://github.com/ghostgamer12312312-arch/PokeRo); its repository metadata and tree were checked read-only. The closest publicly documented Telegram Pokémon RPG pattern was also reviewed in [`tengattack/telegram-pokemon-bot`](https://github.com/tengattack/telegram-pokemon-bot), whose README documents an explicit group-join approval flow (`/join`) and a custom keyboard (`/keyboard`).

Monarch does not copy that project or its assets. The useful patterns are applied as original code: server-side membership checks, explicit operator-controlled administration, and inline keyboard navigation. Monarch keeps its original hunter-and-rift setting, configured `ADMIN_IDS`, guild roles, transactional state changes, and `/menu`/`/codex` keyboards.

The reference is inspiration only. It is not a dependency, and its code is not included in this repository.

## Top-three repository review

The second pass checked the accessible repositories under `ghostgamer12312312-arch` and selected three relevant references:

1. **[PokeRo](https://github.com/ghostgamer12312312-arch/PokeRo)** — the user’s Pokémon RPG reference. Its group-oriented structure motivated explicit membership and operator controls, but Monarch keeps its own hunter-and-rift setting.
2. **[Ultimate](https://github.com/ghostgamer12312312-arch/Ultimate)** — the larger Dragon Ball bot reference. Its broad command surface motivated adding weekly rewards, player gifting, and more explicit team power feedback.
3. **[telegame](https://github.com/ghostgamer12312312-arch/telegame)** — the clearest setup/reference project. Its documented daily/weekly loop, characters, team view, clan management, and Termux onboarding informed Monarch’s `/weekly`, `/give`, `/team_power`, and setup documentation.

These are read-only design references. No source code, assets, tokens, or database files were copied. The resulting Monarch additions are original: `/weekly`, bounded `/give`, `/team_power`, tournament match scheduling/check-in/no-show resolution, and transactional guild administration.
