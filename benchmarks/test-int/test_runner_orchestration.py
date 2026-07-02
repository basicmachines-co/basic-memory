import json
from pathlib import Path

from basic_memory_benchmarks.models import DatasetProvenance, RunConfig, SearchHit
from basic_memory_benchmarks.providers.base import BenchmarkProvider
from basic_memory_benchmarks.runner import run_retrieval


class FakeProvider(BenchmarkProvider):
    def __init__(self, name: str, mapping: dict[str, str]):
        self.name = name
        self.mapping = mapping

    def ingest(self, corpus_path: Path, run_config: RunConfig) -> None:
        _ = corpus_path
        _ = run_config

    def search(self, query: str, limit: int, run_config: RunConfig) -> list[SearchHit]:
        _ = limit
        _ = run_config
        doc_id = self.mapping.get(query)
        if not doc_id:
            return []
        return [
            SearchHit(
                source_doc_id=doc_id, source_path=f"docs/{doc_id}.md", text="match", score=1.0
            )
        ]

    def cleanup(self, run_config: RunConfig) -> None:
        _ = run_config


def test_runner_writes_artifacts(tmp_path: Path) -> None:
    corpus_dir = tmp_path / "docs"
    corpus_dir.mkdir(parents=True)
    (corpus_dir / "doc-a.md").write_text("# doc-a\n", encoding="utf-8")

    queries = [
        {
            "id": "q1",
            "query": "alpha",
            "category": "single_hop",
            "category_id": 1,
            "ground_truth": ["doc-a"],
            "expected_answer": "match",
            "metadata": {},
        }
    ]
    queries_path = tmp_path / "queries.json"
    queries_path.write_text(json.dumps(queries), encoding="utf-8")

    config = RunConfig(
        run_id="testrun",
        dataset_id="synthetic",
        dataset_path=str(tmp_path / "dataset.json"),
        corpus_dir=str(corpus_dir),
        queries_path=str(queries_path),
        output_root=str(tmp_path / "runs"),
        providers=["p1", "p2"],
        allow_provider_skip=False,
    )

    dataset = DatasetProvenance(
        dataset_id="synthetic",
        source_url="local",
        checksum_sha256="abc",
        license_note="test",
        fetched_at_utc="2026-01-01T00:00:00Z",
    )

    providers = {
        "p1": FakeProvider("p1", {"alpha": "doc-a"}),
        "p2": FakeProvider("p2", {"alpha": "doc-a"}),
    }

    run_dir = run_retrieval(
        run_config=config,
        dataset=dataset,
        provider_factory=lambda name: providers[name],
    )

    assert (run_dir / "manifest.json").exists()
    assert (run_dir / "retrieval-summary.json").exists()
    assert (run_dir / "per-query-retrieval.jsonl").exists()
