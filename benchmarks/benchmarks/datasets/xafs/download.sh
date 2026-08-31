#!/usr/bin/env bash
set -euo pipefail

# xAFS benchmark data (CC-BY-4.0) is never vendored into this repo.
# Fetched from Hugging Face at a pinned revision so runs are reproducible.
# Requires the `hf` CLI: pip install -U huggingface_hub  (or: uv tool install huggingface_hub)
#
# XAFS_PERSONAS: optional comma-separated persona subset, e.g. "dp_001,dp_002".
# Unset/empty fetches every persona (~19K files / ~837MB) — subset unless you
# really need the full dataset.

dest="benchmarks/datasets/xafs/upstream"
revision="21142b2c01113cb881c80d6c99bcf0f412ed17f2"

include_args=()
if [ -n "${XAFS_PERSONAS:-}" ]; then
  IFS=',' read -ra personas <<<"$XAFS_PERSONAS"
  for persona in "${personas[@]}"; do
    include_args+=(--include "${persona// /}/*")
  done
fi

# ${arr[@]+...} guards the empty-array expansion under `set -u` on bash 3.2 (macOS).
hf download supermemory/xAFS \
  --repo-type dataset \
  --revision "$revision" \
  --local-dir "$dest" \
  ${include_args[@]+"${include_args[@]}"}

echo "xAFS data available under $dest"
echo "Fetch more personas with: XAFS_PERSONAS=dp_003,dp_004 bash benchmarks/datasets/xafs/download.sh"
