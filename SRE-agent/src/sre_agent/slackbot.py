from __future__ import annotations

import asyncio
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from dotenv import load_dotenv
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)
from slack_bolt import App, Assistant
from slack_bolt.adapter.socket_mode import SocketModeHandler

from sre_agent.agent import PydanticSreAgent

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


# Hardcoded service-to-source mapping injected into the alert investigation
# prompt. The agent has no way to know that an alert tagged
# `service:hello-service` should be cross-referenced against the
# `python-fastapi` Sentry project + the `withcoral/coral-example-projects`
# GitHub repo without being told. In a production setup this would come from
# a service catalog or config; for this demo, a constant is enough.
INVESTIGATION_CONTEXT = """\
hello-service is a Python FastAPI demo app deployed in the coral-demos Kubernetes namespace.

Data sources for this service:
- Datadog: metric hello_service.errors (count type), tagged service:hello-service. Monitor IDs live in datadog.monitors; current state and query are inspectable there.
- Sentry: org slug coral-sm, project slug python-fastapi. Use sentry.issues filtered to project python-fastapi for recent exceptions, counts, first/last seen, and short IDs. Use sentry.project_events or sentry.events to get stack traces.
- Source code: GitHub repository withcoral/coral-example-projects (URL https://github.com/withcoral/coral-example-projects). The hello-service app lives in SRE-agent/demo-app/main.py. Use github.commits filtered by repo and path to find recent changes. Use github.contents (or equivalent table for fetching file content) to read main.py so you can quote the actual offending line of code.

When investigating alerts for this service:
1. Identify the dominant Sentry issue (title + count + short ID).
2. Pull the stack trace and identify file + line number from sentry.events.
3. Fetch the file from GitHub at that path and quote the buggy lines verbatim.
4. Check github.commits for recent changes touching SRE-agent/demo-app/ that align with the onset timestamp.
5. Distinguish immediate mitigation (e.g. add a null guard) from durable fixes (release tagging, input validation, tests).
"""


def _clean_slack_text(text: str) -> str:
    return " ".join(text.split()).strip()


def _extract_alert_text(event: dict[str, Any]) -> str:
    """Datadog posts the alert body via Slack attachments, not the top-level
    `text`. Pull from both so the agent actually sees what fired."""
    parts: list[str] = []
    top = event.get("text")
    if top:
        parts.append(top)
    for att in event.get("attachments") or []:
        title = att.get("title")
        if title:
            parts.append(title)
        body = att.get("text") or att.get("fallback")
        if body:
            parts.append(body)
    return _clean_slack_text("\n".join(parts))


def _event_context(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "channel": event.get("channel"),
        "thread_ts": event.get("thread_ts") or event.get("ts"),
        "user": event.get("user"),
        "ts": event.get("ts"),
        "event_type": event.get("type"),
    }


# Short bot acks we strip from message_history -- they aren't substantive
# turns and including them as ModelResponse messages just confuses the agent.
_ACK_SUBSTRINGS = (
    "Investigating with Coral",
    "Investigating this alert with Coral",
)


def _is_ack_message(text: str) -> bool:
    return any(s in text for s in _ACK_SUBSTRINGS)


def _fetch_thread_history(
    client: Any,
    channel: str,
    thread_ts: str,
    bot_user_id: str | None,
    *,
    exclude_ts: str | None = None,
    limit: int = 50,
) -> list[ModelMessage]:
    """Read a Slack thread and convert it to pydantic-ai message history.

    Bot-authored messages become ModelResponse turns; everything else (humans,
    the original Datadog alert) becomes a ModelRequest turn so the agent sees
    the same conversation the user does. The exclude_ts arg drops the message
    we're currently responding to -- that one is passed in as the new prompt.
    """
    try:
        resp = client.conversations_replies(channel=channel, ts=thread_ts, limit=limit)
    except Exception:
        logger.exception("conversations.replies failed for channel=%s ts=%s", channel, thread_ts)
        return []

    history: list[ModelMessage] = []
    for msg in resp.get("messages") or []:
        if exclude_ts and msg.get("ts") == exclude_ts:
            continue
        text = _clean_slack_text(msg.get("text", "")) or _extract_alert_text(msg)
        if not text:
            continue
        if _is_ack_message(text):
            continue
        is_bot_turn = bool(msg.get("bot_id")) or (
            bot_user_id is not None and msg.get("user") == bot_user_id
        )
        if is_bot_turn:
            history.append(ModelResponse(parts=[TextPart(content=text)]))
        else:
            history.append(ModelRequest(parts=[UserPromptPart(content=text)]))
    return history


