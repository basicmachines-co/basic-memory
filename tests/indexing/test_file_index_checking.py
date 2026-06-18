"""Tests for portable file-index metadata checking."""

from collections.abc import Sequence

import pytest

from basic_memory.indexing.file_index_checking import FileIndexChecker
from basic_memory.indexing.file_index_planning import FileIndexDecisionStatus, FileIndexTarget


class StubIndexedChecksumSource:
    """Returns accepted checksums for indexed paths."""

    def __init__(self, checksums_by_path: dict[str, str | None]) -> None:
        self.checksums_by_path = checksums_by_path
        self.requested_paths: list[tuple[str, ...]] = []

    async def load_indexed_file_checksums(
        self,
        file_paths: Sequence[str],
    ) -> dict[str, str | None]:
        self.requested_paths.append(tuple(file_paths))
        return {
            file_path: self.checksums_by_path[file_path]
            for file_path in file_paths
            if file_path in self.checksums_by_path
        }


class StubCurrentChecksumSource:
    """Returns current storage checksums for paths that need live metadata."""

    def __init__(self, checksums_by_path: dict[str, str | None]) -> None:
        self.checksums_by_path = checksums_by_path
        self.requested_paths: list[str] = []

    async def load_current_file_checksum(self, file_path: str) -> str | None:
        self.requested_paths.append(file_path)
        return self.checksums_by_path[file_path]


@pytest.mark.asyncio
async def test_checker_skips_current_files_before_content_reads() -> None:
    indexed_source = StubIndexedChecksumSource(
        {
            "notes/current.md": "etag-current",
            "notes/caught-up.md": "etag-caught-up",
            "notes/dirty.md": "old-etag",
            "notes/missing.md": "old-etag",
        }
    )
    current_source = StubCurrentChecksumSource(
        {
            "notes/caught-up.md": "etag-caught-up",
            "notes/dirty.md": "etag-dirty",
            "notes/missing.md": None,
        }
    )

    plan = await FileIndexChecker(
        indexed_checksum_source=indexed_source,
        current_checksum_source=current_source,
    ).detect(
        [
            FileIndexTarget(path="notes/current.md", observed_checksum="etag-current"),
            FileIndexTarget(path="notes/caught-up.md", observed_checksum="old-observed"),
            FileIndexTarget(path="notes/dirty.md", observed_checksum="etag-dirty"),
            FileIndexTarget(path="notes/missing.md", observed_checksum="etag-missing"),
        ]
    )

    assert plan.paths_to_read == ("notes/dirty.md",)
    assert [(decision.path, decision.status) for decision in plan.decisions] == [
        ("notes/current.md", FileIndexDecisionStatus.current),
        ("notes/caught-up.md", FileIndexDecisionStatus.current),
        ("notes/missing.md", FileIndexDecisionStatus.missing),
    ]
    assert indexed_source.requested_paths == [
        (
            "notes/current.md",
            "notes/caught-up.md",
            "notes/dirty.md",
            "notes/missing.md",
        )
    ]
    assert current_source.requested_paths == [
        "notes/caught-up.md",
        "notes/dirty.md",
        "notes/missing.md",
    ]


@pytest.mark.asyncio
async def test_checker_reads_legacy_targets_without_metadata() -> None:
    indexed_source = StubIndexedChecksumSource({})
    current_source = StubCurrentChecksumSource({})

    plan = await FileIndexChecker(
        indexed_checksum_source=indexed_source,
        current_checksum_source=current_source,
    ).detect([FileIndexTarget(path="notes/legacy.md")])

    assert plan.paths_to_read == ("notes/legacy.md",)
    assert plan.decisions == ()
    assert indexed_source.requested_paths == []
    assert current_source.requested_paths == []


@pytest.mark.asyncio
async def test_checker_returns_empty_plan_without_sources_for_empty_targets() -> None:
    indexed_source = StubIndexedChecksumSource({})
    current_source = StubCurrentChecksumSource({})

    plan = await FileIndexChecker(
        indexed_checksum_source=indexed_source,
        current_checksum_source=current_source,
    ).detect([])

    assert plan.paths_to_read == ()
    assert plan.decisions == ()
    assert indexed_source.requested_paths == []
    assert current_source.requested_paths == []
