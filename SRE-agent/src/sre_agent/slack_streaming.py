"""End-to-end streaming pipeline for the SRE agent's Slack replies.

`run_streamed_investigation` is shared across the three Slack entry
points (Datadog alert, channel @-mention, DM). The flow:

    contextual quick_ack
        -> chat.startStream (plan mode, first chunk = PlanUpdateChunk)
        -> per-tool-call chat.appendStream with TaskUpdateChunk
        -> chat.stopStream
        -> separate chat.postMessage with the full markdown body + sources + context

Splitting the final reply from the stream avoids `msg_too_long` on
`chat.stopStream` while still giving the user live progress.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    ModelMessage,
    RetryPromptPart,
)
from slack_sdk.models.messages.chunk import (
    PlanUpdateChunk,
    TaskUpdateChunk,
)

from sre_agent.agent import PydanticSreAgent, quick_ack
from sre_agent.slack_format import (
    final_assessment_blocks,
    strip_leading_emoji,
    summarize_tool_result,
    task_title_from_tool_call,
)
from sre_agent.slack_thread_history import event_context

logger = logging.getLogger(__name__)


# Hard cap on a single agent.answer() call. If the agent hasn't
# converged by this point we stop and post whatever we have rather
# than leaving the Slack thread silent forever.
AGENT_RUN_TIMEOUT_SECONDS = 600


def _run_with_timeout(coro: Any, timeout: float = AGENT_RUN_TIMEOUT_SECONDS) -> Any:
    """asyncio.run() wrapping a coroutine in asyncio.wait_for."""
    async def _bounded():
        return await asyncio.wait_for(coro, timeout=timeout)
    return asyncio.run(_bounded())


def run_streamed_investigation(
    *,
    user_input: str,
    prompt: str,
    channel: str,
    parent_ts: str,
    client: Any,
    say: Any,
    event: dict[str, Any],
    team_id: str | None = None,
    message_history: list[ModelMessage] | None = None,
) -> None:
    """Run the agent with live Block Kit streaming + a structured final
    reply in the given Slack thread.

    Args:
        user_input: Text fed to the quick_ack model call. For an alert
            that's the extracted alert body; for an @-mention or DM it's
            the user's question.
        prompt: Full prompt fed to the agent (alerts include deployment
            context + structured-output ask; @-mentions/DMs pass the
            user's text directly).
        channel: Slack channel id to post into.
        parent_ts: Thread root ts the stream + final reply attach to.
        client: Bolt's WebClient (`app.client` / handler-injected).
        say: Bolt's say() helper, used for the fallback path if streaming
            never opens.
        event: Original Slack event dict — used for routing
            (recipient_user_id) and to populate the agent's slack_context.
        team_id: Bot's team_id (required by chat.startStream).
        message_history: Optional pydantic-ai message history (used by
            @-mention follow-ups so the agent has prior turns as context).
    """
    # ---- 1. Contextual quick_ack ---------------------------------------
    try:
        quick_ack_text = asyncio.run(quick_ack(user_input))
    except Exception:
        logger.exception("quick_ack failed")
        quick_ack_text = ":mag: Investigating with Coral..."

    plan_title = strip_leading_emoji(quick_ack_text)

    # ---- 2. Open the stream --------------------------------------------
    # Open in `plan` task display mode with the title as the first chunk.
    # Passing markdown_text alongside chunks puts the stream in TEXT mode
    # and subsequent chunk appends fail with `streaming_mode_mismatch`.
    start_kwargs: dict[str, Any] = {
        "channel": channel,
        "thread_ts": parent_ts,
        "task_display_mode": "plan",
        "chunks": [PlanUpdateChunk(title=plan_title or "Investigating").to_dict()],
    }
    if team_id:
        start_kwargs["recipient_team_id"] = team_id
    user_id = event.get("user")
    if user_id:
        start_kwargs["recipient_user_id"] = user_id

    try:
        stream_resp = client.chat_startStream(**start_kwargs)
        stream_ts = stream_resp["ts"]
        streaming = True
    except Exception:
        logger.exception("chat.startStream failed; falling back to plain message")
        stream_ts = None
        streaming = False
        client.chat_postMessage(channel=channel, thread_ts=parent_ts, text=quick_ack_text)

    # ---- 3. Stream task chunks as the agent runs -----------------------
    task_titles: dict[str, str] = {}
    tool_call_count = 0

    def _push_task(call_id: str, title: str, status: str, output: str | None = None) -> None:
        task_titles[call_id] = title
        if not streaming or stream_ts is None:
            return
        try:
            chunk = TaskUpdateChunk(
                id=call_id,
                title=title,
                status=status,
                output=output or None,  # None instead of empty string
            )
            client.chat_appendStream(
                channel=channel,
                ts=stream_ts,
                chunks=[chunk.to_dict()],
            )
        except Exception:
            logger.exception("chat.appendStream failed for task %s (ignored)", call_id)

    async def stream_handler(_ctx: Any, events: Any) -> None:
        nonlocal tool_call_count
        async for ev in events:
            if isinstance(ev, FunctionToolCallEvent):
                tool_call_count += 1
                call_id = (
                    getattr(ev.part, "tool_call_id", None) or f"call-{len(task_titles)}"
                )
                title = task_title_from_tool_call(
                    ev.part.tool_name, getattr(ev.part, "args", None)
                )
                _push_task(call_id, title, "in_progress")
            elif isinstance(ev, FunctionToolResultEvent):
                part = getattr(ev, "part", None)
                call_id = (
                    getattr(part, "tool_call_id", None)
                    or getattr(ev, "tool_call_id", None)
                )
                if not call_id or call_id not in task_titles:
                    continue
                # RetryPromptPart = the model is being asked to retry the
                # tool call (validation/schema mismatch on its first try).
                # Mark the failed attempt as error so the plan reflects
                # which queries the agent had to correct before landing.
                if isinstance(part, RetryPromptPart):
                    status = "error"
                    output = "retry requested"
                else:
                    status = "complete"
                    output = summarize_tool_result(part)
                _push_task(call_id, task_titles[call_id], status, output)

    # ---- 4. Run the agent ----------------------------------------------
    run_started = time.perf_counter()
    try:
        answer = _run_with_timeout(
            PydanticSreAgent().answer(
                prompt,
                slack_context=event_context(event),
                event_stream_handler=stream_handler,
                message_history=message_history or None,
            )
        )
    except asyncio.TimeoutError:
        logger.warning("SRE agent timed out after %ds", AGENT_RUN_TIMEOUT_SECONDS)
        answer = (
            f":hourglass_flowing_sand: I had to stop early — the investigation ran past "
            f"{AGENT_RUN_TIMEOUT_SECONDS}s. @-mention me with a narrower question if you want "
            f"me to dig further."
        )
    except Exception:
        logger.exception("SRE agent failed")
        answer = "I hit an error while querying Coral. Check the bot logs for details."

    duration = time.perf_counter() - run_started

    # ---- 5. Close the stream + post the final assessment ---------------
    # stopStream's `blocks=` param hits `msg_too_long` for typical 5-10k
    # markdown bodies, so we close the stream cleanly and post the
    # assessment as a separate threaded reply (where the same blocks
    # land via the normal chat.postMessage limits).
    if streaming and stream_ts is not None:
        try:
            client.chat_stopStream(channel=channel, ts=stream_ts)
        except Exception:
            logger.exception("chat.stopStream failed (continuing to post final reply)")

    final_blocks = final_assessment_blocks(
        answer,
        model=os.getenv("SRE_AGENT_MODEL") or os.getenv("ANTHROPIC_MODEL"),
        tool_calls=tool_call_count or None,
        duration_seconds=duration,
    )
    say(text=answer, blocks=final_blocks, thread_ts=parent_ts)
