from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from sre_agent.agent import PedanticSreAgent

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)


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

    @app.event("app_mention")
    def handle_app_mention(event, say, logger):  # type: ignore[no-untyped-def]
        prompt = _clean_slack_text(event.get("text", ""))
        thread_ts = event.get("thread_ts") or event.get("ts")
        say(text="Investigating with Coral. I will be conservative about claims.", thread_ts=thread_ts)
        try:
            answer = asyncio.run(
                PedanticSreAgent().answer(prompt, slack_context=_event_context(event))
            )
        except Exception:
            logger.exception("SRE agent failed")
            answer = "I hit an error while querying Coral. Check the bot logs for details."
        say(text=answer, thread_ts=thread_ts)

    @app.message("")
    def handle_direct_message(message, say, logger):  # type: ignore[no-untyped-def]
        if message.get("channel_type") != "im" or message.get("bot_id"):
            return
        prompt = _clean_slack_text(message.get("text", ""))
        try:
            answer = asyncio.run(
                PedanticSreAgent().answer(prompt, slack_context=_event_context(message))
            )
        except Exception:
            logger.exception("SRE agent failed")
            answer = "I hit an error while querying Coral. Check the bot logs for details."
        say(text=answer)

    return app


def main() -> None:
    app = build_app()
    app_token = os.environ["SLACK_APP_TOKEN"]
    SocketModeHandler(app, app_token).start()


if __name__ == "__main__":
    main()

