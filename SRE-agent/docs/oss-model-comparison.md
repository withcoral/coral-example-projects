# OSS model comparison on AWS Bedrock for the SRE agent

This bot's default model is open-source (`bedrock:minimax.minimax-m2.5`), but
"the bot works with this model" wasn't enough — we wanted concrete data on
how each Bedrock-available OSS model holds up against the full SRE-agent
investigation flow. This page captures that comparison.

If you're forking the project and wondering which model to pick, this is the
data behind the default.

## TL;DR

**🏆 Pick `bedrock:minimax.minimax-m2.5`** for production. It's the only
OSS model in this survey that produces the same quality of structured
investigation as Anthropic Opus 4.7 — including timeline / comparison tables
— at lower latency than Opus.

If you need a low-latency / low-cost tier, **`bedrock:qwen.qwen3-32b-v1:0`**
delivers a coherent 9-header structured response in ~20 seconds. Less
thorough (fewer tool calls, no tables) but useful for "fast triage" mode.

Avoid `bedrock:qwen.qwen3-coder-30b-a3b-v1:0` — it hallucinates a
structured response with zero actual tool calls in the full agent context.

## Why this survey existed

The agent is intentionally model-agnostic: anything pydantic-ai supports
that handles tool use will work, selected via the `SRE_AGENT_MODEL`
environment variable. We needed to pick a default that:

1. Is openly available (Bedrock OSS rather than Anthropic-direct).
2. Runs in the same region as the rest of the demo infra (`eu-west-1`).
3. Holds up under a real multi-source SRE investigation (20+ tool calls
   across Datadog, Sentry, and GitHub via Coral MCP) — not just a
   one-shot tool-use smoke test.

## Methodology

**Phase 1 — Tool-use smoke test (local).** Spin up a minimal pydantic-ai
Agent with one fake `get_error_count(service)` tool and ask the model to
use it. Eliminates models that can't do basic tool-call round-trips with
the Bedrock + pydantic-ai stack.

**Phase 2 — Full agent end-to-end (production cluster).** For each model
that passes Phase 1, swap `SRE_AGENT_MODEL` in the live deployment,
restart the pod, fire a real Datadog monitor alert (`hello_service.errors > 1`
on the demo `hello-service` FastAPI app), and capture:

