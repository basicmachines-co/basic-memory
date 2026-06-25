"""Tests for the grep and full-context baseline providers."""

from __future__ import annotations

from pathlib import Path

from basic_memory_benchmarks.models import RunConfig
from basic_memory_benchmarks.providers import create_provider
from basic_memory_benchmarks.providers.baseline_fullcontext import (
    MAX_CONTEXT_CHARS,
    FullContextProvider,
)
from basic_memory_benchmarks.providers.baseline_grep import FilesystemGrepProvider


def _write_doc(corpus: Path, doc_id: str, body: str) -> None:
    corpus.mkdir(parents=True, exist_ok=True)
    (corpus / f"{doc_id}.md").write_text(
        f"---\ntitle: {doc_id}\nsource_doc_id: {doc_id}\n---\n\n{body}\n",
        encoding="utf-8",
    )


def _config(tmp_path: Path) -> RunConfig:
    return RunConfig(
        run_id="t",
        dataset_id="t",
        dataset_path="t",
        corpus_dir=str(tmp_path),
        queries_path="t",
    )


class TestFilesystemGrep:
    def test_ranks_matching_doc_first(self, tmp_path):
        corpus = tmp_path / "docs"
        _write_doc(corpus, "doc-a", "Joanna moved to Austin and loves the food scene in Austin.")
        _write_doc(corpus, "doc-b", "Anthony trains for marathons every morning.")
        provider = FilesystemGrepProvider()
        provider.ingest(corpus, _config(tmp_path))

        hits = provider.search("Where does Joanna live in Austin?", 5, _config(tmp_path))

        assert hits[0].source_doc_id == "doc-a"
        assert hits[0].score > 0
        assert "Joanna" in (hits[0].text or "")

    def test_coverage_beats_repetition(self, tmp_path):
        corpus = tmp_path / "docs"
        # doc-spam repeats one term; doc-both matches both distinct terms.
        _write_doc(corpus, "doc-spam", "dentist " * 30)
        _write_doc(corpus, "doc-both", "I visited the dentist in November.")
        provider = FilesystemGrepProvider()
        provider.ingest(corpus, _config(tmp_path))

        hits = provider.search("dentist November", 5, _config(tmp_path))

        assert hits[0].source_doc_id == "doc-both"

    def test_no_content_terms_returns_empty(self, tmp_path):
        corpus = tmp_path / "docs"
        _write_doc(corpus, "doc-a", "anything")
        provider = FilesystemGrepProvider()
        provider.ingest(corpus, _config(tmp_path))
        assert provider.search("what did they do", 5, _config(tmp_path)) == []

    def test_limit_respected(self, tmp_path):
        corpus = tmp_path / "docs"
        for i in range(10):
            _write_doc(corpus, f"doc-{i}", "Austin is great.")
        provider = FilesystemGrepProvider()
        provider.ingest(corpus, _config(tmp_path))
        assert len(provider.search("Austin", 3, _config(tmp_path))) == 3

    def test_factory_registration(self):
        assert isinstance(create_provider("baseline-grep"), FilesystemGrepProvider)


class TestFullContext:
    def test_returns_whole_corpus_as_single_hit(self, tmp_path):
        corpus = tmp_path / "docs"
        _write_doc(corpus, "doc-a", "Joanna moved to Austin.")
        _write_doc(corpus, "doc-b", "Anthony runs marathons.")
        provider = FullContextProvider()
        provider.ingest(corpus, _config(tmp_path))

        hits = provider.search("anything", 10, _config(tmp_path))

        assert len(hits) == 1
        assert "Joanna moved to Austin." in (hits[0].text or "")
        assert "Anthony runs marathons." in (hits[0].text or "")
        assert hits[0].source_doc_id is None  # no retrieval claims

    def test_truncation_capped_and_flagged(self, tmp_path):
        corpus = tmp_path / "docs"
        _write_doc(corpus, "doc-big", "x" * (MAX_CONTEXT_CHARS + 1000))
        provider = FullContextProvider()
        provider.ingest(corpus, _config(tmp_path))

        hits = provider.search("anything", 10, _config(tmp_path))

        assert len(hits[0].text or "") <= MAX_CONTEXT_CHARS
        assert hits[0].metadata["truncated"] is True

    def test_cleanup_clears_context(self, tmp_path):
        corpus = tmp_path / "docs"
        _write_doc(corpus, "doc-a", "content")
        provider = FullContextProvider()
        provider.ingest(corpus, _config(tmp_path))
        provider.cleanup(_config(tmp_path))
        assert provider.search("anything", 10, _config(tmp_path)) == []

    def test_factory_registration(self):
        assert isinstance(create_provider("baseline-fullcontext"), FullContextProvider)
