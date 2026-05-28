#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    config = {
        "mcpServers": {
            "coral": {
                "command": "./scripts/start_coral_mcp.sh",
                "args": [],
                "env": {
                    "CORAL_BIN": "${CORAL_BIN:-coral}",
                    "SLACK_TOKEN": "${SLACK_TOKEN}",
                    "DD_API_KEY": "${DD_API_KEY}",
                    "DD_APPLICATION_KEY": "${DD_APPLICATION_KEY}",
                    "DD_SITE": "${DD_SITE:-datadoghq.com}",
                    "GITHUB_TOKEN": "${GITHUB_TOKEN}",
                    "SENTRY_TOKEN": "${SENTRY_TOKEN}",
                    "SENTRY_ORG": "${SENTRY_ORG}",
                    "SENTRY_BASE_URL": "${SENTRY_BASE_URL:-https://sentry.io}",
                },
            }
        }
    }
    (ROOT / ".mcp.json").write_text(json.dumps(config, indent=2) + "\n")
    print(f"Wrote {ROOT / '.mcp.json'}")


if __name__ == "__main__":
    main()
