#!/usr/bin/env bash
# Install the latest released basic-memory CLI via uv.
# Idempotent — safe to re-run. Fails gracefully when uv is absent.
set -euo pipefail

BM_REPO="https://github.com/basicmachines-co/basic-memory.git"
BM_REF="${BM_REF:-}"

# ── check for uv ──────────────────────────────────────────────────
if ! command -v uv >/dev/null 2>&1; then
  echo "⚠  uv not found — skipping basic-memory install."
  echo "   Install uv:  brew install uv"
  echo "            or:  curl -LsSf https://astral.sh/uv/install.sh | sh"
  echo "   Then re-run:  bash scripts/setup-bm.sh"
  exit 0
fi

# ── install basic-memory ────────────────────────────────
if [[ -n "${BM_REF}" ]]; then
  echo "Installing basic-memory from ${BM_REPO}@${BM_REF} ..."
  uv tool install \
    "basic-memory @ git+${BM_REPO}@${BM_REF}" \
    --force
else
  echo "Installing latest basic-memory from PyPI ..."
  uv tool install basic-memory --force
fi

# ── verify ────────────────────────────────────────────────────────
UV_BIN_DIR="$(uv tool dir --bin)"
BM_BIN="${UV_BIN_DIR}/bm"
if [[ ! -x "${BM_BIN}" ]]; then
  BM_BIN="${UV_BIN_DIR}/basic-memory"
fi

if [[ ! -x "${BM_BIN}" ]]; then
  echo "❌  basic-memory binary not found in uv's tool bin after install."
  echo "   You may need to add uv's bin directory to your PATH: ${UV_BIN_DIR}"
  exit 1
fi

PATH_BM="$(command -v bm || true)"
if [[ -n "${PATH_BM}" && "${PATH_BM}" != "${UV_BIN_DIR}/bm" ]]; then
  echo "⚠  bm on PATH resolves to ${PATH_BM}"
  echo "   uv installed basic-memory at ${BM_BIN}; move ${UV_BIN_DIR} earlier in PATH or set bmPath."
fi

echo "✅  $("${BM_BIN}" --version)"
