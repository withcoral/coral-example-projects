from __future__ import annotations

import asyncio
import logging
import os
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

    return app


def main() -> None:
    app = build_app()
    app_token = os.environ["SLACK_APP_TOKEN"]
    SocketModeHandler(app, app_token).start()


if __name__ == "__main__":
    main()
