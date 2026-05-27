"""Tiny .env writer used by the onboarding notebook.

Kept minimal on purpose — the notebook runs Coral commands itself via the
``!coral …`` shell magic so first-time readers can see exactly what's happening.
This helper only owns the one piece of plumbing that's awkward to inline: making
credentials survive a kernel restart by writing them to ``.env`` (and to
``os.environ`` so subsequent ``!coral`` calls inherit them)."""

from __future__ import annotations

import os
from pathlib import Path

ENV_PATH = Path(__file__).resolve().parents[2] / ".env"


def update_env(key: str, value: str, env_path: Path = ENV_PATH) -> None:
    """Persist ``KEY=VALUE`` to .env and set it in ``os.environ`` for this process."""
    env_path.parent.mkdir(parents=True, exist_ok=True)
    lines = env_path.read_text().splitlines() if env_path.exists() else []

    replaced = False
    for i, line in enumerate(lines):
        if line.lstrip().startswith(f"{key}="):
            lines[i] = f"{key}={value}"
            replaced = True
            break
    if not replaced:
        lines.append(f"{key}={value}")

    env_path.write_text("\n".join(lines) + "\n")
    os.environ[key] = value
