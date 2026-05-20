from __future__ import annotations

import json
import os

from pydantic_ai import Agent, ModelSettings
from pydantic_ai.exceptions import UnexpectedModelBehavior, UsageLimitExceeded
from pydantic_ai.mcp import MCPServerStdio
from pydantic_ai.usage import UsageLimits

from sre_agent.coral_mcp import CoralMcpClient, load_coral_env

DEFAULT_MODEL = "claude-sonnet-4-6"
MAX_OUTPUT_TOKENS = 1800

SYSTEM_PROMPT = """You are a Pydantic AI SRE assistant operating inside Slack.

Rules:
- Treat Datadog, Slack, GitHub, and Sentry as evidence sources. Use Coral MCP tools before making factual claims about incidents, alerts, deployments, errors, owners, or recent status.
- Prefer narrow read-only SQL queries. Add LIMIT clauses. Avoid broad scans unless the operator asks for them.
- Distinguish observations from hypotheses. Say "unknown" when the available evidence does not prove something.
- Do not claim to have paged, deployed, reverted, muted, acknowledged, resolved, or changed anything. This demo agent is read-only.
- When asked for an incident assessment, return: summary, evidence, likely causes, confidence, and next checks.
- Mention the source of important evidence, including table names and identifiers where useful.

Response style — write for Slack, not a doc:
- Keep it short. Aim for under 150 words unless the user explicitly asks for depth.
- Lead with the answer. No "Let me check…" preamble; no recap of the question.
- Use Slack mrkdwn, not standard Markdown: *bold* (single asterisks), _italic_, `code`, ```code blocks```, > quotes, `-` bullets. Do NOT use `#` / `##` headers — Slack renders them literally. Use *bold labels* for section breaks instead.
- Flat bullet lists only; Slack mangles nested lists.
- Links: `<https://example.com|link text>`.
- Use status emojis sparingly to draw the eye: :red_circle: critical, :large_yellow_circle: warning, :white_check_mark: ok.
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
        max_tool_rounds: int = 30,
    ):
        self.coral = coral_client or CoralMcpClient()
        self.model = model or os.getenv("ANTHROPIC_MODEL", DEFAULT_MODEL)
        self.max_tool_rounds = max_tool_rounds

    def _build_agent(self, *, event_stream_handler=None) -> Agent:
        coral_server = MCPServerStdio(
            self.coral.coral_bin,
            args=self.coral.mcp_args,
            env=load_coral_env(),
            timeout=10,
            include_instructions=True,
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
    ) -> str:
        agent = self._build_agent(event_stream_handler=event_stream_handler)
        prompt = _prompt_with_context(user_text, slack_context)
        try:
            async with agent:
                result = await agent.run(
                    prompt,
                    usage_limits=UsageLimits(request_limit=self.max_tool_rounds + 1),
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
