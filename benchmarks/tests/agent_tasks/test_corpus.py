"""Corpus helper tests: every-extension listing (xAFS .eml resources included)."""

from __future__ import annotations

from pathlib import Path

from basic_memory_benchmarks.agent_tasks.corpus import (
    DEFAULT_AGE_DAYS,
    SECONDS_PER_DAY,
    copy_corpus,
    corpus_checksum,
    corpus_files,
    snapshot_baseline,
)


def _write_mixed_corpus(tmp_path: Path) -> Path:
    corpus = tmp_path / "corpus"
    (corpus / "notes").mkdir(parents=True)
    (corpus / "notes" / "a.md").write_text("# alpha\n", encoding="utf-8")
    (corpus / "mail").mkdir()
    (corpus / "mail" / "b.eml").write_text("Subject: hello\n\nbody\n", encoding="utf-8")
    return corpus


def test_corpus_files_lists_every_extension(tmp_path: Path) -> None:
    # Dataset corpora (xAFS) carry non-markdown resources; the checksum, copy,
    # and timestamp passes must all see them, not just *.md.
    corpus = _write_mixed_corpus(tmp_path)

    assert corpus_files(corpus) == ["mail/b.eml", "notes/a.md"]


def test_corpus_checksum_pins_non_markdown_bytes(tmp_path: Path) -> None:
    corpus = _write_mixed_corpus(tmp_path)
    checksum_before, count = corpus_checksum(corpus)

    (corpus / "mail" / "b.eml").write_text("Subject: changed\n\nbody\n", encoding="utf-8")
    checksum_after, count_after = corpus_checksum(corpus)

    assert count == count_after == 2
    assert checksum_before != checksum_after


def test_copy_corpus_copies_and_ages_non_markdown(tmp_path: Path) -> None:
    corpus = _write_mixed_corpus(tmp_path)
    project_dir = tmp_path / "project"
    now = 2_000_000_000.0

    copy_corpus(corpus, project_dir, now=now)

    copied_eml = project_dir / "mail" / "b.eml"
    assert copied_eml.read_bytes() == (corpus / "mail" / "b.eml").read_bytes()
    expected_mtime = now - DEFAULT_AGE_DAYS * SECONDS_PER_DAY
    assert copied_eml.stat().st_mtime == expected_mtime
    assert (project_dir / "notes" / "a.md").stat().st_mtime == expected_mtime


def test_snapshot_baseline_is_markdown_only(tmp_path: Path) -> None:
    # State-tracking graders reason about markdown notes only, and a corpus
    # binary must not crash the snapshot's read_text.
    corpus = _write_mixed_corpus(tmp_path)
    (corpus / "assets").mkdir()
    (corpus / "assets" / "blob.bin").write_bytes(b"\xff\xfe\x00not-utf8")

    assert snapshot_baseline(corpus) == {"notes/a.md": "# alpha\n"}
