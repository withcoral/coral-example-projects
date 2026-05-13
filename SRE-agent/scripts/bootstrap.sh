#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required"
  exit 1
fi

if ! command -v coral >/dev/null 2>&1; then
  echo "coral is required. Install it before continuing."
  exit 1
fi

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example. Fill in secrets before running the bot."
fi

if command -v uv >/dev/null 2>&1; then
  uv sync --extra dev --extra notebook
else
  python3 -m venv .venv
  . .venv/bin/activate
  python -m pip install --upgrade pip
  python -m pip install -e ".[dev,notebook]"
fi

./scripts/write_mcp_config.py

echo "Bootstrap complete."
echo "Next: edit .env, then run ./scripts/configure_coral.sh"

