from __future__ import annotations

import json
import os

from pydantic_ai import Agent, ModelSettings
from pydantic_ai.exceptions import UnexpectedModelBehavior, UsageLimitExceeded
from pydantic_ai.mcp import MCPServerStdio
from pydantic_ai.messages import ModelMessage
from pydantic_ai.usage import UsageLimits

from sre_agent.coral_mcp import CoralMcpClient, load_coral_env

# Default model is MiniMax M2.5 routed through Bedrock — serverless on-demand,
# available in eu-west-1 alongside the rest of the demo infra. Override via the
# SRE_AGENT_MODEL env var (any pydantic-ai model string, e.g.
# `anthropic:claude-sonnet-4-6` or `bedrock:anthropic.claude-3-5-sonnet-20241022-v2:0`).
DEFAULT_MODEL = "bedrock:minimax.minimax-m2.5"
# MiniMax (and other reasoning-style models) charge reasoning tokens against
# `max_tokens`, so a tight cap silently kills the run before any user-visible
# output is generated. We set this very high (100k) to give the model
# unconstrained room for deep reasoning + a full structured incident
# assessment. Bedrock + MiniMax will cap further if the model has its own
# per-request limit; otherwise this just makes the budget effectively a non-issue.
MAX_OUTPUT_TOKENS = 100_000

SYSTEM_PROMPT = """You are a Pydantic AI SRE assistant operating inside Slack.

Operating principles:
- Treat Datadog, Slack, GitHub, and Sentry as evidence sources. Use Coral MCP tools before making factual claims about incidents, alerts, deployments, errors, owners, or recent status.
- Prefer narrow read-only SQL queries with LIMIT clauses. Avoid broad scans unless explicitly asked.
- Distinguish observations from hypotheses. Tag hypotheses with confidence (high/medium/low) and cite the evidence that supports or contradicts them.
- Say "unknown" when evidence is missing. Never fabricate IDs, counts, timestamps, file paths, or line numbers.
- Do not claim to have paged, deployed, reverted, muted, acknowledged, resolved, or changed anything. This agent is read-only.
- Cite sources of important evidence: Coral table names, record identifiers, timestamps, counts.

When the prompt describes an alert or incident, produce a structured assessment with these sections (use *bold* labels, not headers):

*Summary* — one line: what's broken, where, and the scope.
*Evidence* — flat bullet list. Each bullet names the Coral source/table and the specific finding (IDs, counts, timestamps).
*Likely cause* — hypothesis with confidence level. If the failure points to a code path (e.g. a Python exception with file:line in the stack trace), look up the file in GitHub via Coral (`github.commits`, `github.contents`, or related tables) and quote the offending line so the diagnosis is grounded in the actual source.
*Blast radius* — affected services, endpoints, user count if known. Call out absence of evidence too ("APM data not available", "no open incident").
*What changed* — recent commits, deploys, releases, or config changes that correlate with onset. If no signal, say so plainly and explain the gap (e.g. no Sentry release tag).
*Mitigation / next checks* — actionable bullets. Separate *immediate* (stop the bleeding) from *durable* (root-cause fix + prevention).

For casual questions outside an incident context, skip the structure and answer in under 100 words.

Response style — write for Slack, not a doc:
- Lead with the answer; no "Let me check…" preamble; no recap of the question.
- Use Slack mrkdwn, not standard Markdown: *bold* (single asterisks), _italic_, `code`, ```code blocks```, > quotes, `-` bullets. Do NOT use `#` / `##` headers — Slack renders them literally.
- Flat bullet lists only; Slack mangles nested lists.
- Links: `<https://example.com|link text>`.
- Status emojis sparingly: :red_circle: critical, :large_yellow_circle: warning, :white_check_mark: ok.
"""


def _pydantic_model_name(model: str) -> str:
    if ":" in model:
        return model
    return f"anthropic:{model}"


def _prompt_with_context(user_text: str, slack_context: dict[str, object] | None) -> str:
    if not slack_context:
        return user_text
    return (
        f"{user_text}\n\nSlack event context:\n"
        f"{json.dumps(slack_context, indent=2, sort_keys=True)}"
    )


QUICK_ACK_INSTRUCTIONS = (
    "Produce ONE short Slack-mrkdwn line acknowledging you're starting to "
    "investigate the given alert. Lead with the :mag: emoji. Reference the "
    "affected service and the apparent issue (error rate, exception type, "
    "endpoint, etc.) so the user sees the bot understood the alert. Under 20 "
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
            model_settings=ModelSettings(max_tokens=4000, temperature=0.0),
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
            model_settings=ModelSettings(max_tokens=MAX_OUTPUT_TOKENS, temperature=0.0),
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
