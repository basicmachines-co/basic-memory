"""Tests for deterministic indexing batch planning."""

from basic_memory.indexing import IndexFileMetadata
from basic_memory.indexing.batching import build_index_batches


def test_build_index_batches_respects_max_files() -> None:
    metadata = {
        f"note-{index}.md": IndexFileMetadata(path=f"note-{index}.md", size=10)
        for index in range(5)
    }

    batches = build_index_batches(
        list(metadata),
        metadata,
        max_files=2,
        max_bytes=10_000,
    )

    assert [batch.paths for batch in batches] == [
        ["note-0.md", "note-1.md"],
        ["note-2.md", "note-3.md"],
        ["note-4.md"],
    ]


def test_build_index_batches_respects_max_bytes() -> None:
    metadata = {
        "a.md": IndexFileMetadata(path="a.md", size=30),
        "b.md": IndexFileMetadata(path="b.md", size=40),
        "c.md": IndexFileMetadata(path="c.md", size=50),
    }

    batches = build_index_batches(
        ["c.md", "a.md", "b.md"],
        metadata,
        max_files=10,
        max_bytes=70,
    )

    assert [(batch.paths, batch.total_bytes) for batch in batches] == [
        (["a.md", "b.md"], 70),
        (["c.md"], 50),
    ]


def test_build_index_batches_puts_giant_file_in_single_file_batch() -> None:
    metadata = {
        "alpha.md": IndexFileMetadata(path="alpha.md", size=10),
        "giant.md": IndexFileMetadata(path="giant.md", size=500),
        "omega.md": IndexFileMetadata(path="omega.md", size=10),
    }

    batches = build_index_batches(
        list(metadata),
        metadata,
        max_files=10,
        max_bytes=100,
    )

    assert [(batch.paths, batch.total_bytes) for batch in batches] == [
        (["alpha.md"], 10),
        (["giant.md"], 500),
        (["omega.md"], 10),
    ]


def test_build_index_batches_is_deterministic() -> None:
    metadata = {
        "notes/b.md": IndexFileMetadata(path="notes/b.md", size=10),
        "notes/a.md": IndexFileMetadata(path="notes/a.md", size=10),
        "notes/c.md": IndexFileMetadata(path="notes/c.md", size=10),
    }

    batches = build_index_batches(
        ["notes/c.md", "notes/a.md", "notes/b.md"],
        metadata,
        max_files=2,
        max_bytes=1_000,
    )

    assert [batch.paths for batch in batches] == [
        ["notes/a.md", "notes/b.md"],
        ["notes/c.md"],
    ]
