#!/usr/bin/env bash
set -euo pipefail

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required to run BM integration tests against source checkout." >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_REPO="${SCRIPT_DIR}/../../.."
REPO_PATH="${BASIC_MEMORY_REPO:-$DEFAULT_REPO}"

if [[ -d "$REPO_PATH" && -f "$REPO_PATH/pyproject.toml" ]]; then
  exec uv run --project "$REPO_PATH" bm "$@"
fi

exec bm "$@"
