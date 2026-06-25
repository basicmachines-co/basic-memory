"""Comparison helpers for retrieval summary outputs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping


def load_retrieval_summary(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return dict(payload)


def compare_provider_metric(
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
    provider: str,
    metric_name: str,
) -> tuple[float | None, float | None, float | None]:
    def _value(blob: Mapping[str, Any]) -> float | None:
        providers_raw = blob.get("providers")
        if not isinstance(providers_raw, list):
            return None
        for item in providers_raw:
            if not isinstance(item, Mapping):
                continue
            if item.get("provider") != provider:
                continue
            metrics_raw = item.get("metrics")
            if not isinstance(metrics_raw, Mapping):
                return None
            raw = metrics_raw.get(metric_name)
            if isinstance(raw, (int, float)):
                return float(raw)
            if isinstance(raw, str):
                try:
                    return float(raw)
                except ValueError:
                    return None
            return None
        return None

    b = _value(baseline)
    c = _value(candidate)
    if b is None or c is None:
        return b, c, None
    return b, c, c - b
