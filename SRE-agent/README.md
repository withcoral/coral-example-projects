# AI SRE Agent with Claude and Coral

A read-only SRE agent that investigates production incidents end-to-end using
**Coral MCP** for evidence (Datadog, Sentry, GitHub, Slack), **Pydantic AI**
for orchestration, and any tool-using LLM (Claude, MiniMax, DeepSeek, Qwen…)
for reasoning.

The agent does not page, deploy, mutate, or write — it only queries and
reports back.

## Two ways to run

There are two supported entry points to the same underlying agent. Pick the
one that matches what you're building.

|  | Route A — Local agent | Route B — Slack bot |
|---|---|---|
| **Use case** | One-off CLI / notebook investigations, or wire Coral into Claude Code as an MCP server | Team-facing autonomous on-call assistant that auto-investigates Datadog alerts |
| **Setup time** | ~10 minutes | ~45 minutes (Slack app + optional Datadog wiring + k8s deploy) |
| **Runs where** | Your terminal or Jupyter | Single-replica Kubernetes Deployment |
| **Entry points** | `coral-sre-agent ask "..."` CLI, the [walkthrough notebook](notebooks/local_sre_agent.ipynb) | `#alerts` auto-investigation, `@`-mentions in channels, DMs |
| **External services** | Anthropic (default) + the Coral sources you've configured | Above + Slack workspace + optional Datadog Slack integration |

Both routes share the same `PydanticSreAgent` (`src/sre_agent/core/agent.py`)
and the same Coral MCP wiring. The Slack bot is just the local agent
wrapped in a Bolt app with a live Block Kit streaming UI.

## Prerequisites

Common to both routes:

