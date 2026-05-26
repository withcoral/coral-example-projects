"""Convert a Slack thread into pydantic-ai `message_history` so the agent
can answer follow-up @-mentions with full conversational context.

Public surface:
    event_context(event)           — minimal context dict for the agent prompt
    fetch_thread_history(...)      — Slack thread -> list[ModelMessage]
    is_ack_message(text)           — filter out bot's own "Investigating..." acks
"""
from __future__ import annotations

import logging
from typing import Any

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)

from sre_agent.slack_format import clean_slack_text, extract_alert_text

logger = logging.getLogger(__name__)


# Short bot acks we strip from message_history. Including them as
# substantive turns just clutters the model's view of the conversation.
_ACK_SUBSTRINGS = (
    "Investigating with Coral",
    "Investigating this alert with Coral",
)


def is_ack_message(text: str) -> bool:
    return any(s in text for s in _ACK_SUBSTRINGS)


def event_context(event: dict[str, Any]) -> dict[str, Any]:
    """Minimal Slack-context dict that gets serialised into the user
    prompt so the agent knows which channel/thread it's running in."""
    return {
        "channel": event.get("channel"),
        "thread_ts": event.get("thread_ts") or event.get("ts"),
        "user": event.get("user"),
        "ts": event.get("ts"),
        "event_type": event.get("type"),
    }


def fetch_thread_history(
    client: Any,
    channel: str,
    thread_ts: str,
    bot_user_id: str | None,
    *,
    exclude_ts: str | None = None,
    limit: int = 50,
) -> list[ModelMessage]:
    """Read a Slack thread and convert it to pydantic-ai message history.

    Bot-authored messages become ModelResponse turns; everything else
    (humans, the original Datadog alert) becomes a ModelRequest turn so
    the agent sees the same conversation the user does. `exclude_ts`
    drops the message we're currently responding to — that one is
    passed in as the new prompt rather than as prior context.
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
        text = clean_slack_text(msg.get("text", "")) or extract_alert_text(msg)
        if not text:
            continue
        if is_ack_message(text):
            continue
        is_bot_turn = bool(msg.get("bot_id")) or (
            bot_user_id is not None and msg.get("user") == bot_user_id
        )
        if is_bot_turn:
            history.append(ModelResponse(parts=[TextPart(content=text)]))
        else:
            history.append(ModelRequest(parts=[UserPromptPart(content=text)]))
    return history
