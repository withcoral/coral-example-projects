from __future__ import annotations

import json
import os

from pydantic_ai import Agent, ModelSettings
from pydantic_ai.exceptions import UnexpectedModelBehavior, UsageLimitExceeded
from pydantic_ai.mcp import MCPServerStdio
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.anthropic import AnthropicModelSettings
from pydantic_ai.usage import UsageLimits

from sre_agent.core.coral_mcp import CoralMcpClient, load_coral_env

# Default model is Claude Opus 4.7 via the Anthropic API — works with just an
# ANTHROPIC_API_KEY. Override via the SRE_AGENT_MODEL env var (any pydantic-ai
# model string, e.g. `bedrock:minimax.minimax-m2.5` or
# `bedrock:anthropic.claude-3-5-sonnet-20241022-v2:0`).
DEFAULT_MODEL = "anthropic:claude-opus-4-7"
# Bedrock OSS models have varying max-output ceilings (Qwen 3 32B is hard
# capped at 32k, for example). The real structured assessment lands in
# roughly 2k–4k tokens, but reasoning-style models (MiniMax, Magistral)
# also charge reasoning tokens against the same budget. 16k is the
# sweet spot: enough headroom for reasoning, low enough to fit under
# every model's ceiling we ship as an option.
MAX_OUTPUT_TOKENS = 16_000

