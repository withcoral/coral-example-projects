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

if ! command -v "$CORAL_BIN" >/dev/null 2>&1; then
  echo "Could not find Coral binary: $CORAL_BIN"
  exit 1
fi

if "$CORAL_BIN" connector --help >/dev/null 2>&1; then
  ADD_KIND="connector"
else
  ADD_KIND="source"
fi

add_provider() {
  local provider="$1"

  if [ "$ADD_KIND" = "connector" ]; then
    if "$CORAL_BIN" connectors discover 2>/dev/null | grep -E "^${provider}[[:space:]]" | grep -q installed; then
      echo "$provider connector already installed"
    else
      "$CORAL_BIN" connector add "$provider"
    fi
  else
    if "$CORAL_BIN" source list 2>/dev/null | grep -E "^${provider}[[:space:]]" >/dev/null; then
      echo "$provider source already installed"
    else
      "$CORAL_BIN" source add "$provider"
    fi
  fi
}

for provider in datadog slack github sentry; do
  add_provider "$provider"
done

echo "Installed Coral sources:"
if [ "$ADD_KIND" = "connector" ]; then
  "$CORAL_BIN" connectors discover
else
  "$CORAL_BIN" source list
fi

echo "Running metadata smoke query..."
"$CORAL_BIN" sql "SELECT schema_name, table_name FROM coral.tables WHERE schema_name IN ('datadog', 'slack', 'github', 'sentry') ORDER BY schema_name, table_name LIMIT 25"

echo "Coral source configuration complete."

