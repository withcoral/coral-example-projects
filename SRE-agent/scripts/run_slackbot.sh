#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [ -f .env ]; then
  set -a
  . ./.env
  set +a
fi

export SLACK_TOKEN="${SLACK_TOKEN:-${SLACK_BOT_TOKEN:-}}"

if command -v uv >/dev/null 2>&1; then
  uv run coral-sre-slackbot
else
  . .venv/bin/activate
  coral-sre-slackbot
fi

