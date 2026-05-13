# Pydantic SRE Agent Plan

Date: 2026-05-13

## Current State

- The agent now uses Pydantic AI with Coral exposed as an MCP stdio toolset.
- Haiku test model works: `claude-haiku-4-5-20251001`.
- Coral is installed locally as `coral 0.1.5`.
- Coral sources currently visible:
  - `datadog`: connected, useful for monitor and incident posture.
  - `github`: connected, broad table surface.
  - `sentry`: connected, useful for issue triage.
  - `linear`: connected, available for planning/project context.
  - `slack`: installed, but not usable yet because the token is missing Slack scope `groups:read`.
- Three real SRE prompts were tested:
  - Datadog alert posture worked well.
  - Sentry triage worked when constrained to Sentry-only.
  - Slack incident-context inspection exposed the expected Slack auth/scope blocker.

## Near-Term Work

1. Fix Slack source access.
   - Add `groups:read` to the Slack user token scopes.
   - Reinstall or reauthorize the Slack app.
   - Update Coral's saved `SLACK_TOKEN` with the refreshed token.
   - Verify:
     ```bash
     coral source test slack
     coral sql "SELECT * FROM slack.channels LIMIT 1"
     ```

2. Turn the tested SRE questions into repeatable demo cases.
   - Add a small script or documented command set for:
     - Current Datadog alert and incident posture.
     - Sentry issue triage.
     - Slack incident-channel context once Slack auth is fixed.
   - Keep prompts read-only and explicitly ask for evidence, uncertainty, confidence, and next checks.
   - Avoid raw stack traces, message dumps, secrets, and provider payloads in outputs.

3. Tighten cross-source investigations.
   - Create narrower Sentry-to-GitHub prompts so the agent does not exhaust the tool-call budget.
   - Prefer a two-step pattern:
     - Identify issue/release/service candidate from Sentry or Datadog.
     - Query GitHub only with a bounded repo, owner, branch, release, or search term.
   - Decide whether `max_tool_rounds` should be configurable from `.env`.

4. Harden agent behavior.
   - Keep returning clean incident-style errors for Coral MCP tool failures.
   - Add integration-style tests that can be run only when real provider credentials are present.
   - Consider structured result models for demo cases so output is easier to compare across runs.
   - Add a redaction pass or stricter prompt guard for provider data that may contain names, stack traces, or messages.

5. Update docs for the actual operating model.
   - Document the split between Slackbot runtime credentials and Coral Slack data credentials.
   - Make it clear that Coral Slack can use a user token, but the token must include the read scopes required by the tables being queried.
   - Update the walkthrough with the three validated SRE cases and known failure modes.

## Stretch Goal: Coral OTEL Demo

Goal: run the newest available Coral build, collect OpenTelemetry from Coral/Pydantic SRE agent activity, and show the traces/metrics in a local viewer.

Plan:

1. Verify or install the newest Coral version.
   - Check the current Homebrew cask and release notes.
   - Upgrade in a controlled way:
     ```bash
     brew update
     brew upgrade coral
     coral --version
     ```
   - Confirm whether the newer CLI exposes OTEL configuration, either through docs, flags, or environment variables.

2. Stand up local telemetry collection.
   - Use an OTEL Collector with an OTLP receiver.
   - Export to a simple local backend such as Jaeger, Grafana Tempo, or another lightweight viewer.
   - Keep this optional so the SRE agent still works without telemetry infrastructure.

3. Run instrumented demo cases.
   - Execute the Datadog and Sentry demo prompts through `PydanticSreAgent`.
   - Capture Coral MCP/tool spans, query timing, model-call timing, and error spans.
   - Include the Slack failure case after auth is fixed, or keep it as a negative telemetry example while `missing_scope` remains.

4. Show the telemetry.
   - Add a short command sequence and screenshots or saved notes showing:
     - model request span
     - Coral MCP SQL tool span
     - provider source query span
     - error span for source failures
   - Use the trace to explain where time is spent and where failures originate.

5. Decide what to productize.
   - If the OTEL signal is useful, add a `scripts/run_agent_with_otel.sh`.
   - Document required `OTEL_*` environment variables.
   - Add a lightweight smoke check that verifies traces are emitted during a demo run.
