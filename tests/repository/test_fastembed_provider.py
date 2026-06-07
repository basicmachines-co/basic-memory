"""Tests for FastEmbedEmbeddingProvider."""

import builtins
import math
import sys
from dataclasses import dataclass

import pytest

from basic_memory.repository.fastembed_provider import FastEmbedEmbeddingProvider
from basic_memory.repository.semantic_errors import SemanticDependenciesMissingError


class _StubVector:
    def __init__(self, values):
        self._values = values

    def tolist(self):
        return self._values


class _StubTextEmbedding:
    init_count = 0
    last_init_kwargs: dict = {}
    last_embed_kwargs: dict = {}

    def __init__(self, model_name: str, cache_dir: str | None = None, threads: int | None = None):
        self.model_name = model_name
        self.embed_calls = 0
        _StubTextEmbedding.last_init_kwargs = {
            "model_name": model_name,
            "cache_dir": cache_dir,
            "threads": threads,
        }
        _StubTextEmbedding.init_count += 1

    def embed(self, texts: list[str], batch_size: int = 64, **kwargs):
        self.embed_calls += 1
        _StubTextEmbedding.last_embed_kwargs = {"batch_size": batch_size, **kwargs}
        for text in texts:
            if "wide" in text:
                yield _StubVector([1.0, 0.0, 0.0, 0.0, 0.5])
            else:
                yield _StubVector([1.0, 0.0, 0.0, 0.0])


@pytest.mark.asyncio
async def test_fastembed_provider_lazy_loads_and_reuses_model(monkeypatch):
    """Provider should instantiate FastEmbed lazily and reuse the loaded model."""
    module = type(sys)("fastembed")
    setattr(module, "TextEmbedding", _StubTextEmbedding)
    monkeypatch.setitem(sys.modules, "fastembed", module)
    _StubTextEmbedding.init_count = 0

    provider = FastEmbedEmbeddingProvider(model_name="stub-model", dimensions=4)
    assert provider._model is None

    first = await provider.embed_query("auth query")
    second = await provider.embed_documents(["database query"])

    assert _StubTextEmbedding.init_count == 1
    assert provider._model is not None
    assert len(first) == 4
    assert len(second) == 1
    assert len(second[0]) == 4


@pytest.mark.asyncio
async def test_fastembed_provider_dimension_mismatch_raises_error(monkeypatch):
    """Provider should fail fast when model output dimensions differ from configured dimensions."""
    module = type(sys)("fastembed")
    setattr(module, "TextEmbedding", _StubTextEmbedding)
    monkeypatch.setitem(sys.modules, "fastembed", module)

    provider = FastEmbedEmbeddingProvider(model_name="stub-model", dimensions=4)
    with pytest.raises(RuntimeError, match="5-dimensional vectors"):
        await provider.embed_documents(["wide vector"])


@pytest.mark.asyncio
async def test_fastembed_provider_missing_dependency_raises_actionable_error(monkeypatch):
    """Missing fastembed package should raise SemanticDependenciesMissingError."""
    monkeypatch.delitem(sys.modules, "fastembed", raising=False)
    original_import = builtins.__import__

    def _raising_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "fastembed":
            raise ImportError("fastembed not installed")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", _raising_import)

    provider = FastEmbedEmbeddingProvider(model_name="stub-model")
    with pytest.raises(SemanticDependenciesMissingError) as error:
        await provider.embed_query("test")

    assert "pip install -U basic-memory" in str(error.value)


@pytest.mark.asyncio
async def test_fastembed_provider_passes_runtime_knobs_to_fastembed(monkeypatch):
    """Provider should pass optional runtime tuning knobs through to FastEmbed."""
    module = type(sys)("fastembed")
    setattr(module, "TextEmbedding", _StubTextEmbedding)
    monkeypatch.setitem(sys.modules, "fastembed", module)
    _StubTextEmbedding.last_init_kwargs = {}
    _StubTextEmbedding.last_embed_kwargs = {}

    provider = FastEmbedEmbeddingProvider(
        model_name="stub-model",
        dimensions=4,
        batch_size=8,
        cache_dir="/tmp/fastembed-cache",
        threads=3,
        parallel=2,
    )
    await provider.embed_documents(["runtime knobs"])

    assert _StubTextEmbedding.last_init_kwargs == {
        "model_name": "stub-model",
        "cache_dir": "/tmp/fastembed-cache",
        "threads": 3,
    }
    assert _StubTextEmbedding.last_embed_kwargs == {"batch_size": 8, "parallel": 2}


