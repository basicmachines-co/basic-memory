"""Cloud benchmarks use the supported CLI search transport."""

import json
from subprocess import CompletedProcess
from unittest.mock import patch

import pytest

from basic_memory_benchmarks.models import RunConfig
from basic_memory_benchmarks.providers.bm_cloud import BasicMemoryCloudProvider


def test_enabled_cloud_search_uses_current_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BASIC_MEMORY_BENCH_ENABLE_CLOUD", "true")
    config = RunConfig(
        run_id="cloud-test",
        dataset_id="synthetic",
        dataset_path="dataset.json",
        corpus_dir="docs",
        queries_path="queries.json",
    )
    payload = {"results": [{"entity_id": 7, "file_path": "notes/topic.md", "score": 0.8}]}
    with patch(
        "basic_memory_benchmarks.providers.bm_cloud.run_command",
        return_value=CompletedProcess([], 0, stdout=json.dumps(payload)),
    ) as command:
        hits = BasicMemoryCloudProvider().search("topic", 3, config)

    command.assert_called_once_with(
        [
            "bm",
            "tool",
            "search",
            "topic",
            "--project",
            "bm-bench-cloud-test",
            "--page-size",
            "3",
            "--hybrid",
            "--cloud",
        ]
    )
    assert len(hits) == 1
    assert hits[0].source_doc_id == "topic"
    assert hits[0].score == 0.8
