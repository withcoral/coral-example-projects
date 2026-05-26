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
# Model selection — defaults to bedrock:minimax.minimax-m2.5 (serverless on
# Bedrock). Override with any pydantic-ai model string, e.g.
# `anthropic:claude-sonnet-4-6`.
SRE_AGENT_MODEL=bedrock:minimax.minimax-m2.5

# Used when the model string routes through `bedrock:`. boto3 needs both env
# vars; AWS_REGION alone is not enough. The bearer token is a long-lived
# Bedrock API key (the `ABSK…` format), not a short-lived pre-signed URL.
AWS_REGION=eu-west-1
AWS_DEFAULT_REGION=eu-west-1
AWS_BEARER_TOKEN_BEDROCK=bedrock-api-key-...

# Used when the model string routes through `anthropic:` (or if you set
# ANTHROPIC_MODEL for back-compat). Skip if you only use Bedrock.
ANTHROPIC_API_KEY=sk-ant-...

SLACK_BOT_TOKEN=xoxb-...          # Bot User OAuth Token
SLACK_APP_TOKEN=xapp-...          # App-Level Token (connections:write)
DD_API_KEY=...                    # Datadog API key
DD_APPLICATION_KEY=...            # Datadog Application key (Coral needs BOTH)
DD_SITE=datadoghq.com
GITHUB_TOKEN=...
SENTRY_TOKEN=...
SENTRY_ORG=...
SENTRY_DSN=...                    # for the demo hello-service app
ALERTS_CHANNEL_ID=C...            # the #alerts channel ID (optional)
DATADOG_SLACK_APP_ID=A...         # Datadog's Slack app ID (optional)
```

`ALERTS_CHANNEL_ID` + `DATADOG_SLACK_APP_ID` gate the auto-investigation
handler — leave them blank and the bot still runs (mentions + DMs only).
Wiring them up is covered in §10.

> **Model swap.** The default is MiniMax M2.5 via Bedrock because it's
> serverless on-demand in `eu-west-1` alongside the rest of the demo infra.
> To use Anthropic directly instead, set `SRE_AGENT_MODEL=anthropic:claude-sonnet-4-6`
> and provide `ANTHROPIC_API_KEY`. The agent code is model-agnostic — any
> pydantic-ai-supported model that handles tool use will work.

## 4. Connect Coral data sources

`coral source add <provider>` reads credentials from the environment. Run
`./scripts/configure_coral.sh`, or step through `notebooks/pydantic_sre_agent.ipynb`
for an interactive walkthrough.

> **Gotcha:** the Datadog source needs **both** `DD_API_KEY` *and*
> `DD_APPLICATION_KEY`. With only the API key, `coral source add datadog` fails.

## 5. Run locally

```bash
./scripts/run_agent.sh ask "What SRE data sources can you see through Coral?"
./scripts/run_slackbot.sh        # starts the Socket Mode bot (blocks)
```

DM the bot or `@`-mention it in a channel it's in. Only run **one** instance
at a time — two on the same `SLACK_APP_TOKEN` double-process every event.

The agent (`src/sre_agent/agent.py`) registers Coral MCP as a Pydantic AI
toolset and returns a structured incident assessment with evidence,
hypotheses, mitigation steps, and links back to the originating systems.
The `#alerts` handler (`src/sre_agent/slackbot.py`) auto-investigates
messages posted by the Datadog Slack app and replies in-thread.

### How a reply is rendered

All three entry points — the `#alerts` auto-investigation, an `@`-mention in
any channel, and a DM to the bot — flow through the same helper
(`_run_streamed_investigation` in `slackbot.py`) and produce the same shape
of reply using Slack's AI-agent Block Kit streaming API.

1. *Contextual quick-ack as the plan title* — a `:mag:` one-liner generated
   by a fast no-tools model call (~1s). It's pushed as the first
   `PlanUpdateChunk` so the streaming message opens with a clear heading like
   `:mag: Looking into hello-service — 3 exceptions in the last 5m`.
2. *Live plan block* — `chat.startStream` opens in `task_display_mode='plan'`
   with `chunks=[PlanUpdateChunk(...)]` (chunks-only on start, NOT
   `markdown_text`; mixing the two silently puts the stream in text mode and
   subsequent chunk appends fail with `streaming_mode_mismatch`). Each Coral
   MCP tool call becomes a `TaskUpdateChunk` keyed by `tool_call_id`:
   - `in_progress` on the call event,
   - `complete` on `ToolReturnPart` (with `output` set to a one-line summary
     like `"3 rows"`),
   - `error` on `RetryPromptPart`.
   Same `task_id` patches in place — no UI collapse on update.
3. *Stream close + final reply* — `chat.stopStream` closes the plan
   (without final blocks; stopStream's `blocks=` hits `msg_too_long` for a
   5–10 kchar markdown body). Then a separate threaded `chat.postMessage`
   carries the full assessment:
   - `markdown` block (GitHub-flavored Markdown: `## headers`, GFM `tables`,
     fenced code with language hints, `[link](url)` syntax — Slack
     auto-parses into native `header` / `rich_text` / `divider` / `table`
     blocks on receive)
   - `header` + `actions` blocks with **one URL button per source** (Datadog
     monitor, Sentry issue, GitHub file/commit) so the operator can open
     the underlying system in one click. Source bullets in the model's
     `## Sources` section are parsed out of the markdown and rendered as
     these buttons.
   - `context` footer with model name, tool-call count, and wall-clock
     duration: `:robot_face: claude-opus-4-7 · :wrench: 22 Coral queries · :stopwatch: 2m 4s`