@pytest.mark.asyncio
async def test_fastembed_provider_parallel_one_disables_multiprocessing(monkeypatch):
    """parallel=1 should not pass FastEmbed multiprocessing kwargs."""
    module = type(sys)("fastembed")
    setattr(module, "TextEmbedding", _StubTextEmbedding)
    monkeypatch.setitem(sys.modules, "fastembed", module)
    _StubTextEmbedding.last_embed_kwargs = {}

    provider = FastEmbedEmbeddingProvider(model_name="stub-model", dimensions=4, parallel=1)
    await provider.embed_documents(["parallel guardrail"])

    assert _StubTextEmbedding.last_embed_kwargs == {"batch_size": 64}


@pytest.mark.asyncio
async def test_fastembed_provider_parallel_two_passes_multiprocessing(monkeypatch):
    """parallel>1 should keep passing FastEmbed multiprocessing kwargs."""
    module = type(sys)("fastembed")
    setattr(module, "TextEmbedding", _StubTextEmbedding)
    monkeypatch.setitem(sys.modules, "fastembed", module)
    _StubTextEmbedding.last_embed_kwargs = {}

    provider = FastEmbedEmbeddingProvider(model_name="stub-model", dimensions=4, parallel=2)
    await provider.embed_documents(["parallel enabled"])

    assert _StubTextEmbedding.last_embed_kwargs == {"batch_size": 64, "parallel": 2}


class _UnormalizedVector:
    """Stub vector with norm != 1 (simulates multilingual models like paraphrase-multilingual-*)."""

    def __init__(self, values):
        self._values = values

    def tolist(self):
        return self._values


class _UnnormalizedTextEmbedding:
    def __init__(self, model_name: str, **_kwargs):
        self.model_name = model_name

    def embed(self, texts: list[str], **_kwargs):
        # Return a vector with norm ~= 2.9 (typical for multilingual MiniLM models)
        for _ in texts:
            yield _UnormalizedVector([1.5, 2.0, 1.0, 0.5])


@pytest.mark.asyncio
async def test_fastembed_provider_l2_normalizes_output_vectors(monkeypatch):
    """Returned vectors must be unit-normalized regardless of the raw model output.

    sqlite_search_repository uses a formula that assumes norm == 1. Models such as
    paraphrase-multilingual-MiniLM-L12-v2 return vectors with norm ~2.9, which breaks
    cosine similarity scoring. The provider must apply L2 normalization before returning.
    """
    module = type(sys)("fastembed")
    setattr(module, "TextEmbedding", _UnnormalizedTextEmbedding)
    monkeypatch.setitem(sys.modules, "fastembed", module)

    provider = FastEmbedEmbeddingProvider(model_name="stub-multilingual", dimensions=4)
    result = await provider.embed_documents(["some text"])

    assert len(result) == 1
    norm = math.sqrt(sum(x * x for x in result[0]))
    assert abs(norm - 1.0) < 1e-6, f"Expected unit norm, got {norm}"


@pytest.mark.asyncio
async def test_fastembed_provider_zero_vector_does_not_raise(monkeypatch):
    """A zero vector from the model must be returned as-is without a division error."""

    class _ZeroEmbedding:
        def __init__(self, model_name: str, **_kwargs):
            pass

        def embed(self, texts: list[str], **_kwargs):
            for _ in texts:
                yield _UnormalizedVector([0.0, 0.0, 0.0, 0.0])

    module = type(sys)("fastembed")
    setattr(module, "TextEmbedding", _ZeroEmbedding)
    monkeypatch.setitem(sys.modules, "fastembed", module)

    provider = FastEmbedEmbeddingProvider(model_name="stub-zero", dimensions=4)
    result = await provider.embed_documents(["zero vector"])

    assert result == [[0.0, 0.0, 0.0, 0.0]]


# --- Self-heal of corrupt/partial model cache (#895) ---
#
# A real interrupted FastEmbed download is non-deterministic and offline-unfriendly, so we
# stub TextEmbedding to (a) advertise an HF source + model_file via _list_supported_models so
# the provider can compute the exact models--<org>--<repo> cache subdir and the artifact name,
# and (b) raise a NO_SUCHFILE-style ONNX error on the first construction. This is the justified
# mock case called out in the task. The purge is gated on a filesystem confirmation that the
# snapshot dir exists but the artifact is missing, so each test stages the cache accordingly.


@dataclass
class _StubModelSource:
    hf: str


@dataclass
class _StubModelDescription:
    model: str
    sources: _StubModelSource
    model_file: str = "model_optimized.onnx"


