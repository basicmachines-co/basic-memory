from basic_memory_benchmarks.models import DatasetProvenance, RunConfig, RunManifest, RuntimeInfo


def test_manifest_schema_roundtrip() -> None:
    config = RunConfig(
        run_id="run1",
        dataset_id="synthetic",
        dataset_path="benchmarks/synthetic/queries.json",
        corpus_dir="benchmarks/synthetic/docs",
        queries_path="benchmarks/synthetic/queries.json",
        providers=["bm-local"],
    )
    manifest = RunManifest(
        run_id="run1",
        created_at_utc="2026-01-01T00:00:00Z",
        benchmark_git_sha="abc",
        bm_source="github:main",
        dataset=DatasetProvenance(
            dataset_id="synthetic",
            source_url="local",
            checksum_sha256="123",
            license_note="test",
            fetched_at_utc="2026-01-01T00:00:00Z",
        ),
        runtime=RuntimeInfo(
            os="test", python_version="3.12", started_at_utc="2026-01-01T00:00:00Z"
        ),
        config=config,
    )
    payload = manifest.model_dump(mode="json")
    assert payload["run_id"] == "run1"
    assert payload["config"]["providers"] == ["bm-local"]
