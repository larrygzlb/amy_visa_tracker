#!/bin/sh
# Run one visa check (VFS + Infovisa), save it to the app's history and send the WxPusher notifications.
# Usage: ./check_once.sh
set -e
cd "$(dirname "$0")"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is not installed: https://docs.astral.sh/uv/getting-started/installation/" >&2
  exit 1
fi

# Downloads Chromium the first time; does nothing when it is already installed
uv run --quiet --with playwright python -m playwright install chromium

uv run --quiet app.py --once
