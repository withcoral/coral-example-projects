# Spike: Claude Agent SDK as an alternative agent backend

Research spike. Evaluates Anthropic's **Claude Agent SDK** (formerly the Claude
Code SDK) as an alternative backend for the SRE Agent's investigation loop,
which currently runs on **Pydantic AI** (`src/sre_agent/agent.py`,
`PydanticSreAgent`).

## 1. What the Claude Agent SDK is

The Claude Agent SDK is Anthropic's official library for building autonomous
agents. It exposes the same agent loop, tool orchestration, and context
management that power Claude Code, programmable in **Python and TypeScript**.

What it adds over the raw Anthropic Messages API:

- **Agent loop built in.** With the raw Messages API you write the
  `while stop_reason == "tool_use"` loop and execute tools yourself (this is
  effectively what Pydantic AI does for us today). The Agent SDK runs that loop
  internally — you call `query()` / `ClaudeSDKClient` and iterate over a stream
  of messages until a final `ResultMessage`.
- **Built-in tools.** `Read`, `Write`, `Edit`, `Bash`, `Glob`, `Grep`,
  `WebSearch`, `WebFetch`, `Monitor`, `AskUserQuestion` — usable without
  implementing tool execution.
- **MCP support.** Native client for stdio, HTTP/SSE, and in-process ("SDK")
  MCP servers.
- **Permissions.** `allowed_tools` / `disallowed_tools`, `permission_mode`, and
  a `can_use_tool` callback to gate tool use.
- **Context management.** Automatic compaction, plus **tool search** (on by
  default) that withholds tool definitions from the context window and loads
  only what each turn needs.
- **Sessions.** Persistent conversation state (JSONL on the local filesystem);
  resume and fork sessions across exchanges.
- **Hooks & subagents.** Lifecycle callbacks (`PreToolUse`, `PostToolUse`,
  `Stop`, ...) and delegated specialist agents via the `Agent` tool.

Architecturally the Python SDK is a thin wrapper over a bundled **Claude Code
CLI binary** — the SDK spawns and talks to that subprocess. Python 3.10+.

## 2. How integration would work

The Coral MCP server is a local CLI exposed over stdio (`coral mcp` /
`coral mcp-stdio`, see `coral_mcp.py`). The Agent SDK speaks stdio MCP natively,
so Coral wires in with no adapter:

```python
from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions, ResultMessage

options = ClaudeAgentOptions(
    model="claude-sonnet-4-6",
    system_prompt=SYSTEM_PROMPT,
    mcp_servers={
        "coral": {
            "command": coral_bin,        # from CoralMcpClient
            "args": mcp_args,            # ["mcp"] or ["mcp-stdio"]
            "env": load_coral_env(),
        }
    },
    allowed_tools=["mcp__coral__*"],     # gate to Coral tools only
    max_turns=6,                         # replaces UsageLimits(request_limit=...)
    # disallowed_tools / no Bash/Write/Edit -> keep the bot read-only
)

async with ClaudeSDKClient(options=options) as client:
    await client.query(prompt)
    async for message in client.receive_response():
        if isinstance(message, ResultMessage):
            answer = message.result
```

Notes for our long-running Slack bot:

- Coral tools must be explicitly allowed (`mcp__coral__*`); MCP tools are not
  auto-approved by permission modes.
- For a read-only bot, do **not** allow `Bash`/`Write`/`Edit`; restrict `tools`
  to Coral plus optionally `WebSearch`. The current Pydantic AI agent only has
  Coral, so this keeps parity.
- `slackbot.py` calls `asyncio.run(...)` per Slack event today. The same
  per-request pattern works with `query()`; `ClaudeSDKClient` could instead be
  held open per Slack thread to reuse session context, but that adds lifecycle
  management to the bot.
- The SDK launches the bundled Claude Code subprocess. Embedding it means the
  bot host needs that binary plus Node-free Python install (`pip install
  claude-agent-sdk` bundles the CLI). The 60s default MCP connect timeout
  applies to Coral startup.

## 3. Pros / cons vs the current Pydantic AI loop

### Pros of the Claude Agent SDK

