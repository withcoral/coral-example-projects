#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [ -f .env ]; then
  set -a
  . ./.env
  set +a
fi

CORAL_BIN="${CORAL_BIN:-coral}"
export SLACK_TOKEN="${SLACK_TOKEN:-${SLACK_BOT_TOKEN:-}}"

exec "$CORAL_BIN" mcp-stdio

