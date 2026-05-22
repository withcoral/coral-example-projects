# Build & Deploy an AI SRE Agent with Coral and Claude

The canonical, reproduce-from-zero walkthrough for this project: a read-only
Slack bot that runs autonomous SRE investigations. It queries your
observability stack through **Coral**, reasons with **Claude** via **Pydantic
AI**, and ships as a single container on **Kubernetes**.

This guide is kept in sync with the implementation — it is updated alongside
the PRs that change behavior.

## What you're building

- A Slack bot that **auto-investigates Datadog alerts** in an `#alerts`
  channel, and answers **@-mentions** and **DMs** with evidence-backed
  assessments.
- Read-only by design — it queries data, never mutates.
- Slack **Socket Mode** (outbound WebSocket — no public endpoint, no ingress).
- Deployed as a single-replica Kubernetes Deployment.

## Prerequisites

- The **Coral CLI** — `curl -fsSL https://withcoral.com/install.sh | sh` (macOS: `brew install withcoral/tap/coral`)
- **Python 3.12+** and **uv**
- **Docker** and access to an image registry (this guide uses **Amazon ECR**)
- A **Kubernetes cluster** for the deploy step (this guide uses EKS)
- Accounts/keys: **Anthropic**, a **Slack workspace** you can admin, and the
  data sources you want — **Datadog**, **GitHub**, **Sentry**

## 1. Clone and install

```bash
git clone <repo> && cd coral-example-projects/SRE-agent
./scripts/bootstrap.sh   # installs the Python package; creates .env
```

## 2. Create the Slack app

This is the fiddliest part — do every sub-step. At <https://api.slack.com/apps>
→ **Create New App → From scratch**.

1. **Socket Mode** → toggle **on**. (Transport: the bot dials out to Slack, so
   no public URL is ever needed.)
2. **Agents & AI Apps** → **enable**. This unlocks the Assistant pane —
   suggested prompts and the live "investigating…" status line.
3. **OAuth & Permissions → Bot Token Scopes** — add:
   `app_mentions:read`, `assistant:write`, `chat:write`, `channels:history`,
   `channels:read`, `groups:history`, `groups:read`, `im:history`, `im:read`,
   `im:write`, `users:read`.
4. **Event Subscriptions** → **Enable Events** → *Subscribe to bot events*:
   `app_mention`, `message.im`, `message.channels`, `assistant_thread_started`.
   Click **Save Changes**.
5. **App Home** → enable the **Messages Tab** and check *"Allow users to send
   Slash commands and messages from the messages tab"* (so DMs work).
6. **Basic Information → App-Level Tokens** → generate a token with the
   `connections:write` scope. This is your **`SLACK_APP_TOKEN`** (`xapp-…`).
7. **Install App** to your workspace. Then, under **OAuth & Permissions**, copy
   the **Bot User OAuth Token** (`xoxb-…`) — this is your **`SLACK_BOT_TOKEN`**.

> **Gotcha:** use the **Bot User OAuth Token** (`xoxb-…`), *not* the **User
> OAuth Token** (`xoxp-…`). The user token authenticates as *you* and the bot
> will silently never receive events. Whenever you change scopes you must
> **Reinstall** the app, which **rotates `SLACK_BOT_TOKEN`** — update `.env`.

Finally, invite the bot to your alerts channel: `/invite @your-bot` in
`#alerts`.

## 3. Fill in `.env`

```bash
ANTHROPIC_API_KEY=sk-ant-...
SLACK_BOT_TOKEN=xoxb-...          # Bot User OAuth Token
SLACK_APP_TOKEN=xapp-...          # App-Level Token (connections:write)
DD_API_KEY=...                    # Datadog API key
DD_APP_KEY=...                    # Datadog Application key (Coral requires BOTH)
DD_SITE=datadoghq.com
GITHUB_TOKEN=...
SENTRY_TOKEN=...
SENTRY_ORG=...
ALERTS_CHANNEL_ID=C...            # the #alerts channel ID (optional)
DATADOG_SLACK_APP_ID=A...         # Datadog's Slack app ID (optional)
```

`ALERTS_CHANNEL_ID` + `DATADOG_SLACK_APP_ID` gate the auto-investigation
handler — leave them blank and the bot still runs (mentions + DMs only).

## 4. Connect Coral data sources

`coral source add <provider>` reads credentials from the environment. Run
`./scripts/configure_coral.sh`, or step through `notebooks/pydantic_sre_agent.ipynb`
for an interactive walkthrough.

