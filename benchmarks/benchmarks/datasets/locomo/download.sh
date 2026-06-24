#!/usr/bin/env bash
set -euo pipefail

uv run bm-bench datasets fetch --dataset locomo --output benchmarks/datasets/locomo/locomo10.json
