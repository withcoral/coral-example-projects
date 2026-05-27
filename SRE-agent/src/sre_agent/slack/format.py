"""Block Kit and Slack-mrkdwn formatting helpers for the SRE agent's replies.

Pure functions only: take text or data, return Slack block dicts or
formatted strings. No I/O, no Slack API calls. Reusable across the
streaming pipeline (`slack_streaming.py`) and the bot entry points
(`slackbot.py`).
"""
from __future__ import annotations

import json as _json
import re
from typing import Any


# Match a GFM table separator line (`|---|---|---|`). Used to detect a
# table header so we can ensure a blank line precedes it.
_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|[\s\-:|]+\|\s*$")

# Slack's markdown block caps the text field at 12000 characters. A long
# multi-source assessment can exceed that; we split at paragraph
# boundaries and use this slightly-under-12k value as a safety margin.
_MARKDOWN_BLOCK_LIMIT = 11800

# Match the `## Sources` section of an agent reply so we can pull it
# out and render the entries as actions-block URL buttons.
_SOURCES_SECTION_RE = re.compile(
    r"\n##\s+Sources\s*\n(?P<body>.*?)(?=\n##\s|\Z)", re.DOTALL | re.IGNORECASE
)

# Match a single sources bullet. Two shapes supported:
#   - **[Datadog]** [Monitor 108023099 — ...](https://...)
#   - [some link text](https://...)
_SOURCE_BULLET_RE = re.compile(
    r"""
    ^\s*[-*]\s*                            # bullet
    (?:\*\*\[(?P<source>[^\]]+)\]\*\*\s*)? # optional **[SourceName]** prefix
    \[(?P<title>[^\]]+)\]\((?P<url>https?://[^)\s]+)\)
    """,
    re.VERBOSE,
)


def clean_slack_text(text: str) -> str:
    """Collapse whitespace and trim."""
    return " ".join(text.split()).strip()


def extract_alert_text(event: dict[str, Any]) -> str:
    """Datadog posts the alert body via Slack attachments, not the
    top-level `text`. Pull from both so the agent actually sees what
    fired (and to populate thread history with the alert content)."""
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
    return clean_slack_text("\n".join(parts))


def strip_leading_emoji(text: str) -> str:
    """The Slack `plan` block title field renders text without emoji
    shortcode substitution, so `:mag:` at the start would show up as
    literal characters. Strip a leading `:name:` token."""
    out = text.strip()
    if out.startswith(":") and " " in out:
        first, rest = out.split(" ", 1)
        if first.startswith(":") and first.endswith(":"):
            return rest.strip()
    return out


def ensure_table_spacing(text: str) -> str:
    """GFM tables only render when preceded by a blank line. Models
    sometimes omit it; detect any `|...|` row immediately followed by
    a `|---|---|` separator and inject a blank line before it if the
    previous line isn't already blank. Idempotent."""
    lines = text.split("\n")
    out: list[str] = []
    for i, line in enumerate(lines):
        is_table_header = (
            line.lstrip().startswith("|")
            and i + 1 < len(lines)
            and _TABLE_SEPARATOR_RE.match(lines[i + 1])
        )
        if is_table_header and out and out[-1].strip() != "":
            out.append("")
        out.append(line)
    return "\n".join(out)


def split_markdown_into_chunks(text: str, limit: int = _MARKDOWN_BLOCK_LIMIT) -> list[str]:
    """Split a long markdown body so each chunk fits in a Slack
    markdown block. Prefer paragraph (`\\n\\n`) boundaries, then
    single-newline, then a hard char cut. Idempotent on short inputs."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        window = remaining[:limit]
        cut = window.rfind("\n\n")
        if cut <= 0:
            cut = window.rfind("\n")
        if cut <= 0:
            cut = limit
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip("\n")
    return chunks


def markdown_blocks(text: str) -> list[dict[str, Any]]:
    """Wrap a markdown body in one or more Slack `markdown` blocks so
    GitHub-flavored markdown (## headers, GFM tables, fenced code with
    language hints, `[link](url)`) renders as native UI. Normalises
    table spacing first; chunks long bodies to fit the 12k char limit."""
    normalised = ensure_table_spacing(text)
    return [{"type": "markdown", "text": chunk}
            for chunk in split_markdown_into_chunks(normalised)]


def split_sources(answer: str) -> tuple[str, list[dict[str, str]]]:
    """Pull the `## Sources` section out of the markdown body. Returns
    `(body_without_sources, sources)`. Each source dict has
    `source` / `title` / `url` keys."""
    match = _SOURCES_SECTION_RE.search(answer)
    if not match:
        return answer, []
    sources: list[dict[str, str]] = []
    for line in match.group("body").splitlines():
        m = _SOURCE_BULLET_RE.match(line)
        if m:
            sources.append({
                "source": (m.group("source") or "Link").strip(),
                "title": m.group("title").strip(),
                "url": m.group("url").strip(),
            })
    body = (answer[: match.start()] + answer[match.end():]).rstrip()
    return body, sources


def source_action_blocks(sources: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Render parsed sources as a `Sources` header + one or more
    `actions` blocks with URL buttons. Buttons open the target URL
    directly (no action_id handler needed). Slack caps actions at 25
    elements and button text at 75 chars; chunk + truncate accordingly."""
    if not sources:
        return []
    blocks: list[dict[str, Any]] = [{
        "type": "header",
        "text": {"type": "plain_text", "text": "Sources", "emoji": True},
    }]
    # 5 buttons per row keeps each row readable.
    for start in range(0, min(len(sources), 25), 5):
        elements = []
        for i, src in enumerate(sources[start : start + 5]):
            label = f"{src['source']}: {src['title']}" if src["title"] else src["source"]
            if len(label) > 75:
                label = label[:72] + "…"
            elements.append({
                "type": "button",
                "text": {"type": "plain_text", "text": label},
                "url": src["url"],
                "action_id": f"open_source_{start + i}",
            })
        blocks.append({"type": "actions", "elements": elements})
    return blocks


