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
    FunctionToolResultEvent,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)
from slack_bolt import App, Assistant
from slack_bolt.adapter.socket_mode import SocketModeHandler
from slack_sdk.models.messages.chunk import (
    PlanUpdateChunk,
    TaskUpdateChunk,
)

from sre_agent.agent import PydanticSreAgent, quick_ack

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
- Datadog: metric hello_service.errors (count type), tagged service:hello-service. Monitor IDs live in datadog.monitors. The full alert payload is in the prompt below.
- Sentry: org slug coral-sm, project slug python-fastapi. sentry.issues holds aggregated exceptions (filter by project for recent ones, with counts, first/last seen, short IDs). sentry.events / sentry.project_events have full stack traces.
- Source code: GitHub repository withcoral/coral-example-projects. The hello-service app source lives at SRE-agent/demo-app/main.py. Coral's GitHub source exposes github.commits and github.contents for this repo, both of which accept a `ref` filter (branch name or commit SHA).
  - Heads-up on branches: production-deployed code does not always live on the repo's default branch. If github.contents returns 404 (or empty) for a path you have strong evidence exists (from a Sentry stack trace, for example), the default branch is probably stale and the deploy is running off a development branch. List the repo's branches via github.branches (or equivalent) and retry the same path with `ref = '<that-branch>'` in the WHERE clause. Don't give up after one 404.

URL templates for the Sources section (and inline links):
- Datadog monitor: https://app.datadoghq.eu/monitors/{MONITOR_ID}
- Sentry issue:    https://coral-sm.sentry.io/issues/{ISSUE_ID}/  (use the numeric id, not the short id; the short id also works via redirect)
- GitHub file:     https://github.com/withcoral/coral-example-projects/blob/main/{PATH}  (e.g. SRE-agent/demo-app/main.py)
- GitHub commit:   https://github.com/withcoral/coral-example-projects/commit/{SHA}
Render every URL as Slack mrkdwn: <URL|short label>. Example: <https://app.datadoghq.eu/monitors/108023099|Datadog monitor>.

Investigation guidance:
- You have a generous tool-call budget. Be thorough: query each data source as many times as you need to get strong evidence. Cross-reference findings across Sentry, GitHub, and Datadog.
- Sentry is usually the highest-signal source for code-level errors -- start there to find the dominant issue, exception type, and file:line.
- Pull the actual offending source line from GitHub when possible so the Likely cause section quotes real code, not paraphrase.
- If github.contents returns no rows for the file, try one alternate (e.g. a parent directory listing to confirm path indexing) -- if that also yields nothing, note the gap in Evidence and rely on the Sentry stack trace for the Likely cause.
- Check github.commits for recent touches to SRE-agent/demo-app/ around the alert's onset timestamp.

Synthesis: produce the full structured assessment (Summary / Evidence / Likely cause / Blast radius / What changed / Mitigation) defined in your system instructions.
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


# Hard cap on a single agent.answer() call. If the agent hasn't converged by
# this point we stop and post whatever we have rather than leaving the thread
# silent forever. Set generously -- the agent is allowed plenty of tool budget
# (100 rounds, 50 retries per tool), so the timeout should accommodate.
AGENT_RUN_TIMEOUT_SECONDS = 600


def _coerce_args_to_dict(tool_args: Any) -> dict[str, Any]:
    """pydantic-ai's ToolCallPart.args can be either a JSON string or a dict
    depending on how the model emits the call. Normalise to dict."""
    if isinstance(tool_args, dict):
        return tool_args
    if isinstance(tool_args, str):
        try:
            import json as _json
            parsed = _json.loads(tool_args)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _task_title_from_tool_call(tool_name: str, tool_args: Any) -> str:
    """Format a short, scannable title for a single Coral MCP tool call. The
    title shows up in the Slack plan block so an operator can see what the
    agent is currently doing without reading raw JSON.

    Arg names below match Coral's actual MCP schema (verified via list_tools):
      sql:            sql
      list_tables:    schema (optional)
      search_tables:  pattern, schema (optional)
      describe_table: schema, table
      list_columns:   schema, table, pattern (optional)
    """
    args = _coerce_args_to_dict(tool_args)
    if tool_name == "sql":
        sql = (args.get("sql") or "").strip().replace("\n", " ")
        return f"sql: {sql[:90]}{'…' if len(sql) > 90 else ''}" if sql else "sql"
    if tool_name in ("describe_table", "list_columns"):
        schema = args.get("schema") or ""
        table = args.get("table") or ""
        qualified = f"{schema}.{table}" if schema and table else (table or schema or "?")
        pattern = args.get("pattern")
        suffix = f" /{pattern}/" if pattern else ""
        return f"{tool_name}({qualified}){suffix}"
    if tool_name == "list_tables":
        return f"list_tables({args.get('schema') or 'all'})"
    if tool_name == "search_tables":
        pattern = args.get("pattern") or "?"
        scope = args.get("schema")
        if scope:
            return f"search_tables(/{pattern}/ in {scope})"
        return f"search_tables(/{pattern}/)"
    if not args:
        return tool_name
    short_args = ", ".join(f"{k}={str(v)[:30]}" for k, v in list(args.items())[:2])
    return f"{tool_name}({short_args})"


