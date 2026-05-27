# AI SRE Slackbot with Claude and Coral

A working Slackbot that auto-investigates production incidents end-to-end.

When a Datadog monitor fires into `#alerts`, the bot:
1. Posts a contextual quick-ack within ~1 second (`:mag: Looking into hello-service — 3 exceptions in last 5m`).
2. Streams a live Block Kit `plan` block that shows each Coral MCP tool call as it runs (Datadog → Sentry → GitHub) with `in_progress` / `complete` / `error` status per task.
3. Posts a structured assessment when done — Summary / Evidence / Likely cause / Blast radius / What changed / Mitigation / Sources — with native Slack `header`, GFM `table`, fenced code, and one URL button per source.
4. Footers each reply with model, tool-call count, and wall-clock duration.

The same flow runs on `@`-mentions (with thread history fetched for follow-ups) and on DMs.

Built with:

- Slack Bolt for Python in Socket Mode (`chat.startStream` / `appendStream` / `stopStream` for the live plan UI)
- Pydantic AI (model selectable via `SRE_AGENT_MODEL` — default `bedrock:minimax.minimax-m2.5`, current deploy on `anthropic:claude-opus-4-7`)
- Coral MCP over stdio
- Coral sources for Datadog, Slack, GitHub, and Sentry

The bot is intentionally read-only — it queries Coral and reports back; it never pages, deploys, or mutates state.

## Quickstart

```bash
./scripts/bootstrap.sh
```

Edit `.env`, then configure Coral:

```bash
./scripts/configure_coral.sh
./scripts/run_agent.sh doctor
./scripts/run_agent.sh ask "What SRE data sources can you see through Coral?"
```

Run the Slackbot:

```bash
./scripts/run_slackbot.sh
```

## Slack App Setup

Create a Slack app for a workspace where you can test customer-style incident prompts.

1. Enable Socket Mode.
2. Create an app-level token with `connections:write`; put it in `SLACK_APP_TOKEN`.
3. Add a bot user and install the app.
4. Put the bot token in `SLACK_BOT_TOKEN`.
5. Subscribe to bot events: `app_mention` and `message.im`.
6. Add OAuth scopes needed by the demo: `app_mentions:read`, `chat:write`, `channels:read`, `channels:history`, `groups:read`, `groups:history`, `im:read`, `im:history`, and `users:read`.

The Coral Slack connector reads `SLACK_TOKEN`; the scripts set it from `SLACK_BOT_TOKEN` when `SLACK_TOKEN` is empty.

## Connect Claude Code to Coral MCP

The script writes `.mcp.json` to use `scripts/start_coral_mcp.sh`, which detects whether your installed Coral binary exposes `mcp` or `mcp-stdio`:

```bash
./scripts/write_mcp_config.py
```

You can also add the server through Claude Code:

```bash
claude mcp add coral --scope project -- coral mcp
```

If your Coral binary is older and exposes `mcp-stdio`:

```bash
claude mcp add coral --scope project -- coral mcp-stdio
```

Then run Claude Code from this directory and use `/mcp` to verify the Coral server is available.

## Guide

The full reproduce-from-zero walkthrough (Slack app, Coral, Anthropic/Bedrock, EKS deploy) is in [GUIDE.md](GUIDE.md).

## OSS model comparison

We benchmarked the Bedrock OSS catalog (MiniMax M2.5, Qwen 3 32B / Next 80B / Coder 30B, Mistral Devstral / Magistral) against the real SRE-agent investigation flow. The writeup — methodology, per-model observations, and the recommendation behind the default — is in [docs/oss-model-comparison.md](docs/oss-model-comparison.md).

## License

Apache 2.0 — see the [LICENSE](../LICENSE) at the repository root.
