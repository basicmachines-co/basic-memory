#!/usr/bin/env bash
#
# PreCompact shim — the entire hook. All logic (config resolution, the
# extractive Codex checkpoint, opt-in envelope capture) lives in the released
# basic-memory package behind `basic-memory hook pre-compact`; the plugin
# ships configuration, not code.
#
# Resolution order: BM_BIN (explicit override) → basic-memory / bm on PATH
# (preferred — keeps the hook's version consistent with the user's MCP server)
# → uvx at a released floor (fetches from PyPI on first run; uv is the
# documented prerequisite). Nothing resolvable → silent exit 0: the plugin
# must stay invisible to non-Basic-Memory users (fail-open).
set -u

if [[ -n "${BM_BIN:-}" ]]; then
    # An explicit executable path (may contain spaces) stays one word; any
    # other value is a multi-token launcher like "uvx basic-memory".
    if [[ -x "$BM_BIN" ]]; then BM=("$BM_BIN"); else read -r -a BM <<<"$BM_BIN"; fi
elif command -v basic-memory >/dev/null 2>&1; then
    BM=(basic-memory)
elif command -v bm >/dev/null 2>&1; then
    BM=(bm)
elif command -v uvx >/dev/null 2>&1; then
    BM=(uvx "basic-memory>=0.22.1")
else
    exit 0
fi

# Codex has no project-dir env var; project mapping uses the payload cwd.
# The hook JSON on stdin passes through untouched.
exec "${BM[@]}" hook pre-compact --harness codex