class _SelfHealStubTextEmbedding:
    """Raises a NO_SUCHFILE-style ONNX error on the first N constructions, then succeeds."""

    fail_first_n = 1
    construct_count = 0
    HF_SOURCE = "stub-org/stub-model-onnx-q"
    RESOLVED_MODEL = "stub-model"
    MODEL_FILE = "model_optimized.onnx"

    def __init__(self, model_name: str, cache_dir: str | None = None, threads: int | None = None):
        type(self).construct_count += 1
        if type(self).construct_count <= type(self).fail_first_n:
            raise RuntimeError(
                "[ONNXRuntimeError] : 3 : NO_SUCHFILE : Load model from "
                f"{cache_dir}/models--stub-org--stub-model-onnx-q/snapshots/abc123/"
                "model_optimized.onnx failed. File doesn't exist"
            )
        self.model_name = model_name

    def embed(self, texts: list[str], batch_size: int = 64, **kwargs):
        for _ in texts:
            yield _StubVector([1.0, 0.0, 0.0, 0.0])

    @classmethod
    def _list_supported_models(cls):
        # Include decoys so the resolver's skip branches are exercised: a model with a
        # different name (name-mismatch skip) and one with an empty HF source (no-source skip).
        return [
            _StubModelDescription(
                model="some-other-model",
                sources=_StubModelSource(hf="other-org/other-model"),
                model_file=cls.MODEL_FILE,
            ),
            _StubModelDescription(
                model=cls.RESOLVED_MODEL,
                sources=_StubModelSource(hf=""),
                model_file=cls.MODEL_FILE,
            ),
            _StubModelDescription(
                model=cls.RESOLVED_MODEL,
                sources=_StubModelSource(hf=cls.HF_SOURCE),
                model_file=cls.MODEL_FILE,
            ),
        ]


def _install_self_heal_stub(monkeypatch):
    module = type(sys)("fastembed")
    setattr(module, "TextEmbedding", _SelfHealStubTextEmbedding)
    monkeypatch.setitem(sys.modules, "fastembed", module)
    _SelfHealStubTextEmbedding.construct_count = 0
    _SelfHealStubTextEmbedding.fail_first_n = 1
    _SelfHealStubTextEmbedding.RESOLVED_MODEL = "stub-model"


@pytest.mark.asyncio
async def test_fastembed_provider_self_heals_corrupt_model_cache(monkeypatch, tmp_path):
    """A NO_SUCHFILE load failure should purge the model cache subdir and retry once."""
    _install_self_heal_stub(monkeypatch)

    # Simulate the partial-download artifact: the model's HF cache subdir exists on disk
    # but is incomplete. The provider must remove exactly this subdir, not the whole cache.
    cache_dir = tmp_path / "fastembed_cache"
    model_subdir = cache_dir / "models--stub-org--stub-model-onnx-q"
    model_subdir.mkdir(parents=True)
    (model_subdir / "stale.bin").write_text("partial download")
    unrelated_subdir = cache_dir / "models--other--keep-me"
    unrelated_subdir.mkdir(parents=True)
    (unrelated_subdir / "data.bin").write_text("do not delete")

    provider = FastEmbedEmbeddingProvider(
        model_name="stub-model", dimensions=4, cache_dir=str(cache_dir)
    )

    vectors = await provider.embed_documents(["recover after corrupt cache"])

    # Construction was attempted exactly twice: the failing load, then the post-purge retry.
    assert _SelfHealStubTextEmbedding.construct_count == 2
    # The corrupt model subdir was removed; the unrelated model cache was untouched.
    assert not model_subdir.exists()
    assert unrelated_subdir.exists()
    assert (unrelated_subdir / "data.bin").read_text() == "do not delete"
    # The retry produced real vectors.
    assert len(vectors) == 1
    assert len(vectors[0]) == 4


@pytest.mark.asyncio
async def test_fastembed_provider_fails_fast_on_persistent_corrupt_cache(monkeypatch, tmp_path):
    """A second consecutive NO_SUCHFILE failure must fail fast (no infinite retry loop)."""
    _install_self_heal_stub(monkeypatch)
    # Both constructions fail — the retry does not loop.
    _SelfHealStubTextEmbedding.fail_first_n = 2

    cache_dir = tmp_path / "fastembed_cache"
    model_subdir = cache_dir / "models--stub-org--stub-model-onnx-q"
    model_subdir.mkdir(parents=True)

    provider = FastEmbedEmbeddingProvider(
        model_name="stub-model", dimensions=4, cache_dir=str(cache_dir)
    )

    with pytest.raises(RuntimeError, match="NO_SUCHFILE"):
        await provider.embed_documents(["still broken"])

    # Exactly one retry: two total construction attempts, then fail fast.
    assert _SelfHealStubTextEmbedding.construct_count == 2


