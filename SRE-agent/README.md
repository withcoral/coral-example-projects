# AI SRE Slackbot with Claude and Coral

This example is a functioning Slackbot for SRE investigations. It uses:

- Slack Bolt for Python in Socket Mode
- Anthropic Messages API tool use
- Coral MCP over stdio
- Coral sources for Datadog, Slack, GitHub, and Sentry

The bot is intentionally read-only. It answers Slack mentions and DMs by querying Coral, then returns a pedantic incident-style answer with evidence and uncertainty.

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

## Publish Privately

Initialize and review locally first:

```bash
git init
git add .
git commit -m "Add Coral AI SRE Slackbot example"
```

Create a private GitHub repository under the `coral` owner:

```bash
REMOTE=coral/coral-example-projects VISIBILITY=private ./SRE-agent/scripts/create_private_repo.sh
```

Use `VISIBILITY=internal` instead when the GitHub organization supports internal repositories.

## Guide

The publishable walkthrough is in [GUIDE.md](GUIDE.md).