> **Slack streaming gotchas worth knowing:**
> - `chat.startStream` requires both `recipient_team_id` (cached from
>   `auth.test`) and `recipient_user_id` (from `event.user` — which is set
>   even on bot-posted Datadog alerts, where it's the Datadog bot's user id).
> - The `alert` block type isn't valid in `chat.postMessage` or
>   `chat.stopStream` — it's a modal/home-surface block only. The plan
>   title and the markdown body's `## Summary` carry the severity signal.
> - GFM tables only render with a blank line before the header row;
>   `_ensure_table_spacing` injects one if the model forgets.

### Follow-ups

`@`-mention the bot inside any thread the bot is engaged with. The handler
calls `conversations.replies` to fetch the prior turns, converts them to
pydantic-ai `message_history`, and runs the agent in context — so a
follow-up like *"@SRE Agent which branch is the bug on?"* picks up the
original alert, the bot's previous assessment, and any human discussion
that's happened in between. Auto-replies without an `@`-mention are
deliberately off (avoids the bot interjecting in human-to-human discussion).

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

## 10. Wire Datadog → Slack for auto-investigation

Optional but recommended for the SRE demo. This enables the third entry point
in `slackbot.py`: when the Datadog Slack app posts an alert into `#alerts`,
the agent replies in-thread with an investigation (likely cause, blast
radius, what changed, next checks).

Authoritative reference:
<https://docs.datadoghq.com/integrations/slack/?tab=datadogforslack#setup>.
Steps below summarise what we did for this project.

### 10a. Install the Datadog app in Slack

Slack → workspace name → **Settings & administration** → **Manage apps** →
search **Datadog** → **Add to Slack** and authorize. Workspace admin rights
required.

### 10b. Connect Slack from inside Datadog

In Datadog (EU example): `https://app.datadoghq.eu/integrations/slack`
(or **Integrations** → **Slack**) → **Configuration** tab → **+ Add Account**.
A Slack OAuth popup opens; pick the workspace and **Allow**. On return,
your workspace is listed in the Configuration tab.

> **Site matters.** Use the URL for your Datadog site — `datadoghq.com`,
> `datadoghq.eu`, `us3.datadoghq.com`, etc. — and make sure `DD_SITE` in
> `.env` and the k8s Secret match.

### 10c. Name the Slack account and add #alerts

Still on the Slack integration **Configuration** tab, under the workspace:

- **Account name** — a short handle that becomes the notification target,
  e.g. `alerts` makes `@slack-alerts` work in monitor messages.
- **+ Add Channel** → enter `#alerts` → **Save**.

### 10d. Invite the Datadog bot to #alerts

In Slack `#alerts`:

```
/invite @Datadog
```

Datadog can post into a channel only after its bot is a member.

### 10e. Send a test notification

In Datadog's Slack integration **Configuration** tab, next to the channel
entry, click **Test**. A test message should arrive in `#alerts` within
a few seconds.

Common reasons the test message doesn't show up:

| Symptom | Fix |
|---|---|
| Test silently does nothing | Bot not in channel — re-run `/invite @Datadog`. |
| Channel-name typo error | Datadog config has `#alerts` spelled differently from the actual channel. |
| Wrong workspace | OAuth picked the wrong workspace — remove and re-add. |

### 10f. Capture DATADOG_SLACK_APP_ID

The bot's auto-investigate handler only fires for messages whose `app_id`
matches Datadog's Slack app in this workspace. Get the value from the test
message:

1. In Slack `#alerts`, hover the test message → **More actions (⋯)** →
   **Copy link**, or click **View message source** if your workspace has it.
2. Easier path: read it from the Slack message metadata in the bot's logs
   (the message handler logs the incoming event), or query the Slack API
   `conversations.history` for the channel and look for `app_id` on the
   Datadog post — it has the form `A0XXXXXX`.

Add it to `.env`:

```bash
DATADOG_SLACK_APP_ID=A0XXXXXX
```

Patch the k8s Secret and restart the deployment:

```bash
kubectl -n coral-demos patch secret sre-agent-secrets \
  --type=merge -p '{"stringData":{"DATADOG_SLACK_APP_ID":"A0XXXXXX"}}'
kubectl -n coral-demos rollout restart deployment/sre-agent
```

With `ALERTS_CHANNEL_ID` + `DATADOG_SLACK_APP_ID` both set, the next real
Datadog alert posted to `#alerts` triggers an investigation reply in-thread.

## 11. Build a real demo target: `hello-service`

A canary metric is enough to prove the wire works, but the agent has nothing
*interesting* to investigate when it fires. To do an end-to-end SRE
investigation, the agent needs a real service, with real exceptions, in real
Sentry, that a real Datadog monitor can alert on.