- Less loop code to own: drops our manual `UsageLimits` / retry / exception
  plumbing for Anthropic-maintained orchestration.
- Built-in **context compaction + tool search** — useful if Coral exposes many
  tables/tools and an investigation runs long.
- First-class **hooks** for audit logging every Coral tool call (a natural fit
  for a "show your evidence" SRE bot) and **subagents** for splitting
  investigation steps.
- Same vendor as the model; new Claude Code capabilities land here first.

### Cons / risks

- **Anthropic-only.** The SDK is locked to Claude. Pydantic AI is
  model-agnostic (`anthropic:` prefix today, but swappable). Loses provider
  optionality.
- **Heavier runtime.** Ships and spawns a Claude Code CLI subprocess rather
  than being a pure library — more moving parts to deploy and monitor in a
  long-lived Slack bot than Pydantic AI + `mcp` over stdio.
- **Code-centric design.** The SDK is built for filesystem/coding agents
  (`Read`/`Edit`/`Bash`, `cwd`, `.claude/` config discovery). Our use case is
  pure read-only data investigation; much of the surface area is unused, and
  `setting_sources` must be constrained so the bot does not pick up stray
  `.claude/` or `CLAUDE.md` config from the host.
- **Migration cost** for a working MVP: rewrite `PydanticSreAgent`, re-map
  error handling (`UsageLimitExceeded` → `max_turns`, `UnexpectedModelBehavior`
  → `ResultMessage` error subtypes), and re-validate read-only guarantees.
- Pydantic AI gives **typed/structured outputs** and tighter Python-native
  ergonomics; the Agent SDK streams loosely-typed message objects.

### Cost

Both bill the **same per-token Messages API rates** — the SDK is not cheaper or
more expensive per token (Sonnet 4.6 ≈ $3/$15 per M in/out as of May 2026).
Practical cost differences are second-order:

- Tool search / compaction can *reduce* tokens on long investigations by not
  sending every tool definition each turn.
- The SDK's larger default system prompt and agent scaffolding can *add* tokens
  versus our lean Pydantic setup unless trimmed.
- `total_cost_usd` from the SDK is a client-side estimate, not authoritative.
- Note: from **June 15, 2026** Agent SDK usage on Claude *subscription* plans
  draws from a separate monthly Agent SDK credit. This bot uses an
  `ANTHROPIC_API_KEY` (standard API billing), so that change does not apply —
  but worth flagging if anyone runs it under a subscription.

## 4. Recommendation

**Stay on Pydantic AI for the current MVP. Do not migrate now.**

For this use case — an autonomous but **read-only, single-tool** (Coral MCP)
SRE investigation embedded in a long-running Slack bot — the Pydantic AI loop
is already a good fit: lightweight, model-agnostic, pure-library, and working.
The Claude Agent SDK's headline strengths (built-in filesystem/coding tools,
subagents, `.claude/` config, sessions) mostly do not apply here, and it adds a
bundled CLI subprocess and Anthropic lock-in for little concrete gain.

**Revisit the Agent SDK if** the investigation loop grows toward: many MCP
tools/tables where tool-search and compaction materially help; multi-step
investigations that benefit from subagents; or a need for built-in hooks-based
audit logging of every evidence query. At that point the SDK's orchestration
becomes worth the heavier runtime. Recommended next step: a small time-boxed
proof-of-concept wiring Coral via `mcp__coral__*` behind a feature flag, to
measure token cost and latency against the current loop before committing.

## Sources

- [Agent SDK overview — Claude docs](https://code.claude.com/docs/en/agent-sdk/overview)
- [Connect to external tools with MCP — Claude docs](https://code.claude.com/docs/en/agent-sdk/mcp)
- [Agent SDK reference (Python) — Claude docs](https://code.claude.com/docs/en/agent-sdk/python)
- [Track cost and usage — Claude docs](https://code.claude.com/docs/en/agent-sdk/cost-tracking)
- [claude-agent-sdk on PyPI](https://pypi.org/project/claude-agent-sdk/)
- [anthropics/claude-agent-sdk-python on GitHub](https://github.com/anthropics/claude-agent-sdk-python)
- [Claude API pricing](https://platform.claude.com/docs/en/about-claude/pricing)
