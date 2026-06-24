import json
from pathlib import Path

import pytest

from basic_memory_benchmarks.reporting.compare import compare_provider_metric, load_retrieval_summary


def test_compare_provider_metric(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.json"
    candidate = tmp_path / "candidate.json"

    baseline.write_text(
        json.dumps(
            {
                "providers": [
                    {
                        "provider": "bm-local",
                        "metrics": {
                            "recall_at_5": 0.4,
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    candidate.write_text(
        json.dumps(
            {
                "providers": [
                    {
                        "provider": "bm-local",
                        "metrics": {
                            "recall_at_5": 0.5,
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    b = load_retrieval_summary(baseline)
    c = load_retrieval_summary(candidate)
    base_value, candidate_value, delta = compare_provider_metric(b, c, "bm-local", "recall_at_5")
    assert base_value == 0.4
    assert candidate_value == 0.5
    assert delta == pytest.approx(0.1)
