# Privacy notes for operators

Monarch is self-hosted. The operator controls the Telegram bot token, database, logs, backups, and retention policy.

The bot stores Telegram user IDs (needed to bind a game profile to its owner), Telegram usernames when available, display names, game progress, economy/inventory, companion/guild/market/tournament data, raid contributions, daily activity dates, and any moderation reason entered by an administrator. It does not intentionally store message history, phone numbers, contact lists, or Telegram passwords. Telegram itself processes bot interactions under its own policies.

Restrict access to the server and SQLite files. Backups contain the same player information and should be encrypted or stored in a private location with limited access. Do not publish database files or logs. Configure administrators narrowly, explain the game's data practices to your players, and delete database/backups when no longer needed. A public software repository does not make the operator's game database public.