def build_app() -> App:
    load_dotenv()
    token = os.environ["SLACK_BOT_TOKEN"]
    app = App(token=token)
    assistant = Assistant()

    # Cache the bot's own user_id once at startup so the thread-history helper
    # can identify which messages came from us. auth.test is cheap and only
    # runs once per process.
    try:
        bot_user_id: str | None = app.client.auth_test()["user_id"]
        logger.info("bot user_id resolved: %s", bot_user_id)
    except Exception:
        bot_user_id = None
        logger.exception("auth.test failed; thread history will treat bot replies as user turns")

    @assistant.thread_started
    def thread_started(say, set_suggested_prompts, logger):  # type: ignore[no-untyped-def]
        say("Hi — I'm your SRE assistant. I query Coral and stay read-only.")
        set_suggested_prompts(prompts=SUGGESTED_PROMPTS)

    @assistant.user_message
    def user_message(payload, set_status, say, logger):  # type: ignore[no-untyped-def]
        prompt = _clean_slack_text(payload.get("text", ""))
        set_status("investigating with Coral…")

        async def trace(ctx, events):
            async for event in events:
                if isinstance(event, FunctionToolCallEvent):
                    set_status(f"calling {event.part.tool_name}…")

        try:
            answer = asyncio.run(
                PydanticSreAgent().answer(
                    prompt,
                    slack_context=_event_context(payload),
                    event_stream_handler=trace,
                )
            )
        except Exception:
            logger.exception("SRE agent failed")
            answer = "I hit an error while querying Coral. Check the bot logs for details."
        finally:
            # Always clear the typing-indicator status when we're done, even on error.
            set_status("")

        say(answer)

    app.use(assistant)

    @app.event("app_mention")
    def handle_app_mention(event, say, client, logger):  # type: ignore[no-untyped-def]
        prompt = _clean_slack_text(event.get("text", ""))
        thread_ts = event.get("thread_ts") or event.get("ts")
        is_followup = event.get("thread_ts") is not None

        # When the mention is in an existing thread, fetch the prior turns so
        # the agent has the original alert + investigation as context. For a
        # fresh mention (thread_ts == ts), history is empty.
        message_history: list[ModelMessage] = []
        if is_followup:
            message_history = _fetch_thread_history(
                client,
                event["channel"],
                thread_ts,
                bot_user_id,
                exclude_ts=event.get("ts"),
            )
            logger.info("loaded %d prior turns for thread %s", len(message_history), thread_ts)

        say(
            text="Picking up the thread..." if is_followup else "Investigating with Coral. I'll be conservative about claims.",
            thread_ts=thread_ts,
        )
        try:
            answer = asyncio.run(
                PydanticSreAgent().answer(
                    prompt,
                    slack_context=_event_context(event),
                    message_history=message_history or None,
                )
            )
        except Exception:
            logger.exception("SRE agent failed")
            answer = "I hit an error while querying Coral. Check the bot logs for details."
        say(text=answer, thread_ts=thread_ts)

    alerts_channel_id = os.getenv("ALERTS_CHANNEL_ID")
    datadog_app_id = os.getenv("DATADOG_SLACK_APP_ID")
    handled_alert_ts: set[str] = set()

    @app.event("message")
    def handle_alert_message(event, say, logger):  # type: ignore[no-untyped-def]
        # No-op unless #alerts is configured, so the bot runs fine without it.
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

        # Immediate ack so the channel sees the bot is on it -- the real
        # investigation can take 30s+ depending on tool-call depth.
        say(text=":mag: Investigating this alert with Coral...", thread_ts=ts)

        alert_text = _extract_alert_text(event)
        prompt_parts = [
            "A Datadog alert just fired. Produce the full structured incident assessment "
            "defined in your instructions (Summary / Evidence / Likely cause / Blast radius / "
            "What changed / Mitigation). Ground the Likely cause section in the actual source "
            "code -- if a Sentry stack trace points at a file:line, look that file up in GitHub "
            "via Coral and quote the offending line.",
        ]
        prompt_parts.append(
            "Deployment-specific context (service-to-source mapping):\n"
            + INVESTIGATION_CONTEXT
        )
        prompt_parts.append(f"Alert:\n{alert_text or '(empty alert body)'}")
        prompt = "\n\n".join(prompt_parts)

        try:
            answer = asyncio.run(
                PydanticSreAgent().answer(prompt, slack_context=_event_context(event))
            )
        except Exception:
            logger.exception("SRE agent failed on Datadog alert")
            answer = "I hit an error while investigating this alert. Check the bot logs for details."
        say(text=answer, thread_ts=ts)

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
