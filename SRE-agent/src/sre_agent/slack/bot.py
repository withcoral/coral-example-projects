"""Slack entry point for the SRE agent.

Thin layer: build the Bolt app, dispatch Slack events into the shared
investigation pipeline, run the health-check HTTP server. Most of the
substantive work lives in:

    sre_agent.core.agent           — pydantic-ai agent + system prompt
    sre_agent.slack.format         — Block Kit + mrkdwn helpers
    sre_agent.slack.streaming      — the live-plan + final-reply pipeline
    sre_agent.slack.thread_history — Slack thread -> message_history
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from dotenv import load_dotenv
from pydantic_ai.messages import ModelMessage
from slack_bolt import App, Assistant
from slack_bolt.adapter.socket_mode import SocketModeHandler

from sre_agent.core.agent import PydanticSreAgent
from sre_agent.slack.format import clean_slack_text, extract_alert_text
from sre_agent.slack.streaming import run_streamed_investigation
from sre_agent.slack.thread_history import event_context, fetch_thread_history

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)


SUGGESTED_PROMPTS = [
    {"title": "What can you see?",
     "message": "What SRE data sources can you see through Coral, and what's queryable in each?"},
    {"title": "Active Datadog alerts",
     "message": "What Datadog monitors are currently firing or have fired in the last hour?"},
    {"title": "Recent Sentry issues",
     "message": "List Sentry issues from the last 24h with more than 100 events."},
    {"title": "Today's GitHub deploys",
     "message": "What GitHub PRs were merged today and which services do they touch?"},
]


# ============================================================================
# FORK ME: deployment-specific context.
# ----------------------------------------------------------------------------
# This block is injected verbatim into the alert-investigation prompt. It
# tells the agent which Datadog metric/monitor represents your service,
# which Sentry org+project it reports to, which GitHub repo+path holds the
# source, and the URL templates it should cite in the final assessment.
#
# The values below describe THIS demo (`hello-service`, the Coral Sentry
# org `coral-sm`, the GitHub repo `withcoral/coral-example-projects`).
# When you fork this project, rewrite the whole string to describe YOUR
# service stack. Without that edit, the agent will be told to look at
# Coral's demo Sentry and GitHub paths — which it can't see, and which
# will produce nonsense investigations.
# ============================================================================
INVESTIGATION_CONTEXT = """\
hello-service is a Python FastAPI demo app deployed in the coral-demos Kubernetes namespace.

Data sources for this service:
- Datadog metric: hello_service.errors (count type), tagged service:hello-service. Monitor IDs live in datadog.monitors.
- Datadog Logs: hello-service ships structured per-request logs to Datadog via the HTTP intake. Each request produces an entry with method/path/status/duration_ms; errors additionally carry error.kind, error.message, error.stack, and query_params. Not currently queryable via Coral (no datadog.logs table) -- reference the Datadog Logs UI by URL when you want a human to inspect raw requests.
- Sentry: org slug coral-sm, project slug python-fastapi. sentry.issues holds aggregated exceptions; sentry.events / sentry.project_events have full stack traces. Sentry remains the canonical source for individual stack-trace investigation.
- Source code: GitHub repository withcoral/coral-example-projects. The hello-service app source lives at SRE-agent/demo-app/main.py. github.commits and github.contents accept a `ref` filter (branch name or commit SHA).
  - Heads-up on branches: production-deployed code does not always live on the repo's default branch. If github.contents returns 404 (or empty) for a path you have strong evidence exists (from a Sentry stack trace), the default branch is probably stale and the deploy is running off a development branch. List the repo's branches via github.branches and retry with `ref = '<that-branch>'`.

