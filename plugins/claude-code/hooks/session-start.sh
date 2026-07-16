#!/usr/bin/env bash
#
# SessionStart shim — the entire hook. All logic (settings resolution, the
# context brief, opt-in envelope capture) lives in the released basic-memory
# package behind `basic-memory hook session-start`; the plugin ships
# configuration, not code.
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

# ${CLAUDE_PROJECT_DIR} pins project mapping to the session's project root
# instead of trusting cwd; the hook JSON on stdin passes through untouched.
if [[ -n "${CLAUDE_PROJECT_DIR:-}" ]]; then
    "${BM[@]}" hook session-start --harness claude --project-dir "${CLAUDE_PROJECT_DIR}"
else
    "${BM[@]}" hook session-start --harness claude
fi

# Fail-open: run instead of exec, then always exit 0. A launcher that resolves
# but errors at runtime — a cold uvx that cannot reach PyPI, an unbuildable
# floor, a bad BM_BIN — would otherwise tail-exec its non-zero status to the
# harness and disrupt the session. The CLI's stdout still reaches the session
# context and stdin still passes through.
exit 0
