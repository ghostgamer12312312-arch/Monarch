#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv
  . .venv/bin/activate
  python -m pip install -r requirements.txt
else
  . .venv/bin/activate
fi
if [ ! -f .env ]; then
  echo "Missing .env. Copy .env.example to .env and add your new Telegram token privately." >&2
  exit 1
fi
exec python -m monarchbot.main