`demo-app/` contains a tiny FastAPI app with a deliberate, plausible bug:

```python
@app.get("/greet")
def greet(name: str = "alice"):
    display = USERS.get(name)        # returns None for unknown names
    return {"message": f"Hello, {display.upper()}!"}  # AttributeError on None
```

It's the kind of bug everyone has shipped: happy-path code that wasn't tested
against unknown input. When `/greet?name=dave` hits the deployed pod:

1. Handler raises `AttributeError: 'NoneType' object has no attribute 'upper'`.
2. Sentry SDK captures the exception with a full Python stack trace.
3. App middleware pushes a `hello_service.errors` counter to Datadog,
   tagged `service:hello-service exception:AttributeError`.
4. A Datadog monitor watching that counter crosses threshold, posts to
   `#alerts`, the SRE agent investigates in-thread.

### 11a. Create the Sentry project

In a Sentry org isolated from your production data:

1. **Projects** → **Create Project** → choose **Python / FastAPI**.
2. Name it `hello-service`.
3. From the project's Getting Started page (or **Settings → [project] → Client
   Keys (DSN)**), copy the **DSN** — looks like
   `https://<key>@o<orgid>.ingest.<region>.sentry.io/<projectid>`.

Add the DSN to `.env`:

```bash
SENTRY_DSN=https://...
```

> The DSN is what the **app** uses to ship events *into* Sentry. It is
> distinct from `SENTRY_TOKEN`, which Coral uses to *read* Sentry's API on the
> agent side. Both come from the same Sentry org but serve opposite directions.

### 11b. Build the image

```bash
cd demo-app
docker build --platform linux/amd64 -t hello-service:<git-sha> .
```

Same `linux/amd64` gotcha as the agent — must match cluster node arch.

### 11c. Push to your registry

Push to whatever registry your cluster pulls from (ECR in this guide), then
record the digest:

```bash
docker tag hello-service:<git-sha> <ECR_REPO>/hello-service:<git-sha>
docker push <ECR_REPO>/hello-service:<git-sha>
docker inspect --format='{{index .RepoDigests 0}}' <ECR_REPO>/hello-service:<git-sha>
```

Update the `image:` line in `deploy/hello-service.yaml` with the digest ref
(`hello-service@sha256:...`) before applying.

### 11d. Create the hello-service Secret in k8s

Just like `sre-agent-secrets`, create the demo app's Secret out-of-band so
nothing sensitive lands in git:

```bash
kubectl -n coral-demos create secret generic hello-service-secrets \
  --from-literal=SENTRY_DSN='https://...' \
  --from-literal=DD_API_KEY='...' \
  --from-literal=DD_SITE='datadoghq.eu'
```

### 11e. Deploy

```bash
kubectl apply -f deploy/hello-service.yaml
kubectl -n coral-demos rollout status deployment/hello-service
kubectl -n coral-demos port-forward svc/hello-service 8000:80
curl 'http://localhost:8000/'                  # → hello world
curl 'http://localhost:8000/greet?name=alice'  # → Hello, ALICE!
curl 'http://localhost:8000/greet?name=dave'   # → 500, Sentry event fires
```

### 11f. Create the Datadog monitor

In Datadog → **Monitors** → **New Monitor** → **Metric**:

- **Metric**: `hello_service.errors` · aggregation `sum` · over `last 5 minutes`
- **Group by**: `service` (optional, gives nicer alert text)
- **Threshold**: `above 5` (so the monitor only fires on a real spike, not
  one stray request)
- **No data**: *do not notify* (otherwise idle periods will page)
- **Title**: `hello-service error rate elevated`
- **Message body**:
  ```
  {{#is_alert}}
  hello-service is throwing exceptions: {{value}} errors in the last 5m.
  Investigate: top Sentry issues, recent commits, blast radius.
  @slack-<your-account>
  {{/is_alert}}
  ```
- **Save**.

### 11g. Trigger and verify

```bash
scripts/demo_trigger_alert.sh           # 30 requests, all 500
```

Within ~1–2 minutes you should see, in order:
1. Sentry: a new `AttributeError: 'NoneType' object has no attribute 'upper'`
   issue with full traceback in the `hello-service` project.
2. Datadog: the `hello_service.errors` counter crosses threshold; monitor
   moves to ALERT.
3. Slack `#alerts`: Datadog posts the monitor alert.
4. SRE bot: replies in-thread with an investigation that names the service,
   identifies the dominant Sentry exception, and suggests next checks.

If any step doesn't happen, work backwards from there using the
[Troubleshooting](#troubleshooting) table.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Bot never receives messages | Wrong token (`xoxp-` user token instead of `xoxb-` bot token), or missing event subscriptions / scopes. Reinstall the app. |
| "Sending messages to this app has been turned off" | App Home → enable the Messages Tab + allow user messages. |
| Pod `CrashLoopBackOff`, `exec format error` | Image arch mismatch — rebuild `--platform linux/amd64`. |
| `coral source add datadog` fails | `DD_APPLICATION_KEY` (Datadog Application key) is missing. |
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