> **Gotcha:** the Datadog source needs **both** `DD_API_KEY` *and*
> `DD_APP_KEY` (Coral surfaces it as `DD_APPLICATION_KEY`). With only the API
> key, `coral source add datadog` fails.

## 5. Run locally

```bash
./scripts/run_agent.sh ask "What SRE data sources can you see through Coral?"
./scripts/run_slackbot.sh        # starts the Socket Mode bot (blocks)
```

DM the bot or `@`-mention it in a channel it's in. Only run **one** instance
at a time — two on the same `SLACK_APP_TOKEN` double-process every event.

The agent (`src/sre_agent/agent.py`) registers Coral MCP as a Pydantic AI
toolset and returns a conservative answer with evidence, hypotheses, and next
checks. The `#alerts` handler (`src/sre_agent/slackbot.py`) auto-investigates
messages posted by the Datadog Slack app and replies in-thread.

## 6. Containerize

```bash
docker build --platform linux/amd64 -t coral-sre-agent:<git-sha> .
```

> **Gotcha:** build for **`linux/amd64`** if your cluster nodes are x86_64.
> An arm64 image (e.g. built on Apple Silicon) crash-loops with
> `exec format error`.

The image installs the published Coral CLI and the Python app; it bakes in no
secrets. `scripts/docker-entrypoint.sh` registers Coral sources from the
environment at startup, then launches the bot.

## 7. Push to Amazon ECR

```bash
aws ecr create-repository --repository-name coral-sre-agent --region <region>
aws ecr get-login-password --region <region> \
  | docker login --username AWS --password-stdin <account>.dkr.ecr.<region>.amazonaws.com
docker tag  coral-sre-agent:<git-sha> <account>.dkr.ecr.<region>.amazonaws.com/coral-sre-agent:<git-sha>
docker push <account>.dkr.ecr.<region>.amazonaws.com/coral-sre-agent:<git-sha>
```

## 8. Deploy to Kubernetes

Manifests live in `deploy/`. The Deployment is **`replicas: 1`** — Socket Mode
requires exactly one connection — and needs **no Service or Ingress**.

```bash
kubectl apply -f deploy/namespace.yaml

# Create the Secret from your .env values (it is NOT committed):
kubectl create secret generic sre-agent-secrets -n coral-demos \
  --from-literal=ANTHROPIC_API_KEY=... --from-literal=SLACK_BOT_TOKEN=... \
  --from-literal=SLACK_APP_TOKEN=... # ...and the rest; see deploy/secret.example.yaml

# Point deploy/deployment.yaml at your pushed image, then:
kubectl apply -f deploy/deployment.yaml
```

Pin the image by **digest** (`...coral-sre-agent@sha256:...`) for a
cache-proof rollout — re-using a tag can leave a stale image on the node.

## 9. Verify

```bash
kubectl rollout status deployment/sre-agent -n coral-demos
kubectl get pods  -n coral-demos          # expect 1/1 Running
kubectl logs deployment/sre-agent -n coral-demos | grep "Bolt app is running"
```

Then DM the bot or `@`-mention it — a reply confirms the full path
(Slack → agent → Coral → Claude → Slack) works end to end.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Bot never receives messages | Wrong token (`xoxp-` user token instead of `xoxb-` bot token), or missing event subscriptions / scopes. Reinstall the app. |
| "Sending messages to this app has been turned off" | App Home → enable the Messages Tab + allow user messages. |
| Pod `CrashLoopBackOff`, `exec format error` | Image arch mismatch — rebuild `--platform linux/amd64`. |
| `coral source add datadog` fails | `DD_APP_KEY` (Datadog Application key) is missing. |
| `missing_scope` on `slack.channels` | Bot lacks `channels:read` / `channels:history` — add scopes and reinstall. |

## Safety notes

- Keep `.env`, tokens, and customer data out of git.
- The agent is **read-only** — it never pages, deploys, or resolves anything.
  Do not add write tools without an approval and audit story.
- Use scoped, least-privilege provider credentials.

## References

- Coral: <https://github.com/withcoral/coral>
- Pydantic AI: <https://ai.pydantic.dev/>
- Slack Bolt for Python (Socket Mode): <https://docs.slack.dev/tools/bolt-python/concepts/socket-mode>
- Slack AI apps / assistants: <https://docs.slack.dev/ai/>
