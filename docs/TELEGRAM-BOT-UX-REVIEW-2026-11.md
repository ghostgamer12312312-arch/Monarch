# Telegram bot UX review — 2026-11-20

The four requested public bot profiles were checked through their public Telegram landing pages. Public pages expose identity, profile text, community links, and a Start Bot entry point, but they do not expose the post-start replies, private command flows, or live inline keyboards without a Telegram client session.

## Observed patterns

`@DemonSlayerXbot` presents a short branded identity and a direct Start Bot entry point. `@Veilofworldsbot` combines the bot identity with visible group and channel links. `@Naruto_X_Boruto_Bot` makes its fan-made status and update channel explicit. `@AnimeLegendsBot` communicates the core loop in three short ideas: collect characters, build a team, and battle others, with separate community and update links.

These patterns support a clear public identity, a short gameplay promise, transparent community links when the owner provides them, and a first action that is immediately understandable. They do not justify copying source code, artwork, anime names, copyrighted assets, or private bot behavior.

## Monarch changes from this review

Monarch already had a branded profile, `/about`, a public command menu, a button hub, persistent battles, companions, teams, guilds, raids, PvP, tournaments, and a market. The concrete UI weakness was that several hub sections printed slash-command instructions instead of keeping the player inside the inline flow.

The hub now exposes real inline destinations for Profile, Dungeons, Daily Reward, Inventory, Guild, World Boss, Arena, Market, Codex, Echo Companions, About, and Help. Dungeons now show direct buttons for Crimson Gate, Forgotten Rift, and Abyssal Citadel, plus a Back to Hub action. Direct dungeon buttons call the same server-side `start_battle` service used by commands, so energy, level gates, cooldowns, and persistence remain centralized.

The battle callback retains stale-turn protection, transaction-first behavior, callback acknowledgement, and `/battle_status` fallback if Telegram cannot refresh the message. The menu callback catches game errors and returns the player to a usable hub instead of silently failing.

## Remaining live verification

A local build and the complete automated suite pass, but the public landing pages cannot verify live post-start UI. A real test-bot session should still check callback rendering, Telegram message edit permissions, command scopes, group privacy behavior, and rate limits. Community and update links can be added later when the owner supplies them.

## References

- [Demon Slayer public Telegram profile](https://t.me/DemonSlayerXbot) "Demon Slayer Telegram bot landing page"
- [Veil of Worlds public Telegram profile](https://t.me/Veilofworldsbot) "Veil of Worlds Telegram bot landing page"
- [Naruto X Boruto public Telegram profile](https://t.me/Naruto_X_Boruto_Bot) "Naruto X Boruto Telegram bot landing page"
- [Anime Legends public Telegram profile](https://t.me/AnimeLegendsBot) "Anime Legends Telegram bot landing page"
