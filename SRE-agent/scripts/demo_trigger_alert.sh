#!/usr/bin/env bash
# Trigger the hello-service bug enough times to fire its Datadog monitor.
#
# Usage:
#   scripts/demo_trigger_alert.sh                # 30 requests via port-forward
#   COUNT=100 scripts/demo_trigger_alert.sh      # custom request count
#   APP_URL=http://localhost:8000 scripts/demo_trigger_alert.sh  # skip port-forward
#
# The script hits /greet?name=<unknown> in a loop. Each request raises
# AttributeError in the handler -> Sentry captures it, app pushes a Datadog
# counter, monitor fires, Slack posts to #alerts, SRE agent investigates.

set -euo pipefail

NAMESPACE="${NAMESPACE:-coral-demos}"
SERVICE="${SERVICE:-hello-service}"
COUNT="${COUNT:-30}"
LOCAL_PORT="${LOCAL_PORT:-18000}"
APP_URL="${APP_URL:-}"

PF_PID=""
cleanup() {
  if [ -n "$PF_PID" ] && kill -0 "$PF_PID" 2>/dev/null; then
    kill "$PF_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT

if [ -z "$APP_URL" ]; then
  if lsof -iTCP:"$LOCAL_PORT" -sTCP:LISTEN -t >/dev/null 2>&1; then
    echo "ERROR: local port $LOCAL_PORT is already in use." >&2
    echo "       Set LOCAL_PORT=<free port> or stop the listener and retry." >&2
    exit 1
  fi
  echo "Starting kubectl port-forward to $SERVICE in $NAMESPACE on :$LOCAL_PORT..."
  kubectl -n "$NAMESPACE" port-forward "svc/$SERVICE" "$LOCAL_PORT:80" >/dev/null 2>&1 &
  PF_PID=$!
  ready=false
  for _ in 1 2 3 4 5 6 7 8; do
    sleep 1
    if curl -sf "http://localhost:$LOCAL_PORT/healthz" >/dev/null 2>&1; then
      ready=true
      break
    fi
  done
  if [ "$ready" != true ]; then
    echo "ERROR: port-forward never became reachable on :$LOCAL_PORT." >&2
    echo "       Check 'kubectl -n $NAMESPACE get pods -l app.kubernetes.io/name=$SERVICE'." >&2
    exit 1
  fi
  APP_URL="http://localhost:$LOCAL_PORT"
fi

echo "Firing $COUNT bad-greet requests at $APP_URL/greet ..."
errors=0
for i in $(seq 1 "$COUNT"); do
  status=$(curl -s -o /dev/null -w "%{http_code}" "$APP_URL/greet?name=user${i}")
  if [ "$status" = "500" ]; then
    errors=$((errors + 1))
  fi
done

echo "Done. $errors / $COUNT requests returned 500 (expected ~all)."
echo "Watch #alerts in Slack — the Datadog monitor should fire shortly,"
echo "then the SRE agent will reply in-thread with an investigation."
