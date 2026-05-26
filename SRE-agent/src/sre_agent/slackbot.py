"""Slack entry point for the SRE agent.

Thin layer: build the Bolt app, dispatch Slack events into the shared
investigation pipeline, run the health-check HTTP server. Most of the
substantive work lives in:

    sre_agent.agent                — pydantic-ai agent + system prompt
    sre_agent.slack_format         — Block Kit + mrkdwn helpers
    sre_agent.slack_streaming      — the live-plan + final-reply pipeline
    sre_agent.slack_thread_history — Slack thread -> message_history
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

from sre_agent.agent import PydanticSreAgent
from sre_agent.slack_format import clean_slack_text, extract_alert_text
from sre_agent.slack_streaming import run_streamed_investigation
from sre_agent.slack_thread_history import event_context, fetch_thread_history

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


# Optional deployment-specific context (service-to-source mapping) injected
# into the alert investigation prompt. Without an override the agent will
# introspect Coral's catalog at runtime to discover what data is available
# -- which works, but burns extra tool calls. Anyone deploying this template
# should set SRE_INVESTIGATION_CONTEXT to their own mapping (see README).
INVESTIGATION_CONTEXT_DEFAULT = """\
(No deployment-specific service-to-source mapping configured. The agent will
introspect Coral's catalog at runtime to discover what data is available.)

If you want to ground investigations faster, set the SRE_INVESTIGATION_CONTEXT
environment variable to a short paragraph (or a few bullets) describing:
- The services this bot covers, and how they're tagged in Datadog
- Which Sentry org/project each service reports to
- The GitHub repo + path where each service's source lives
- URL patterns to use for inline links / the Sources section
- Any branching / ref quirks (e.g. deploys not running off the default branch)
"""


def _investigation_context() -> str:
    configured = (os.getenv("SRE_INVESTIGATION_CONTEXT") or "").strip()
    return configured or INVESTIGATION_CONTEXT_DEFAULT


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

        run_streamed_investigation(
            user_input=prompt,
            prompt=prompt,
            channel=channel,
            parent_ts=thread_ts,
            client=client,
            say=say,
            event=payload,
            team_id=bot_team_id,
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
            "Deployment-specific context (service-to-source mapping):\n" + _investigation_context(),
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
