# Coral Example Projects

Reference implementations for building production-style AI agents on top of
[Coral](https://withcoral.com) — a unified MCP layer over the observability,
incident, code, and chat tools that real engineering teams already use.

These are full, runnable projects, not toy snippets. Fork them, point them at
your own Coral instance and data sources, and adapt from there.

## Projects

- **[SRE-agent](SRE-agent/README.md)** — Slack bot that auto-investigates
  Datadog alerts end-to-end. Streams a live Block Kit "plan" of every Coral
  MCP tool call it makes, then posts a structured incident assessment with
  evidence pulled from Datadog, Sentry, and GitHub. Pydantic AI + Slack Bolt
  Socket Mode + Coral MCP over stdio. Deploys as a single-replica container.

## License

Apache 2.0 — see [LICENSE](LICENSE).
