#!/usr/bin/env bash
#
# SessionStart hook - brief Codex from Basic Memory at thread start.
#
# Contract: best effort only. A missing Basic Memory install, empty project, slow
# cloud read, or bad config must never disrupt a Codex thread.

set -u

if ! command -v uv >/dev/null 2>&1; then
    exit 0
fi

script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
uv run --script "$script_dir/session-start.py" 2>/dev/null || exit 0