@pytest.mark.asyncio
async def test_fastembed_provider_fails_fast_when_purge_silently_noops(monkeypatch, tmp_path):
    """If rmtree silently fails (e.g. Windows locked files), do not claim success or retry.

    shutil.rmtree(ignore_errors=True) can no-op when a file is locked. Treating that as a
    successful purge would retry against the same broken cache; instead the load must fail
    fast with the original error. We inject a no-op rmtree to simulate the locked-file case.
    """
    import basic_memory.repository.fastembed_provider as fastembed_provider

    _install_self_heal_stub(monkeypatch)

    cache_dir = tmp_path / "fastembed_cache"
    model_subdir = cache_dir / "models--stub-org--stub-model-onnx-q"
    model_subdir.mkdir(parents=True)
    (model_subdir / "stale.bin").write_text("partial download")

    # Simulate a deletion that silently fails to remove the directory.
    monkeypatch.setattr(
        fastembed_provider.shutil, "rmtree", lambda *args, **kwargs: None, raising=True
    )

    provider = FastEmbedEmbeddingProvider(
        model_name="stub-model", dimensions=4, cache_dir=str(cache_dir)
    )

    with pytest.raises(RuntimeError, match="NO_SUCHFILE"):
        await provider.embed_documents(["locked cache"])

    # rmtree no-oped, so the subdir survives and no retry was attempted.
    assert model_subdir.exists()
    assert _SelfHealStubTextEmbedding.construct_count == 1


@pytest.mark.asyncio
async def test_fastembed_provider_does_not_purge_on_unrelated_error(monkeypatch, tmp_path):
    """A non-cache load error must propagate without deleting any cache subdir."""

    class _ConfigErrorTextEmbedding:
        construct_count = 0

        def __init__(self, model_name: str, cache_dir: str | None = None, **_kwargs):
            type(self).construct_count += 1
            raise ValueError("invalid model configuration")

        @classmethod
        def _list_supported_models(cls):
            return [
                _StubModelDescription(
                    model="stub-model",
                    sources=_StubModelSource(hf="stub-org/stub-model-onnx-q"),
                )
            ]

    module = type(sys)("fastembed")
    setattr(module, "TextEmbedding", _ConfigErrorTextEmbedding)
    monkeypatch.setitem(sys.modules, "fastembed", module)

    cache_dir = tmp_path / "fastembed_cache"
    model_subdir = cache_dir / "models--stub-org--stub-model-onnx-q"
    model_subdir.mkdir(parents=True)
    (model_subdir / "keep.bin").write_text("keep")

    provider = FastEmbedEmbeddingProvider(
        model_name="stub-model", dimensions=4, cache_dir=str(cache_dir)
    )

    with pytest.raises(ValueError, match="invalid model configuration"):
        await provider.embed_documents(["bad config"])

    # No retry and no deletion for errors that are not missing-artifact failures.
    assert _ConfigErrorTextEmbedding.construct_count == 1
    assert model_subdir.exists()


@pytest.mark.asyncio
async def test_fastembed_provider_cold_load_does_not_purge_or_retry(monkeypatch, tmp_path):
    """A cold load (snapshot dir absent) must NOT be misread as corruption.

    This is the CI happy-path regression: on a cold model cache the first load can fail
    before the model is downloaded, but with no snapshot dir there is nothing corrupt to
    purge. The original error must propagate unchanged with no retry, so a normal
    not-yet-downloaded model is never deleted.
    """
    _install_self_heal_stub(monkeypatch)

    cache_dir = tmp_path / "fastembed_cache"
    cache_dir.mkdir(parents=True)
    # Intentionally do NOT create the model subdir: this is a normal cold load.

    provider = FastEmbedEmbeddingProvider(
        model_name="stub-model", dimensions=4, cache_dir=str(cache_dir)
    )

    with pytest.raises(RuntimeError, match="NO_SUCHFILE"):
        await provider.embed_documents(["nothing to purge"])

    # Only the initial attempt ran — no snapshot dir means no confirmed corruption, no retry.
    assert _SelfHealStubTextEmbedding.construct_count == 1