- Number of Coral MCP tool calls (from the streamed `plan` block)
- Wall-clock latency from alert post to final reply
- Number of `## H2` section headers in the structured assessment (proxy
  for whether the model followed the system prompt's required shape)
- Number of GFM tables in the body (timeline / comparison rendering)
- Number of URL buttons in the Sources actions block (proxy for grounding)
- Any pod errors observed during the run

The reference is **Anthropic Claude Opus 4.7** (the previous default during
development). Quality target: match Opus's structural output.

## Bedrock OSS-ish catalog (eu-west-1)

These are the ON_DEMAND-priced foundation models from non-Anthropic
providers available in `eu-west-1` at the time of this survey:

| Provider | Model ID |
|---|---|
| MiniMax | `minimax.minimax-m2.5`, `minimax.minimax-m2.1`, `minimax.minimax-m2` |
| Qwen | `qwen.qwen3-32b-v1:0`, `qwen.qwen3-next-80b-a3b`, `qwen.qwen3-coder-30b-a3b-v1:0`, `qwen.qwen3-vl-235b-a22b` |
| Mistral | `mistral.devstral-2-123b`, `mistral.magistral-small-2509`, several smaller `ministral-3` variants, `mistral.mistral-large-2402-v1:0`, etc. |

Tested candidates were chosen by recency, parameter count, and tool-use
suitability (vision-only and tiny instruct models were skipped).

## Phase 1: tool-use smoke test results

| Model | Tool call worked | Latency | Notes |
|---|---|---|---|
| `bedrock:minimax.minimax-m2.5` | ✓ | 2.2s | Clean output |
| `bedrock:qwen.qwen3-32b-v1:0` | ✓ | 1.0s | Fastest |
| `bedrock:qwen.qwen3-next-80b-a3b` | ✓ | 1.3s | Clean output |
| `bedrock:qwen.qwen3-coder-30b-a3b-v1:0` | ✓ | 2.8s | Verbose |
| `bedrock:mistral.devstral-2-123b` | ✗ | 0.9s | Bedrock returns 400 on the tool-result JSON shape pydantic-ai emits |
| `bedrock:mistral.magistral-small-2509` | ✗ | 0.7s | Emits literal `[TOOL_CALLS]get_error_count{...}` as text rather than calling the tool — broken integration |

Four candidates advanced to the full agent test.

## Phase 2: full agent end-to-end

Each row is one real investigation triggered by 8 bad requests to the
`hello-service` `/greet` endpoint, with the agent given the same
deployment context (Datadog monitor `hello_service.errors`, Sentry project
`python-fastapi`, GitHub repo `withcoral/coral-example-projects`).

| Metric | Anthropic Opus 4.7 (baseline) | MiniMax M2.5 | Qwen 3 32B | Qwen 3 Next 80B | Qwen 3 Coder 30B |
|---|---|---|---|---|---|
| Tool calls | ~20 | **28** | 8 | **34** | **0** ⚠️ |
| Latency | 2m 8s | 1m 36s | **20s** | 1m 46s | 5s |
| H2 headers | 9 | 9 | 9 | 9 | 9 |
| Tables in body | 1 | **2** | 0 | 0 | 0 |
| Source buttons | 5 | 3 | 3 | 2 | 3 |
| Pod errors | 0 | 0 | 0 | 0 | 0 |

### Per-model observations

**MiniMax M2.5** — the only OSS model that produces tables. Used the
timeline pattern from the system prompt (`Time | Event | Source`) plus
a second comparison table. 28 tool calls is comparable to Opus's 20 but
done in 32 seconds less wall clock. Charges reasoning tokens against
`max_tokens` so a tight cap silently kills it — we ship with 16k which
is comfortable.

**Qwen 3 32B** — five times faster than MiniMax. Produces a full 9-header
structured response but only 8 tool calls — skips deeper investigation
patterns like reading the offending source file from GitHub. Best fit if
you want a "fast triage" tier: the alert lands, the bot responds with a
coherent assessment in ~20 seconds, you can ask follow-up questions for
depth. Note: hard-capped at 32768 output tokens — we lowered the global
`MAX_OUTPUT_TOKENS` to 16k to fit.

**Qwen 3 Next 80B** — most thorough by tool-call count (34). But somehow
chose not to render any tables despite having the data to do so, and only
produced 2 source buttons. Slower than MiniMax for less polished output.
Not the recommended pick.

**Qwen 3 Coder 30B** — biggest surprise. Marketed as agent-focused, passes
the Phase-1 single-tool smoke test, but in the full agent context with the
real system prompt + Coral MCP tool schemas it **does zero tool calls** and
hallucinates a structured response from the prompt alone. The output has
9 headers and 3 source buttons, but the source URLs are fabricated — the
agent didn't look up real monitor IDs / issue IDs / commit SHAs because it
never queried anything. Caught by the context-footer's `:wrench: 0 Coral queries`
metric. **Avoid.**

## Final recommendation

```bash
# In the k8s Secret / .env:
SRE_AGENT_MODEL=bedrock:minimax.minimax-m2.5

# For a fast-triage variant (DM-only handler? a dedicated low-priority alert channel?):
SRE_AGENT_MODEL=bedrock:qwen.qwen3-32b-v1:0
```

MiniMax M2.5 matches Anthropic Opus 4.7 on structural quality at lower
latency. Qwen 3 32B is the fast/cheap alternative when you want a coherent
answer in seconds rather than minutes.

## Reproducing this survey

The Phase-1 matrix script is in `/tmp/oss_model_matrix.py` (in this
investigation transcript) — about 50 lines of pydantic-ai plus a tool
declaration. Phase 2 is just `kubectl patch secret … SRE_AGENT_MODEL=… &&
kubectl rollout restart deployment/sre-agent`, then trigger an alert via
`scripts/demo_trigger_alert.sh` and inspect the resulting Slack thread's
block structure.

If you fork the project, you can run the same survey against whatever
models are available in your Bedrock region — the agent's model selection
is a single env var, so the experimental cost is just compute + pod
restarts.
