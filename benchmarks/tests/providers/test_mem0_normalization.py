import pytest

from basic_memory_benchmarks.providers.mem0_local import Mem0LocalProvider


def test_mem0_normalization_prefers_nested_metadata() -> None:
    item = {
        "id": "abc",
        "memory": "hello",
        "score": 0.42,
        "metadata": {
            "source_doc_id": "doc-1",
            "source_path": "docs/doc-1.md",
        },
    }
    hit = Mem0LocalProvider._normalize_item(item)
    assert hit.source_doc_id == "doc-1"
    assert hit.source_path == "docs/doc-1.md"
    assert hit.score == 0.42


class TestLocalBackendConfig:
    def _config(self, run_id: str = "run1"):
        from basic_memory_benchmarks.models import RunConfig

        return RunConfig(
            run_id=run_id,
            dataset_id="t",
            dataset_path="t",
            corpus_dir="t",
            queries_path="t",
        )

    def test_local_config_shape(self, monkeypatch):
        from basic_memory_benchmarks.providers.mem0_local import Mem0LocalProvider

        monkeypatch.delenv("MEM0_LLM_MODEL", raising=False)
        monkeypatch.delenv("MEM0_EMBED_MODEL", raising=False)
        monkeypatch.delenv("MEM0_EMBED_DIMS", raising=False)
        provider = Mem0LocalProvider()
        config = provider._local_config("http://localhost:11434/v1", self._config("abc-q1"))

        assert config["llm"]["provider"] == "openai"
        assert config["llm"]["config"]["openai_base_url"] == "http://localhost:11434/v1"
        assert config["embedder"]["config"]["embedding_dims"] == 768
        # Run-scoped, qdrant-safe collection name (no dashes).
        assert config["vector_store"]["config"]["collection_name"] == "bm_bench_abc_q1"
        assert config["vector_store"]["config"]["embedding_model_dims"] == 768

    def test_skipped_without_any_backend(self, monkeypatch):
        from basic_memory_benchmarks.exceptions import ProviderSkippedError
        from basic_memory_benchmarks.providers.mem0_local import Mem0LocalProvider

        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("MEM0_OPENAI_COMPAT_BASE_URL", raising=False)
        provider = Mem0LocalProvider()
        with pytest.raises(ProviderSkippedError, match="MEM0_OPENAI_COMPAT_BASE_URL"):
            provider._ensure_memory(self._config())

    def test_infer_env_flag(self, monkeypatch):
        from basic_memory_benchmarks.providers.mem0_local import Mem0LocalProvider

        monkeypatch.setenv("MEM0_INFER", "true")
        assert Mem0LocalProvider()._infer is True
        monkeypatch.setenv("MEM0_INFER", "false")
        assert Mem0LocalProvider()._infer is False
        monkeypatch.delenv("MEM0_INFER")
        assert Mem0LocalProvider()._infer is False


class TestTelemetryLockAvoidance:
    def test_telemetry_disabled_before_import(self, monkeypatch):
        import os

        from basic_memory_benchmarks.providers.mem0_local import Mem0LocalProvider

        monkeypatch.setenv("MEM0_OPENAI_COMPAT_BASE_URL", "http://localhost:1/v1")
        monkeypatch.delenv("MEM0_TELEMETRY", raising=False)
        provider = Mem0LocalProvider()
        # _ensure_memory sets the env var before importing mem0; construction
        # against the dead endpoint may fail later, which is fine here.
        try:
            provider._ensure_memory(
                __import__("basic_memory_benchmarks.models", fromlist=["RunConfig"]).RunConfig(
                    run_id="t", dataset_id="t", dataset_path="t", corpus_dir="t", queries_path="t"
                )
            )
        except Exception:
            pass
        assert os.environ["MEM0_TELEMETRY"] == "false"

    def test_cleanup_closes_clients_and_drops_memory(self):
        from basic_memory_benchmarks.providers.mem0_local import Mem0LocalProvider

        class FakeClient:
            closed = False

            def close(self):
                self.closed = True

        class FakeStore:
            def __init__(self):
                self.client = FakeClient()

        class FakeMemory:
            def __init__(self):
                self.vector_store = FakeStore()
                self._telemetry_vector_store = FakeStore()

            def delete_all(self, user_id):
                pass

        provider = Mem0LocalProvider()
        memory = FakeMemory()
        provider._memory = memory

        from basic_memory_benchmarks.models import RunConfig

        provider.cleanup(
            RunConfig(
                run_id="t", dataset_id="t", dataset_path="t", corpus_dir="t", queries_path="t"
            )
        )
        assert memory.vector_store.client.closed
        assert memory._telemetry_vector_store.client.closed
        assert provider._memory is None