- **Python 3.12+** and **[uv](https://docs.astral.sh/uv/)**
- **Coral CLI** — `curl -fsSL https://withcoral.com/install.sh | sh` (macOS: `brew install withcoral/tap/coral`)
- API keys for the data sources you want to wire up:
  **Datadog** (API key + Application key), **GitHub** (PAT), **Sentry**
  (auth token), **Slack** (bot token — Coral can read Slack channels too)
- A model API key. The default is **Anthropic** (`ANTHROPIC_API_KEY`). Any
  pydantic-ai-supported model with tool-use works — see [Model selection](#model-selection).

Only Route B needs:

- A **Slack workspace** you can admin (to create the Slack app and install it)
- For the deployed mode: **Docker**, an image registry, and a **Kubernetes cluster**

## Common setup

### 1. Clone and install

```bash
git clone https://github.com/withcoral/coral-example-projects.git
cd coral-example-projects/SRE-agent
./scripts/bootstrap.sh       # uv sync, copies .env.example -> .env, writes .mcp.json
```

### 2. Fill in `.env`

Open `.env` and set at minimum:

```bash
SRE_AGENT_MODEL=anthropic:claude-opus-4-7   # default; see "Model selection" below
ANTHROPIC_API_KEY=sk-ant-...

# Data sources — fill in only what you have. Each is optional.
DD_API_KEY=...                              # Datadog API key
DD_APPLICATION_KEY=...                      # Datadog Application key (Coral needs BOTH)
DD_SITE=datadoghq.com                       # or datadoghq.eu, us3.datadoghq.com, ...
GITHUB_TOKEN=...                            # GitHub PAT
SENTRY_TOKEN=...                            # Sentry auth token
SENTRY_ORG=...                              # Sentry org slug
```

Route B (Slack bot) adds `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, and optionally
`ALERTS_CHANNEL_ID` + `DATADOG_SLACK_APP_ID` for the auto-investigation
handler. See [Route B](#route-b--slack-bot) below.

### 3. Register Coral sources

```bash
./scripts/configure_coral.sh
```

This runs `coral source add` for each provider whose credentials are present,
then runs a metadata smoke query so you can see what's queryable.

> **Gotcha:** Datadog needs **both** `DD_API_KEY` and `DD_APPLICATION_KEY`. With
> only the API key, `coral source add datadog` fails.

That's enough setup to run Route A. Skip to it if you don't need the Slack bot.

---

## Route A — Local agent

Run the agent as a CLI or step through it interactively in a notebook.

### CLI

```bash
./scripts/run_agent.sh doctor                                              # smoke-test Coral MCP
./scripts/run_agent.sh ask "What SRE data sources can you see through Coral?"
./scripts/run_agent.sh ask "Datadog monitors firing in the last hour?"
./scripts/run_agent.sh ask "List Sentry issues from the last 24h with >100 events"
```

Each call constructs a fresh `PydanticSreAgent`, registers Coral MCP as a
toolset, and returns a structured assessment with evidence + cited sources.

### Notebook walkthrough

For a guided tour of the same agent — install Coral, paste credentials,
register sources, then build the agent step-by-step with the system prompt
visible — open:

```
notebooks/local_sre_agent.ipynb
```

Useful when you want to see exactly what's happening at each layer
(`coral source add` → `coral sql ...` → `Agent(...)` → tool dispatch).

### Use Coral as an MCP server in your own coding agent

Coral exposes a stdio MCP server, so any MCP-over-stdio client can call its
`sql` and `list_tables` tools without going through this project. We ship a
`.mcp.json` and a wrapper script that loads `.env` for you:

```bash
./scripts/write_mcp_config.py     # writes .mcp.json -> ./scripts/start_coral_mcp.sh
```

**Claude Code** — restart from this directory and run `/mcp` to verify. Or
configure the server through the CLI:

```bash
claude mcp add coral --scope project -- coral mcp-stdio
```

**Codex CLI / other MCP clients** — edit your client's config to point at the
absolute path of `scripts/start_coral_mcp.sh` (it shells in `.env` for you).

---

## Route B — Slack bot

When a Datadog monitor fires into `#alerts`, the bot:

1. Posts a contextual quick-ack within ~1 second (`:mag: Looking into hello-service — 3 exceptions in last 5m`).
2. Streams a live Block Kit **plan** block — each Coral MCP tool call shows as
   a task with `in_progress` / `complete` / `error` status.
3. Posts the final structured assessment when done — Summary / Evidence /
   Likely cause / Blast radius / What changed / Mitigation / Sources — with
   native Slack `header`, GFM `table`, fenced code, and one URL button per
   source.
4. Footers each reply with model name, tool-call count, and wall-clock
   duration.

The same flow runs on `@`-mentions in any channel (with thread history
fetched for follow-ups) and on DMs.

### 1. Create the Slack app

Most-fiddly step — do every sub-step. At <https://api.slack.com/apps>
→ **Create New App** → **From scratch**.

1. **Socket Mode** → toggle on. (No public URL is ever needed.)
2. **Agents & AI Apps** → enable. This unlocks the Assistant pane — suggested
   prompts and the live "investigating…" status line.
3. **OAuth & Permissions → Bot Token Scopes** — add:
   `app_mentions:read`, `assistant:write`, `chat:write`, `channels:history`,
   `channels:read`, `groups:history`, `groups:read`, `im:history`, `im:read`,
   `im:write`, `users:read`.
4. **Event Subscriptions** → enable → *Subscribe to bot events*:
   `app_mention`, `message.im`, `message.channels`, `assistant_thread_started`.
5. **App Home** → enable the **Messages Tab** and check *"Allow users to send
   Slash commands and messages from the messages tab"* (so DMs work).
6. **Basic Information → App-Level Tokens** → generate a token with the
   `connections:write` scope — this is `SLACK_APP_TOKEN` (`xapp-…`).
7. **Install App** to your workspace. Then under **OAuth & Permissions** copy
   the **Bot User OAuth Token** (`xoxb-…`) — this is `SLACK_BOT_TOKEN`.

Add both to `.env`. Invite the bot to your alerts channel:

```
/invite @your-bot
```

> **Gotcha:** use the **Bot User OAuth Token** (`xoxb-…`), *not* the **User
> OAuth Token** (`xoxp-…`). The user token authenticates as *you* and the bot
> will silently never receive events. Whenever you change scopes, **Reinstall**
> the app — that rotates `SLACK_BOT_TOKEN`, so update `.env`.

### 2. Run locally (Socket Mode)

```bash
./scripts/run_slackbot.sh        # blocks; Ctrl-C to stop
```

DM the bot, or `@`-mention it in any channel it's in. Only run **one**
instance at a time — two on the same `SLACK_APP_TOKEN` double-process every
event.

That's the minimum end-to-end for Route B. The next sections take it from
"running on my laptop" to "running 24/7 in your cluster, auto-investigating
real alerts."

### 3. Containerize and deploy to Kubernetes

The bot uses Slack **Socket Mode** — outbound WebSocket, no inbound traffic —
so the Deployment needs **no Service or Ingress** and must run **exactly one
replica** (multiple connections would double-process every event).

```bash
# Build for the cluster's node arch (x86_64 here).
docker build --platform linux/amd64 -t coral-sre-agent:<git-sha> .

# Push to whatever registry your cluster pulls from (Amazon ECR shown).
aws ecr create-repository --repository-name coral-sre-agent --region <region>
aws ecr get-login-password --region <region> | docker login --username AWS \
  --password-stdin <account>.dkr.ecr.<region>.amazonaws.com
docker tag  coral-sre-agent:<git-sha> <account>.dkr.ecr.<region>.amazonaws.com/coral-sre-agent:<git-sha>
docker push <account>.dkr.ecr.<region>.amazonaws.com/coral-sre-agent:<git-sha>
```

> **Gotcha:** build for **`linux/amd64`** if your nodes are x86_64. An arm64
> image (e.g. built on Apple Silicon) crash-loops with `exec format error`.

Apply the manifests:

```bash
kubectl apply -f deploy/namespace.yaml

# Create the Secret from your .env (it is NOT committed):
kubectl create secret generic sre-agent-secrets -n coral-demos \
  --from-literal=ANTHROPIC_API_KEY=... \
  --from-literal=SLACK_BOT_TOKEN=...   --from-literal=SLACK_APP_TOKEN=... \
  --from-literal=DD_API_KEY=...        --from-literal=DD_APPLICATION_KEY=... \
  --from-literal=DD_SITE=datadoghq.com --from-literal=GITHUB_TOKEN=... \
  --from-literal=SENTRY_TOKEN=...      --from-literal=SENTRY_ORG=...
# (see deploy/secret.example.yaml for the full key list)

# Edit deploy/deployment.yaml — replace <YOUR_REGISTRY> with your pushed image.
# Pin by digest (...coral-sre-agent@sha256:...) for cache-proof rollouts.
kubectl apply -f deploy/deployment.yaml

# Verify
kubectl rollout status deployment/sre-agent -n coral-demos
kubectl logs deployment/sre-agent -n coral-demos | grep "Bolt app is running"
```

DM or `@`-mention the bot — a reply confirms the full path
(Slack → agent → Coral → Claude → Slack) works end-to-end.

### 4. (Optional) Wire Datadog → Slack for auto-investigation

Connect Datadog's Slack app so monitor alerts post into `#alerts`. The bot
listens for those messages and replies in-thread with an investigation.

Authoritative reference: <https://docs.datadoghq.com/integrations/slack/>.
Summary of what to do:

1. **In Slack** — install the **Datadog** app from the workspace app
   directory (admin rights required).
2. **In Datadog** — Integrations → Slack → Configuration → **+ Add Account**.
   OAuth into your workspace. Use the URL for your Datadog site
   (`datadoghq.com`, `datadoghq.eu`, `us3.datadoghq.com`, …) and make sure
   `DD_SITE` in `.env` and the k8s Secret match.
3. **Add `#alerts`** to the workspace's account in Datadog's Configuration
   tab. Pick an **Account name** like `alerts` so monitor messages can
   target `@slack-alerts`.
4. **Invite the Datadog bot** in Slack: `/invite @Datadog` in `#alerts`.
5. **Test** — Datadog → Slack integration → click *Test* next to the channel.
   A test message should land in `#alerts` within seconds.

Then capture `DATADOG_SLACK_APP_ID` (the `A0XXXXXX` ID of Datadog's Slack
app in your workspace — visible in the bot's incoming event logs, or via
Slack's `conversations.history` API) and `ALERTS_CHANNEL_ID` (the Slack
channel ID), and add both to your k8s Secret:

```bash
kubectl -n coral-demos patch secret sre-agent-secrets --type=merge -p \
  '{"stringData":{"ALERTS_CHANNEL_ID":"C0XXXXXX","DATADOG_SLACK_APP_ID":"A0XXXXXX"}}'
kubectl -n coral-demos rollout restart deployment/sre-agent
```

With both set, the next real alert posted into `#alerts` triggers an
investigation reply in-thread.

### 5. (Optional) Demo target: `hello-service`

A bare counter is enough to wire the pipes, but the agent has nothing
*interesting* to investigate. `demo-app/` is a tiny FastAPI app with a
deliberate, plausible bug — happy-path code that wasn't tested against
unknown input:

```python
@app.get("/greet")
def greet(name: str = "alice"):
    display = USERS.get(name)        # returns None for unknown names
    return {"message": f"Hello, {display.upper()}!"}  # AttributeError on None
```

When `/greet?name=dave` hits the pod:

1. Handler raises `AttributeError: 'NoneType' object has no attribute 'upper'`.
2. The Sentry SDK captures it with a full traceback.
3. App middleware pushes a `hello_service.errors` counter to Datadog,
   tagged `service:hello-service exception:AttributeError`.
4. A Datadog monitor watching that counter crosses threshold, posts to
   `#alerts`, the SRE agent investigates in-thread.

To wire it up:

1. In Sentry, create a **Python / FastAPI** project called `hello-service`,
   copy its DSN into `.env` as `SENTRY_DSN`.
2. Build the image (`cd demo-app && docker build --platform linux/amd64 ...`),
   push it, point `deploy/hello-service.yaml` at the digest, apply.
3. Create the `hello-service-secrets` Secret (`SENTRY_DSN`, `DD_API_KEY`,
   `DD_SITE`) — same out-of-band pattern as `sre-agent-secrets`.
4. In Datadog, create a Metric monitor on `sum:hello_service.errors`, group by
   `service`, threshold `above 5`, message addresses `@slack-<your-account>`.

Trigger it:

```bash
scripts/demo_trigger_alert.sh        # default: 30 bad-greet requests, all 500
```

Within ~1-2 minutes you'll see Sentry capture the issue, Datadog flip the
monitor, Datadog post to `#alerts`, the bot reply in-thread.

---

## Customize the agent for your stack

This repo ships configured for the demo `hello-service`. Edits to make it
useful against your stack:

- **`src/sre_agent/slack/bot.py` → `INVESTIGATION_CONTEXT`** — rewrite the
  whole string to describe your service: Datadog metric/monitor names, the
  Sentry org+project slug, the GitHub repo+path, and the URL templates the
  agent cites as sources. The comment block right above the constant flags
  this as a fork-me edit. **Without it, the agent will be told to look at
  the demo Sentry/GitHub paths, which it can't see — producing nonsense
  investigations.**
- **`deploy/deployment.yaml` and `deploy/hello-service.yaml`** — replace the
  `<YOUR_REGISTRY>/...` placeholders with your real image refs.
- **`coral-demos` namespace** — rename across `deploy/*.yaml` and
  `scripts/demo_trigger_alert.sh` if a different namespace fits your cluster.
- **`SUGGESTED_PROMPTS` in `src/sre_agent/slack/bot.py`** — the Assistant
  pane's first-DM suggestions. Tailor to your team's common asks.

## How a Slack reply is rendered

All three Slack entry points (alert, `@`-mention, DM) flow through
`run_streamed_investigation` in `slack/streaming.py`. Same shape every time:

1. **Contextual quick-ack as the plan title** — a `:mag:` one-liner from a
   fast no-tools model call (~1s), pushed as the first `PlanUpdateChunk`.
2. **Live plan block** — `chat.startStream` opens in
   `task_display_mode='plan'`. Each Coral MCP tool call becomes a
   `TaskUpdateChunk` keyed by `tool_call_id`: `in_progress` on the call event,
   `complete` (with one-line output) on `ToolReturnPart`, `error` on
   `RetryPromptPart`. Same `task_id` patches in place — no UI collapse.
3. **Stream close + final reply** — `chat.stopStream` closes the plan, then
   a separate threaded `chat.postMessage` carries the full assessment:
   - `markdown` block (GFM — `## headers`, `tables`, fenced code, inline
     `[link](url)`)
   - `actions` block with **one URL button per source** (Datadog monitor,
     Sentry issue, GitHub file/commit)
   - `context` footer like
     `:robot_face: claude-opus-4-7 · :wrench: 22 Coral queries · :stopwatch: 2m 4s`

> **Streaming-API gotchas:** `chat.startStream` requires both
> `recipient_team_id` (cached from `auth.test`) and `recipient_user_id` (from
> `event.user`). Mixing `markdown_text` with `chunks` on `startStream`
> silently flips the stream to TEXT mode and breaks subsequent appends with
> `streaming_mode_mismatch`. GFM tables only render with a blank line before
> the header row — `_ensure_table_spacing` injects one if the model forgets.

### Follow-ups

`@`-mention the bot inside any thread it's already engaged with. The handler
fetches prior turns via `conversations.replies`, converts them to
pydantic-ai `message_history`, and runs the agent with that context — so
*"which branch is the bug on?"* picks up the original alert, the bot's
previous assessment, and any human discussion in between. Auto-replies
without an `@`-mention are deliberately off so the bot doesn't interject in
human-to-human discussion.

## Model selection

`SRE_AGENT_MODEL` accepts any pydantic-ai model string. The default is
`anthropic:claude-opus-4-7` for reliability; the agent is model-agnostic and
works with anything that supports tool use.

| Provider | Example value | Notes |
|---|---|---|
| Anthropic (default) | `anthropic:claude-opus-4-7`, `anthropic:claude-sonnet-4-6` | Most reliable, has prompt caching enabled |
| AWS Bedrock | `bedrock:minimax.minimax-m2.5`, `bedrock:anthropic.claude-3-5-sonnet-20241022-v2:0` | Needs `AWS_REGION` + `AWS_DEFAULT_REGION` + `AWS_BEARER_TOKEN_BEDROCK` |
| OpenRouter | `openrouter:deepseek/deepseek-v4-pro`, `openrouter:qwen/qwen3.7-max` | Needs `OPENROUTER_API_KEY`; access to MiniMax / DeepSeek / Qwen and many others |

`MAX_OUTPUT_TOKENS` (in `core/agent.py`) is 32k by default — enough headroom
for reasoning-style models like MiniMax M2.7 that emit a long internal
chain-of-thought before the final answer.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Bot never receives messages | Wrong token (`xoxp-` user token instead of `xoxb-` bot), or missing event subscriptions / scopes. Reinstall the app. |
| "Sending messages to this app has been turned off" | App Home → enable the Messages Tab + allow user messages. |
| Pod `CrashLoopBackOff`, `exec format error` | Image arch mismatch — rebuild `--platform linux/amd64`. |
| `coral source add datadog` fails | `DD_APPLICATION_KEY` is missing. Datadog needs **both** keys. |
| `missing_scope` querying `slack.channels` | Bot lacks `channels:read` / `channels:history`. Add scopes and reinstall. |
| `streaming_mode_mismatch` on `chat.appendStream` | `chat.startStream` was called with both `markdown_text` and `chunks` — drop the `markdown_text`. |
| `msg_too_long` on `chat.stopStream` | The assessment exceeded the stopStream blocks limit. Already handled — we post the final reply via a separate `chat.postMessage`. |

## Safety

- Keep `.env`, secrets, and customer data out of git.
- The agent is **read-only** — it never pages, deploys, or resolves anything.
  Do not add write tools without an approval + audit story.
- Use scoped, least-privilege credentials for every provider.

## References

- Coral: <https://github.com/withcoral/coral>
- Pydantic AI: <https://ai.pydantic.dev/>
- Slack Bolt for Python (Socket Mode): <https://docs.slack.dev/tools/bolt-python/concepts/socket-mode>
- Slack AI apps / assistants: <https://docs.slack.dev/ai/>
- Datadog Slack integration: <https://docs.datadoghq.com/integrations/slack/>

## License

Apache 2.0 — see the [LICENSE](../LICENSE) at the repository root.
