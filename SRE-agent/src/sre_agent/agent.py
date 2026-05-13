from __future__ import annotations

import json
import os
from typing import Any

from anthropic import AsyncAnthropic

from sre_agent.coral_mcp import CoralMcpClient

SYSTEM_PROMPT = """You are a pedantic AI SRE assistant operating inside Slack.

Rules:
- Treat Datadog, Slack, GitHub, and Sentry as evidence sources. Use Coral MCP tools before making factual claims about incidents, alerts, deployments, errors, owners, or recent status.
- Prefer narrow read-only SQL queries. Add LIMIT clauses. Avoid broad scans unless the operator asks for them.
- Distinguish observations from hypotheses. Say "unknown" when the available evidence does not prove something.
- Do not claim to have paged, deployed, reverted, muted, acknowledged, resolved, or changed anything. This demo agent is read-only.
- When asked for an incident assessment, return: summary, evidence, likely causes, confidence, and next checks.
- Mention the source of important evidence, including table names and identifiers where useful.
"""


def _block_to_dict(block: Any) -> dict[str, Any]:
    if hasattr(block, "model_dump"):
        return block.model_dump(mode="json", exclude_none=True)
    if hasattr(block, "dict"):
        return block.dict(exclude_none=True)
    return dict(block)


def _extract_text(content: list[Any]) -> str:
    parts: list[str] = []
    for block in content:
        if getattr(block, "type", None) == "text":
            parts.append(getattr(block, "text", ""))
    return "\n".join(part for part in parts if part).strip()


class PedanticSreAgent:
    def __init__(
        self,
        *,
        anthropic_client: AsyncAnthropic | None = None,
        coral_client: CoralMcpClient | None = None,
        model: str | None = None,
        max_tool_rounds: int = 6,
    ):
        self.anthropic = anthropic_client or AsyncAnthropic()
        self.coral = coral_client or CoralMcpClient()
        self.model = model or os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
        self.max_tool_rounds = max_tool_rounds

    async def answer(self, user_text: str, *, slack_context: dict[str, Any] | None = None) -> str:
        tools = [tool.to_anthropic_tool() for tool in await self.coral.list_tools()]
        context_text = ""
        if slack_context:
            context_text = "\n\nSlack event context:\n" + json.dumps(
                slack_context, indent=2, sort_keys=True
            )

        messages: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": f"{user_text}{context_text}",
            }
        ]

        for _ in range(self.max_tool_rounds):
            response = await self.anthropic.messages.create(
                model=self.model,
                max_tokens=1800,
                system=SYSTEM_PROMPT,
                messages=messages,
                tools=tools,
            )

            messages.append(
                {
                    "role": "assistant",
                    "content": [_block_to_dict(block) for block in response.content],
                }
            )

            tool_uses = [block for block in response.content if getattr(block, "type", None) == "tool_use"]
            if not tool_uses:
                return _extract_text(response.content) or "(No text response returned.)"

            tool_results: list[dict[str, Any]] = []
            for tool_use in tool_uses:
                result_text = await self.coral.call_tool(tool_use.name, tool_use.input)
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_use.id,
                        "content": result_text[:20000],
                    }
                )

            messages.append({"role": "user", "content": tool_results})

        return (
            "I stopped after the configured tool-call budget. The available evidence was not "
            "enough for a final answer without risking overreach."
        )