def _markdown_blocks(text: str) -> list[dict[str, Any]]:
    """Wrap a long text reply in a Slack markdown block so GitHub-flavored
    markdown (## headers, tables, fenced code with language hints, `[link](url)`)
    renders as rich UI rather than raw syntax."""
    return [{"type": "markdown", "text": text}]


def _alert_level_for(headline: str) -> str:
    """Map status emojis in a one-line headline to Slack alert block levels.

    Default is "error" -- this banner sits at the top of an alert
    investigation, so a Datadog monitor firing is the load-bearing case.
    """
    low = headline.lower()
    if ":white_check_mark:" in low or ":green_circle:" in low:
        return "success"
    if ":large_yellow_circle:" in low or ":warning:" in low:
        return "warning"
    return "error"


def _final_assessment_blocks(headline: str, body: str) -> list[dict[str, Any]]:
    """Build the final-reply block sequence: a severity alert banner on top,
    followed by the full markdown assessment. Falls back to just the markdown
    block if the headline is empty."""
    blocks: list[dict[str, Any]] = []
    if headline.strip():
        blocks.append({
            "type": "alert",
            "level": _alert_level_for(headline),
            "text": {"type": "mrkdwn", "text": headline.strip()},
        })
    blocks.append({"type": "markdown", "text": body})
    return blocks


