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

[ -n "${DD_API_KEY:-}" ]    && add_source datadog || echo "Skipping datadog: DD_API_KEY not set"
[ -n "${GITHUB_TOKEN:-}" ]  && add_source github  || echo "Skipping github: GITHUB_TOKEN not set"
[ -n "${SENTRY_TOKEN:-}" ]  && add_source sentry  || echo "Skipping sentry: SENTRY_TOKEN not set"
[ -n "${SLACK_TOKEN:-}" ]   && add_source slack   || echo "Skipping slack: SLACK_TOKEN not set"

echo "Coral source configuration complete. Starting bot..."
exec coral-sre-slackbot
