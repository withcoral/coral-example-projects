#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../.."

REMOTE="${REMOTE:-}"
VISIBILITY="${VISIBILITY:-private}"

if [ -z "$REMOTE" ]; then
  echo "Set REMOTE to the GitHub owner/name, for example REMOTE=withcoral/coral-example-projects"
  exit 1
fi

if ! command -v gh >/dev/null 2>&1; then
  echo "GitHub CLI is required to create the remote repository."
  exit 1
fi

if [ "$VISIBILITY" = "internal" ]; then
  gh repo create "$REMOTE" --internal --source . --remote origin --push
else
  gh repo create "$REMOTE" --private --source . --remote origin --push
fi