def _run_with_timeout(coro: Any, timeout: float = AGENT_RUN_TIMEOUT_SECONDS) -> Any:
    """asyncio.run() wrapping the coroutine in asyncio.wait_for. Lets the
    Slack handler post a 'had to stop early' fallback instead of hanging."""
    async def _bounded():
        return await asyncio.wait_for(coro, timeout=timeout)
    return asyncio.run(_bounded())


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

        try:
            answer = _run_with_timeout(
                PydanticSreAgent().answer(
                    prompt,
                    slack_context=_event_context(event),
                    message_history=message_history or None,
                )
            )
        except asyncio.TimeoutError:
            logger.warning("SRE agent timed out after %ds on @-mention", AGENT_RUN_TIMEOUT_SECONDS)
            answer = (
                f":hourglass_flowing_sand: I had to stop early -- this took longer than "
                f"{AGENT_RUN_TIMEOUT_SECONDS}s. Narrow your question and I'll have another go."
            )
        except Exception:
            logger.exception("SRE agent failed")
            answer = "I hit an error while querying Coral. Check the bot logs for details."
        say(text=answer, blocks=_markdown_blocks(answer), thread_ts=thread_ts)

    alerts_channel_id = os.getenv("ALERTS_CHANNEL_ID")
    datadog_app_id = os.getenv("DATADOG_SLACK_APP_ID")
    handled_alert_ts: set[str] = set()

    @app.event("message")
    def handle_alert_message(event, say, client, logger):  # type: ignore[no-untyped-def]
        # Only fires on Datadog-app messages in #alerts. Human thread replies
        # need an @-mention to trigger the bot (see handle_app_mention above).
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

        alert_text = _extract_alert_text(event)
        try:
            quick_ack_text = asyncio.run(quick_ack(alert_text))
        except Exception:
            logger.exception("quick_ack failed")
            quick_ack_text = ":mag: Investigating this alert with Coral..."

        # The plan title field doesn't render emoji codes (`:mag:`), so strip
        # a leading one if present rather than letting "mag:" leak into the UI.
        plan_title = quick_ack_text.strip()
        if plan_title.startswith(":") and " " in plan_title:
            first, rest = plan_title.split(" ", 1)
            if first.startswith(":") and first.endswith(":"):
                plan_title = rest.strip()

        # Use Slack's purpose-built streaming API for agent progress. Each
        # TaskUpdateChunk with a stable `id` updates that task in place --
        # crucially, this does NOT re-render the whole block (no UI collapse
        # on update, unlike chat.update).
        try:
            stream_resp = client.chat_startStream(
                channel=event["channel"],
                thread_ts=ts,
                markdown_text=quick_ack_text,
                chunks=[PlanUpdateChunk(title=plan_title).to_dict()],
            )
            stream_ts = stream_resp["ts"]
            streaming = True
        except Exception:
            logger.exception("chat.startStream failed; falling back to plain message")
            stream_ts = None
            streaming = False
            client.chat_postMessage(
                channel=event["channel"],
                thread_ts=ts,
                text=quick_ack_text,
            )

        task_titles: dict[str, str] = {}

        def _push_task(call_id: str, title: str, status: str):
            task_titles[call_id] = title
            if not streaming or stream_ts is None:
                return
            try:
                client.chat_appendStream(
                    channel=event["channel"],
                    ts=stream_ts,
                    chunks=[TaskUpdateChunk(id=call_id, title=title, status=status).to_dict()],
                )
            except Exception:
                logger.exception("chat.appendStream failed for task %s (ignored)", call_id)

        async def stream_handler(_ctx, events):
            async for ev in events:
                if isinstance(ev, FunctionToolCallEvent):
                    call_id = getattr(ev.part, "tool_call_id", None) or f"call-{len(task_titles)}"
                    title = _task_title_from_tool_call(
                        ev.part.tool_name, getattr(ev.part, "args", None)
                    )
                    _push_task(call_id, title, "in_progress")
                elif isinstance(ev, FunctionToolResultEvent):
                    call_id = getattr(
                        getattr(ev, "part", None), "tool_call_id", None
                    ) or getattr(ev, "tool_call_id", None)
                    if not call_id or call_id not in task_titles:
                        continue
                    _push_task(call_id, task_titles[call_id], "complete")

        prompt = "\n\n".join([
            "A Datadog alert just fired. Produce the full structured incident assessment "
            "defined in your instructions (Summary / Evidence / Likely cause / Blast radius / "
            "What changed / Mitigation). Ground the Likely cause section in the actual source "
            "code -- if a Sentry stack trace points at a file:line, look that file up in GitHub "
            "via Coral and quote the offending line.",
            "Deployment-specific context (service-to-source mapping):\n" + INVESTIGATION_CONTEXT,
            f"Alert:\n{alert_text or '(empty alert body)'}",
        ])

        try:
            answer = _run_with_timeout(
                PydanticSreAgent().answer(
                    prompt,
                    slack_context=_event_context(event),
                    event_stream_handler=stream_handler,
                )
            )
            final_status_for_open = "complete"
        except asyncio.TimeoutError:
            logger.warning("SRE agent timed out after %ds on Datadog alert", AGENT_RUN_TIMEOUT_SECONDS)
            answer = (
                f":hourglass_flowing_sand: I had to stop early -- the investigation ran past "
                f"{AGENT_RUN_TIMEOUT_SECONDS}s. The Sentry issue + Datadog monitor are visible "
                f"in this alert; @-mention me with a follow-up question if you want me to dig further."
            )
            final_status_for_open = "error"
        except Exception:
            logger.exception("SRE agent failed on Datadog alert")
            answer = "I hit an error while investigating this alert. Check the bot logs for details."
            final_status_for_open = "error"

        # Final reply = severity alert banner (level inferred from the
        # contextual ack's emoji) + the full markdown assessment.
        final_blocks = _final_assessment_blocks(quick_ack_text, answer)
        if streaming and stream_ts is not None:
            try:
                client.chat_stopStream(
                    channel=event["channel"],
                    ts=stream_ts,
                    markdown_text=answer,
                    blocks=final_blocks,
                )
            except Exception:
                logger.exception("chat.stopStream failed; posting answer as a fallback message")
                say(text=answer, blocks=final_blocks, thread_ts=ts)
        else:
            # Stream never opened; post the answer as a normal threaded reply.
            say(text=answer, blocks=final_blocks, thread_ts=ts)

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
