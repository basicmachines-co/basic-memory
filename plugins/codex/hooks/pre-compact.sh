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

# A pre-hook basic-memory/bm left on PATH would otherwise shadow the uvx floor
# and exec a CLI without the `hook` command — erroring instead of failing open.
# Probe `hook` support first; stdin is detached (</dev/null) so the probe never
# consumes the hook JSON meant for the real invocation.
supports_hook() { "$@" hook --help >/dev/null 2>&1 </dev/null; }

if [[ -n "${BM_BIN:-}" ]]; then
    # An explicit path (may contain spaces) stays one word; any other value is a
    # multi-token launcher like "uvx basic-memory". Test existence, not the
    # executable bit: Git Bash reports extensionless files as non-executable, so
    # `-x` would word-split a real path on Windows.
    if [[ -e "$BM_BIN" ]]; then BM=("$BM_BIN"); else read -r -a BM <<<"$BM_BIN"; fi
elif command -v basic-memory >/dev/null 2>&1 && supports_hook basic-memory; then
    BM=(basic-memory)
elif command -v bm >/dev/null 2>&1 && supports_hook bm; then
    BM=(bm)
elif command -v uvx >/dev/null 2>&1; then
    BM=(uvx "basic-memory>=0.22.1")
else
    exit 0
fi

# Codex has no project-dir env var; project mapping uses the payload cwd.
# The hook JSON on stdin passes through untouched.
exec "${BM[@]}" hook pre-compact --harness codex