URL templates for the Sources section (and inline links):
- Datadog monitor: https://app.datadoghq.eu/monitors/{MONITOR_ID}
- Datadog Logs:    https://app.datadoghq.eu/logs?query=service%3Ahello-service  (append &from_ts=... &to_ts=... for a time window; include this when the operator might want to grep raw requests around the incident)
- Sentry issue:    https://coral-sm.sentry.io/issues/{ISSUE_ID}/
- GitHub file:     https://github.com/withcoral/coral-example-projects/blob/main/{PATH}
- GitHub commit:   https://github.com/withcoral/coral-example-projects/commit/{SHA}
"""


def build_app() -> App:
    load_dotenv()
    token = os.environ["SLACK_BOT_TOKEN"]
    app = App(token=token)
    assistant = Assistant()

    # Cache the bot's identity at startup. user_id lets the thread-history
    # helper distinguish bot-authored messages from human ones. team_id is
    # required by chat.startStream (the AI-agent streaming endpoint rejects
    # calls without it: `missing_recipient_team_id`).
    bot_user_id: str | None = None
    bot_team_id: str | None = None
    try:
        auth = app.client.auth_test()
        bot_user_id = auth["user_id"]
        bot_team_id = auth.get("team_id")
        logger.info("bot user_id=%s team_id=%s", bot_user_id, bot_team_id)
    except Exception:
        logger.exception("auth.test failed; streaming + thread history will degrade")

    @assistant.thread_started
    def thread_started(say, set_suggested_prompts, logger):  # type: ignore[no-untyped-def]
        say("Hi — I'm your SRE assistant. I query Coral and stay read-only.")
        set_suggested_prompts(prompts=SUGGESTED_PROMPTS)

    @assistant.user_message
    def user_message(payload, set_status, say, client, context, logger):  # type: ignore[no-untyped-def]
        # DMs use the same streaming pipeline as @-mentions + alerts.
        # Slack's Assistant `set_status` typing indicator is redundant once
        # the plan block is streaming, so we don't call it.
        prompt = clean_slack_text(payload.get("text", ""))
        assistant_thread = payload.get("assistant_thread") or {}
        channel = (
            payload.get("channel")
            or assistant_thread.get("channel_id")
            or context.get("channel_id")
        )
        thread_ts = (
            payload.get("thread_ts")
            or assistant_thread.get("thread_ts")
            or payload.get("ts")
        )
        if not channel or not thread_ts:
            logger.warning("DM payload missing channel/thread_ts: %s", payload)
            # Defensive fallback: simple say() flow if we can't open a stream.
            try:
                answer = asyncio.run(
                    PydanticSreAgent().answer(prompt, slack_context=event_context(payload))
                )
            except Exception:
                logger.exception("SRE agent failed")
                answer = "I hit an error while querying Coral. Check the bot logs for details."
            say(answer)
            return

        # Fetch prior turns from the assistant thread so DM follow-ups have
        # context (mirrors the @-mention follow-up flow).
        message_history: list[ModelMessage] = []
        if payload.get("ts") and thread_ts and payload.get("ts") != thread_ts:
            message_history = fetch_thread_history(
                client, channel, thread_ts, bot_user_id,
                exclude_ts=payload.get("ts"),
            )
            logger.info(
                "DM follow-up: loaded %d prior turns for thread %s",
                len(message_history), thread_ts,
            )

        run_streamed_investigation(
            user_input=prompt,
            prompt=prompt,
            channel=channel,
            parent_ts=thread_ts,
            client=client,
            say=say,
            event=payload,
            team_id=bot_team_id,
            message_history=message_history,
        )

    app.use(assistant)

    @app.event("app_mention")
    def handle_app_mention(event, say, client, logger):  # type: ignore[no-untyped-def]
        prompt = clean_slack_text(event.get("text", ""))
        thread_ts = event.get("thread_ts") or event.get("ts")
        is_followup = event.get("thread_ts") is not None

        # When the mention is in an existing thread, fetch the prior turns
        # so the agent has the original alert + assessment as context. For
        # a fresh mention (thread_ts == ts), history is empty.
        message_history: list[ModelMessage] = []
        if is_followup:
            message_history = fetch_thread_history(
                client,
                event["channel"],
                thread_ts,
                bot_user_id,
                exclude_ts=event.get("ts"),
            )
            logger.info("loaded %d prior turns for thread %s", len(message_history), thread_ts)

        run_streamed_investigation(
            user_input=prompt,
            prompt=prompt,
            channel=event["channel"],
            parent_ts=thread_ts,
            client=client,
            say=say,
            event=event,
            team_id=bot_team_id,
            message_history=message_history,
        )

    alerts_channel_id = os.getenv("ALERTS_CHANNEL_ID")
    datadog_app_id = os.getenv("DATADOG_SLACK_APP_ID")
    handled_alert_ts: set[str] = set()

    @app.event("message")
    def handle_alert_message(event, say, client, logger):  # type: ignore[no-untyped-def]
        # Only fires on Datadog-app messages in #alerts. Human thread
        # replies need an @-mention to trigger the bot.
        if not alerts_channel_id or not datadog_app_id:
            return
        if event.get("subtype") or event.get("channel") != alerts_channel_id:
            return
        if datadog_app_id not in (event.get("app_id"), event.get("bot_id")):
            return
        ts = event.get("ts")
        if not ts or ts in handled_alert_ts:
            return
        handled_alert_ts.add(ts)

        alert_text = extract_alert_text(event)
        prompt = "\n\n".join([
            "A Datadog alert just fired. Produce the full structured incident assessment "
            "defined in your instructions (Summary / Evidence / Likely cause / Blast radius / "
            "What changed / Mitigation). Ground the Likely cause section in the actual source "
            "code -- if a Sentry stack trace points at a file:line, look that file up in GitHub "
            "via Coral and quote the offending line.",
            "Deployment-specific context (service-to-source mapping):\n" + INVESTIGATION_CONTEXT,
            f"Alert:\n{alert_text or '(empty alert body)'}",
        ])
        run_streamed_investigation(
            user_input=alert_text,
            prompt=prompt,
            channel=event["channel"],
            parent_ts=ts,
            client=client,
            say=say,
            event=event,
            team_id=bot_team_id,
        )

    return app


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/healthz":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args: Any) -> None:
        pass  # Silence per-request logging.


def start_health_server() -> HTTPServer:
    port = int(os.getenv("HEALTH_PORT", "8080"))
    server = HTTPServer(("0.0.0.0", port), _HealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    logger.info("Health server listening on :%d/healthz", port)
    return server


def main() -> None:
    app = build_app()
    app_token = os.environ["SLACK_APP_TOKEN"]
    start_health_server()
    SocketModeHandler(app, app_token).start()


if __name__ == "__main__":
    main()