@pytest.mark.asyncio
async def test_fastembed_provider_does_not_purge_when_artifact_present(monkeypatch, tmp_path):
    """A NO_SUCHFILE-shaped error must NOT purge when the artifact is actually on disk.

    The error-text gate alone is not enough: if filesystem inspection finds the model
    artifact present in the snapshot, the cache is not corrupt and must be left intact.
    Re-raise the original error rather than deleting a healthy cache.
    """
    _install_self_heal_stub(monkeypatch)
    # Construction keeps failing with the NO_SUCHFILE text regardless of cache state.
    _SelfHealStubTextEmbedding.fail_first_n = 99

    cache_dir = tmp_path / "fastembed_cache"
    snapshot_dir = cache_dir / "models--stub-org--stub-model-onnx-q" / "snapshots" / "rev1"
    snapshot_dir.mkdir(parents=True)
    artifact = snapshot_dir / "model_optimized.onnx"
    artifact.write_text("valid model artifact")

    provider = FastEmbedEmbeddingProvider(
        model_name="stub-model", dimensions=4, cache_dir=str(cache_dir)
    )

    with pytest.raises(RuntimeError, match="NO_SUCHFILE"):
        await provider.embed_documents(["artifact is fine"])

    # No purge and no retry: the artifact is present, so the cache is not corrupt.
    assert _SelfHealStubTextEmbedding.construct_count == 1
    assert artifact.exists()
    assert artifact.read_text() == "valid model artifact"


@pytest.mark.asyncio
async def test_fastembed_provider_self_heals_when_current_revision_corrupt(monkeypatch, tmp_path):
    """A corrupt current revision must be detected even when an older revision is complete.

    HuggingFace keeps multiple revisions under one models--<repo> tree. Per-revision
    inspection is required: a whole-tree rglob would find the OLD revision's artifact and
    wrongly conclude the cache is healthy, leaving the broken current snapshot
    self-perpetuating (PR #900 review).
    """
    _install_self_heal_stub(monkeypatch)

    cache_dir = tmp_path / "fastembed_cache"
    snapshots = cache_dir / "models--stub-org--stub-model-onnx-q" / "snapshots"
    # Old revision: complete (has the artifact).
    good_rev = snapshots / "rev_old"
    good_rev.mkdir(parents=True)
    (good_rev / "model_optimized.onnx").write_text("complete old artifact")
    # Current revision: interrupted download — directory present, artifact missing.
    bad_rev = snapshots / "rev_current"
    bad_rev.mkdir(parents=True)
    (bad_rev / "stale.partial").write_text("partial download")

    provider = FastEmbedEmbeddingProvider(
        model_name="stub-model", dimensions=4, cache_dir=str(cache_dir)
    )

    vectors = await provider.embed_documents(["recover from mixed-revision cache"])

    # The corrupt-current-revision cache was detected (not masked by the old revision),
    # purged, and the retry succeeded.
    assert _SelfHealStubTextEmbedding.construct_count == 2
    assert not (cache_dir / "models--stub-org--stub-model-onnx-q").exists()
    assert len(vectors) == 1


@pytest.mark.asyncio
async def test_fastembed_provider_self_heals_with_case_insensitive_model_name(
    monkeypatch, tmp_path
):
    """A lower-cased model name must still resolve the HF cache subdir for the purge.

    FastEmbed matches model names case-insensitively, so a config like
    model="baai/bge-small-en-v1.5" is valid. The purge resolver must mirror that, otherwise
    the corrupt subdir resolves to nothing and self-heal silently does nothing.
    """
    _install_self_heal_stub(monkeypatch)
    # Advertise the model under its canonical mixed-case name.
    _SelfHealStubTextEmbedding.RESOLVED_MODEL = "Stub-Model"

    cache_dir = tmp_path / "fastembed_cache"
    model_subdir = cache_dir / "models--stub-org--stub-model-onnx-q"
    model_subdir.mkdir(parents=True)
    (model_subdir / "stale.bin").write_text("partial download")

    # Configure the provider with the lower-cased spelling.
    provider = FastEmbedEmbeddingProvider(
        model_name="stub-model", dimensions=4, cache_dir=str(cache_dir)
    )

    vectors = await provider.embed_documents(["recover with case-insensitive name"])

    assert _SelfHealStubTextEmbedding.construct_count == 2
    assert not model_subdir.exists()
    assert len(vectors) == 1


@pytest.mark.asyncio
async def test_fastembed_provider_fails_fast_without_cache_dir(monkeypatch):
    """Without a configured cache_dir there is nothing to purge, so fail fast."""
    _install_self_heal_stub(monkeypatch)

    # cache_dir defaults to None — _model_cache_candidates() returns no candidates.
    provider = FastEmbedEmbeddingProvider(model_name="stub-model", dimensions=4)

    with pytest.raises(RuntimeError, match="NO_SUCHFILE"):
        await provider.embed_documents(["no cache dir"])

    assert _SelfHealStubTextEmbedding.construct_count == 1
