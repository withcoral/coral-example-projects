from __future__ import annotations

import asyncio
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from dotenv import load_dotenv
from pydantic_ai.messages import FunctionToolCallEvent
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


def _clean_slack_text(text: str) -> str:
    return " ".join(text.split()).strip()


def _event_context(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "channel": event.get("channel"),
        "thread_ts": event.get("thread_ts") or event.get("ts"),
        "user": event.get("user"),
        "ts": event.get("ts"),
        "event_type": event.get("type"),
    }


def build_app() -> App:
    load_dotenv()
    token = os.environ["SLACK_BOT_TOKEN"]
    app = App(token=token)
    assistant = Assistant()

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
    def handle_app_mention(event, say, logger):  # type: ignore[no-untyped-def]
        prompt = _clean_slack_text(event.get("text", ""))
        thread_ts = event.get("thread_ts") or event.get("ts")
        say(text="Investigating with Coral. I'll be conservative about claims.", thread_ts=thread_ts)
        try:
            answer = asyncio.run(
                PydanticSreAgent().answer(prompt, slack_context=_event_context(event))
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

        alert_text = _clean_slack_text(event.get("text", ""))
        prompt = (
            "A Datadog alert just fired. Investigate it using Coral: likely cause, "
            "blast radius, what changed recently, and concrete next checks.\n\n"
            f"Alert:\n{alert_text}"
        )
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