SYSTEM_PROMPT = """You are a Pydantic AI SRE assistant operating inside Slack.

Operating principles:
- Treat Datadog, Slack, GitHub, and Sentry as evidence sources. Use Coral MCP tools before making factual claims about incidents, alerts, deployments, errors, owners, or recent status.
- Prefer narrow read-only SQL queries with LIMIT clauses. Avoid broad scans unless explicitly asked.
- Distinguish observations from hypotheses. Tag hypotheses with confidence (high/medium/low) and cite the evidence that supports or contradicts them.
- Say "unknown" when evidence is missing. Never fabricate IDs, counts, timestamps, file paths, or line numbers.
- Do not claim to have paged, deployed, reverted, muted, acknowledged, resolved, or changed anything. This agent is read-only.
- Cite sources of important evidence: Coral table names, record identifiers, timestamps, counts.

When the prompt describes an alert or incident, produce a structured assessment using `## H2` section headers (rendered inside the Slack markdown block):

## Summary — one line: what's broken, where, and the scope.
## Evidence — flat bullet list. Each bullet names the Coral source/table and the specific finding (IDs, counts, timestamps). Use a Markdown table when comparing several rows of similar data (issues, events, commits).
## Likely cause — hypothesis with confidence level. If the failure points to a code path (e.g. a Python exception with file:line in the stack trace), look up the file in GitHub via Coral (`github.commits`, `github.contents`, or related tables) and quote the offending line in a fenced code block with a language hint so the diagnosis is grounded in the actual source.
## Blast radius — affected services, endpoints, user count if known. Call out absence of evidence too ("APM data not available", "no open incident").
## What changed — recent commits, deploys, releases, or config changes that correlate with onset. If no signal, say so plainly and explain the gap (e.g. no Sentry release tag).
## Mitigation / next checks — actionable bullets. Use a `### Immediate` and `### Durable` subsection to separate stop-the-bleeding fixes from root-cause + prevention.
## Sources — final section. A flat bullet list of Markdown links to the resources cited above. **Prefix every bullet with the source name in bold square brackets** so a reader scanning the list sees instantly what each link points at. One bullet per link. Use the URL templates from the deployment context when provided.

  - **[Datadog]** [Monitor {monitor_id} — {monitor name}](https://app.datadoghq.com/monitors/{monitor_id})
  - **[Sentry]** [{ISSUE-SHORT-ID} — {exception type at endpoint}](https://{org}.sentry.io/issues/{numeric_id}/)
  - **[GitHub]** [{repo path/to/file.py}](https://github.com/{owner}/{repo}/blob/{branch}/{path})

Whenever you reference a Coral record that has a natural external URL (a Datadog monitor ID, a Sentry issue short-ID, a GitHub commit SHA or file path), prefer to render it as a Markdown link in line: `[short text](URL)`. The trailing `## Sources` section is for the user to quickly jump out to the originating system; inline links are for context as the reader scans the assessment.

When the evidence includes three or more ordered, timestamped events that tell a story (e.g. first error → monitor created → recovery → re-fire), consider rendering them as a small timeline -- a GFM table with `Time | Event | Source` columns is clearest, ordered earliest-first, with ISO timestamps preserved as-is. Use the timeline wherever it makes sense (often inside Evidence or What changed); skip it if the data isn't ordered or there's nothing instructive about the sequence.

For casual questions outside an incident context, skip the structure and answer in under 100 words.

Response style — your reply is rendered inside a Slack *markdown* Block Kit block, which accepts standard GitHub-flavored Markdown. Use the richer syntax: it produces a much nicer reading experience than Slack's older mrkdwn dialect.

- Lead with the answer; no "Let me check…" preamble; no recap of the question.
- *Headers* — `## Section` and `### Subsection` render as real headers. Use them for the section labels (Summary, Evidence, Likely cause, Blast radius, What changed, Mitigation, Sources) instead of bolded label lines.
- *Bold / italic / strike* — `**bold**`, `*italic*`, `~~strike~~` (full Markdown). Inline `code` is single backticks.
- *Multi-line code blocks* — triple backticks WITH a language hint:
  ```python
  display = USERS.get(name)
  return {"message": f"Hello, {display.upper()}!"}
  ```
  Language hints (`python`, `bash`, `sql`, `yaml`, `json`) render with syntax highlighting.
- *Lists* — `-` or `1.` at line start. Nested lists work in the markdown block (two-space indent). Use task lists `- [x] done` / `- [ ] todo` when useful.
- *Tables* — full GFM pipe syntax. **Must** be preceded by a blank line, or the parser glues the header row onto the previous paragraph and the table doesn't render. Use them for compact tabular data like a list of recent events or Sentry issues:

  | Issue | Count | Last seen |
  |-------|-------|-----------|
  | {ISSUE-SHORT-ID} | {count} | {iso timestamp} |
- *Block quotes* — `> text`.
- *Links* — `[short text](https://example.com)` (standard Markdown). The legacy `<url|text>` mrkdwn form does NOT render in the markdown block.
- *Emoji* — `:emoji_name:` (colon-delimited) still works. Use status emojis sparingly to anchor scanning: :red_circle: critical, :large_yellow_circle: warning, :white_check_mark: ok, :hourglass_flowing_sand: timeout, :mag: investigating.
- *Mentions* — `<@USERID>`, `<#CHANNELID|name>`, `<!here>`. Only use when explicitly addressing someone; never fabricate IDs.
- *Horizontal rule* — `---` on its own line for visual section breaks.

Reference (for the agent's authors, not the agent itself):
- Slack Block Kit overview: https://docs.slack.dev/block-kit
- Slack formatting basics: https://slack.com/help/articles/202288908-Format-your-messages-in-Slack
"""


def _pydantic_model_name(model: str) -> str:
    if ":" in model:
        return model
    return f"anthropic:{model}"


def _model_settings(*, max_tokens: int, temperature: float = 0.0) -> ModelSettings:
    """Build ModelSettings with Anthropic prompt caching enabled when we're
    routing through Anthropic. Caches the system prompt + tool definitions
    (both static across calls), which on a multi-turn investigation cuts the
    input-token bill by ~90% on cache hits. The cache flags are silently
    ignored by non-Anthropic providers (Bedrock/MiniMax etc.)."""
    return AnthropicModelSettings(
        max_tokens=max_tokens,
        temperature=temperature,
        anthropic_cache_instructions="5m",
        anthropic_cache_tool_definitions="5m",
    )


