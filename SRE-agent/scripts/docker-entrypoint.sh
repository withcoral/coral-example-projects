#!/usr/bin/env sh
set -eu

# Coral Slack connector reads SLACK_TOKEN; fall back to SLACK_BOT_TOKEN.
SLACK_TOKEN="${SLACK_TOKEN:-${SLACK_BOT_TOKEN:-}}"
export SLACK_TOKEN

# Register a Coral source only when its credentials are present in the
# environment. `coral source add` reads the credentials from env vars.
add_source() {
  provider="$1"
  if coral source add "$provider"; then
    echo "Registered Coral source: $provider"
  else
    echo "Coral source '$provider' already installed or unavailable; continuing."
  fi
}

if [ -n "${DD_API_KEY:-}" ] && [ -n "${DD_APPLICATION_KEY:-}" ]; then
  add_source datadog
else
  echo "Skipping datadog: DD_API_KEY and DD_APPLICATION_KEY both required"
fi

[ -n "${GITHUB_TOKEN:-}" ] && add_source github || echo "Skipping github: GITHUB_TOKEN not set"

if [ -n "${SENTRY_TOKEN:-}" ] && [ -n "${SENTRY_ORG:-}" ]; then
  add_source sentry
else
  echo "Skipping sentry: SENTRY_TOKEN and SENTRY_ORG both required"
fi

[ -n "${SLACK_TOKEN:-}" ] && add_source slack || echo "Skipping slack: SLACK_TOKEN not set"

echo "Coral source configuration complete. Starting bot..."
exec coral-sre-slackbot