def context_footer(
    *,
    model: str | None = None,
    tool_calls: int | None = None,
    duration_seconds: float | None = None,
) -> dict[str, Any] | None:
    """Build a Slack `context` block showing the model, tool-call count,
    and wall-clock duration of an investigation. Returns None if all the
    metadata is empty."""
    meta_parts: list[str] = []
    if model:
        # Drop the provider prefix: `anthropic:claude-opus-4-7` -> `claude-opus-4-7`.
        meta_parts.append(f":robot_face: {model.split(':', 1)[-1]}")
    if tool_calls is not None:
        meta_parts.append(
            f":wrench: {tool_calls} Coral {'query' if tool_calls == 1 else 'queries'}"
        )
    if duration_seconds is not None and duration_seconds > 0:
        if duration_seconds < 60:
            dur = f"{duration_seconds:.0f}s"
        else:
            dur = f"{int(duration_seconds // 60)}m {int(duration_seconds % 60)}s"
        meta_parts.append(f":stopwatch: {dur}")
    if not meta_parts:
        return None
    return {
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": " · ".join(meta_parts)}],
    }


def final_assessment_blocks(
    body: str,
    *,
    model: str | None = None,
    tool_calls: int | None = None,
    duration_seconds: float | None = None,
) -> list[dict[str, Any]]:
    """Build the final-reply block sequence: markdown assessment +
    sources rendered as URL buttons + optional context footer."""
    body_without_sources, sources = split_sources(body)
    blocks: list[dict[str, Any]] = []
    blocks.extend(markdown_blocks(body_without_sources))
    blocks.extend(source_action_blocks(sources))
    footer = context_footer(
        model=model, tool_calls=tool_calls, duration_seconds=duration_seconds
    )
    if footer is not None:
        blocks.append(footer)
    return blocks


def coerce_args_to_dict(tool_args: Any) -> dict[str, Any]:
    """pydantic-ai's ToolCallPart.args can be either a JSON string or a
    dict depending on how the model emits the call. Normalise to dict."""
    if isinstance(tool_args, dict):
        return tool_args
    if isinstance(tool_args, str):
        try:
            parsed = _json.loads(tool_args)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def task_title_from_tool_call(tool_name: str, tool_args: Any) -> str:
    """Format a short, scannable title for a single Coral MCP tool call.
    Shows up in the Slack plan block.

    Arg names match Coral's MCP schema (verified via list_tools):
        sql:            sql
        list_tables:    schema (optional)
        search_tables:  pattern, schema (optional)
        describe_table: schema, table
        list_columns:   schema, table, pattern (optional)
    """
    args = coerce_args_to_dict(tool_args)
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


def summarize_tool_result(part: Any) -> str:
    """Produce a one-line summary of a tool's return value for the
    plan block's `output` field. Coral usually returns an ASCII table,
    so the data-row count is the most useful summary."""
    content = getattr(part, "content", None)
    if content is None:
        return ""
    text = str(content).strip()
    if not text:
        return ""
    lines = text.splitlines()
    # ASCII-table format: `+---` borders, `|...|` data rows.
    if lines and lines[0].startswith("+"):
        data_lines = [ln for ln in lines if ln.startswith("|") and not ln.startswith("|---")]
        n_rows = max(len(data_lines) - 1, 0)  # subtract the header row
        return f"{n_rows} row{'s' if n_rows != 1 else ''}"
    # Plain text -- take the first non-empty line, truncated.
    for ln in lines:
        if ln.strip():
            ln = ln.strip()
            return ln[:80] + ("…" if len(ln) > 80 else "")
    return ""