def _prompt_with_context(user_text: str, slack_context: dict[str, object] | None) -> str:
    if not slack_context:
        return user_text
    return (
        f"{user_text}\n\nSlack event context:\n"
        f"{json.dumps(slack_context, indent=2, sort_keys=True)}"
    )


QUICK_ACK_INSTRUCTIONS = (
    "Produce ONE short Slack-mrkdwn line acknowledging the request you're "
    "about to handle. Lead with the :mag: emoji. Be specific: name the thing "
    "you're going to look into (service, error type, endpoint, branch, file, "
    "or whatever the user asked about) so they see you understood. Under 20 "
    "words. Do not ask questions or propose actions."
)


async def quick_ack(alert_text: str, *, model: str | None = None) -> str:
    """Single-shot, no-tools model call producing a context-aware Slack ack.

    Falls back to a hardcoded line if the model call fails -- the alert must
    still be acknowledged even if the model is having a bad day.
    """
    fallback = ":mag: Investigating this alert with Coral..."
    if not alert_text:
        return fallback
    model_name = model or os.getenv("SRE_AGENT_MODEL") or os.getenv("ANTHROPIC_MODEL") or DEFAULT_MODEL
    try:
        agent = Agent(
            _pydantic_model_name(model_name),
            instructions=QUICK_ACK_INSTRUCTIONS,
            model_settings=_model_settings(max_tokens=4000),
        )
        result = await agent.run(alert_text)
        text = str(result.output).strip()
        return text or fallback
    except Exception:
        return fallback


def _exception_chain_text(exc: BaseException) -> str:
    messages: list[str] = []
    current: BaseException | None = exc
    while current is not None:
        message = str(current).strip()
        if message and message not in messages:
            messages.append(message)
        current = current.__cause__ or current.__context__
    return " | ".join(messages)


class PydanticSreAgent:
    def __init__(
        self,
        *,
        coral_client: CoralMcpClient | None = None,
        model: str | None = None,
        max_tool_rounds: int = 100,
    ):
        self.coral = coral_client or CoralMcpClient()
        # SRE_AGENT_MODEL is the canonical override; ANTHROPIC_MODEL is kept
        # for backward compatibility with older deployments.
        self.model = (
            model
            or os.getenv("SRE_AGENT_MODEL")
            or os.getenv("ANTHROPIC_MODEL")
            or DEFAULT_MODEL
        )
        self.max_tool_rounds = max_tool_rounds

    def _build_agent(self, *, event_stream_handler=None) -> Agent:
        coral_server = MCPServerStdio(
            self.coral.coral_bin,
            args=self.coral.mcp_args,
            env=load_coral_env(),
            timeout=10,
            include_instructions=True,
            max_retries=50,
        )
        return Agent(
            _pydantic_model_name(self.model),
            instructions=SYSTEM_PROMPT,
            toolsets=[coral_server],
            model_settings=_model_settings(max_tokens=MAX_OUTPUT_TOKENS),
            event_stream_handler=event_stream_handler,
        )

    async def answer(
        self,
        user_text: str,
        *,
        slack_context: dict[str, object] | None = None,
        event_stream_handler=None,
        message_history: list[ModelMessage] | None = None,
    ) -> str:
        agent = self._build_agent(event_stream_handler=event_stream_handler)
        prompt = _prompt_with_context(user_text, slack_context)
        try:
            async with agent:
                result = await agent.run(
                    prompt,
                    usage_limits=UsageLimits(request_limit=self.max_tool_rounds + 1),
                    message_history=message_history,
                )
        except UsageLimitExceeded:
            return (
                "I stopped after the configured tool-call budget. The available evidence was not "
                "enough for a final answer without risking overreach."
            )
        except UnexpectedModelBehavior as exc:
            return (
                "I hit a Coral MCP tool error before I could finish the investigation: "
                f"{_exception_chain_text(exc)}"
            )

        return str(result.output).strip() or "(No text response returned.)"
