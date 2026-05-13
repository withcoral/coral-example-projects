# How to Build an AI SRE Slackbot with Claude Code and Coral

This guide mirrors the implementation in this directory. Follow it end to end to build the demo bot customers can run locally.

## 1. Create the Project

```bash
mkdir coral-example-projects
cd coral-example-projects
mkdir SRE-agent
cd SRE-agent
```

Add a Python package with Slack Bolt, Pydantic AI, and the MCP Python SDK. This repo already includes that package in `src/sre_agent`.

## 2. Install Coral and Python Dependencies

Run:

```bash
./scripts/bootstrap.sh
```

The bootstrap script:

- Checks for `python3` and `coral`.
- Creates `.env` from `.env.example`.
- Installs the Python package with `uv` when available, otherwise with `venv` and `pip`.
- Writes `.mcp.json` for the local wrapper that starts your installed Coral MCP command.

## 3. Configure SRE Data Sources

Fill in `.env`:

```bash
ANTHROPIC_API_KEY=...
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
DD_API_KEY=...
DD_APP_KEY=...
DD_SITE=datadoghq.com
GITHUB_TOKEN=...
SENTRY_TOKEN=...
SENTRY_ORG=...
```

Then run:

```bash
./scripts/configure_coral.sh
```

The script installs or verifies these Coral sources:

- `datadog`
- `slack`
- `github`
- `sentry`

It supports both newer Coral CLIs with `coral connector ...` and older CLIs with `coral source ...`.

## 4. Start Coral in MCP Mode

Coral exposes MCP over stdio. Run this directly to check the server starts:

```bash
./scripts/start_coral_mcp.sh
```

This process waits for MCP client messages on stdin, so stop it with `Ctrl-C` after confirming it starts.

For Claude Code, use the generated `.mcp.json` or add Coral explicitly:

```bash
claude mcp add coral --scope project -- coral mcp
```

Older Coral binaries may use:

```bash
claude mcp add coral --scope project -- coral mcp-stdio
```

In Claude Code, run `/mcp` to confirm the `coral` server is connected. Depending on the Coral CLI version, the MCP tools may appear as names such as `sql` and `list_tables` or `SQL` and `list_providers`. Ask a question like:

```text
Using Coral, list the Datadog, Slack, GitHub, and Sentry tables available to the SRE agent.
```

## 5. Run the Pydantic SRE Agent Locally

The local CLI uses the same agent loop as the Slackbot:

```bash
./scripts/run_agent.sh doctor
./scripts/run_agent.sh ask "Check recent high-severity Sentry issues and related Datadog monitors. Be explicit about uncertainty."
```

The agent:

- Lists Coral MCP tools.
- Registers Coral MCP as a Pydantic AI toolset.
- Lets Pydantic AI handle model/tool orchestration against Claude.
- Returns a conservative answer with evidence, hypotheses, confidence, and next checks.

## 6. Run the Slackbot

In Slack, create an app with Socket Mode enabled.

Required pieces:

- App-level token with `connections:write`, stored as `SLACK_APP_TOKEN`.
- Bot token stored as `SLACK_BOT_TOKEN`.
- Bot event subscriptions: `app_mention` and `message.im`.
- Bot scopes: `app_mentions:read`, `chat:write`, `channels:read`, `channels:history`, `groups:read`, `groups:history`, `im:read`, `im:history`, and `users:read`.

Start the bot:

```bash
./scripts/run_slackbot.sh
```

Mention it in an incident channel:

```text
@coral-sre what changed around checkout-api errors in the last 30 minutes?
```

The bot replies in-thread and uses Coral-backed evidence before making claims.

## 7. Demo Prompts

Use prompts that show multi-source SRE investigation:

```text
@coral-sre summarize current Datadog monitor alerts and point me at likely owning services.
```

```text
@coral-sre compare recent Sentry issues with GitHub PRs merged today for the checkout repo.
```

```text
@coral-sre read this incident channel and identify unresolved questions, evidence, and next checks.
```

## 8. Notebook Option

Open:

```bash
jupyter lab notebooks/pydantic_sre_agent.ipynb
```

The notebook loads `.env`, checks Coral MCP, and runs the same Pydantic SRE agent against a prompt. Use it for guided customer demos when you want to show each step.

## 9. Safety Notes

- Keep `.env`, token files, screenshots with secrets, and customer data out of git.
- Keep this repository private or internal.
- The example agent is read-only. Do not add write tools until the approval and audit story is clear.
- Prefer scoped tokens and least-privilege provider credentials for customer demos.

## References

- Anthropic Claude Code MCP: https://docs.anthropic.com/en/docs/claude-code/mcp
- Pydantic AI MCP client: https://pydantic.dev/docs/ai/mcp/client/
- Pydantic AI Anthropic provider: https://pydantic.dev/docs/ai/models/anthropic/
- Slack Bolt Python Socket Mode: https://docs.slack.dev/tools/bolt-python/concepts/socket-mode
- Slack `app_mention` event: https://docs.slack.dev/reference/events/app_mention/
