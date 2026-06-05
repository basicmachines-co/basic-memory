#!/usr/bin/env bash
#
# PreCompact hook - checkpoint Codex work into Basic Memory before compaction.
#
# Contract: best effort. The hook only writes when .codex/basic-memory.json pins a
# primary project, and every failure exits 0 so compaction can continue.

set -u

if ! command -v uv >/dev/null 2>&1; then
    exit 0
fi

script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
uv run --script "$script_dir/pre-compact.py" 2>/dev/null || exit 0
