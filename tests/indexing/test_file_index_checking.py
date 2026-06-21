"""Tests for portable file-index metadata checking."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory.indexing.file_index_checking import (
    CurrentFileMetadataSource,
    FileIndexChecker,
    RepositoryIndexedFileChecksumSource,
    StorageCurrentFileChecksumSource,
)
from basic_memory.indexing.file_index_planning import FileIndexDecisionStatus, FileIndexTarget


@dataclass(frozen=True, slots=True)
class StubCurrentMetadata:
    checksum: str


@dataclass(slots=True)
class StubCurrentMetadataSource:
    calls: list[str] = field(default_factory=list)

    async def load_current_file_metadata(
        self,
        file_path: str,
    ) -> StubCurrentMetadata | None:
        self.calls.append(file_path)
        if file_path == "missing.md":
            return None
        return StubCurrentMetadata(checksum="etag-current")


@dataclass(slots=True)
class FakeSessionContext:
    session: object

    async def __aenter__(self) -> object:
        return self.session

    async def __aexit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        return None


@dataclass(slots=True)
class FakeSessionMaker:
    session: object

    def __call__(self) -> FakeSessionContext:
        return FakeSessionContext(session=self.session)


@dataclass(slots=True)
class RecordingChecksumRepository:
    rows: list[tuple[object, object | None]]
    calls: list[tuple[object, tuple[str, ...]]] = field(default_factory=list)

    async def get_by_file_paths(
        self,
        session: AsyncSession,
        file_paths: Sequence[str],
    ) -> list[tuple[object, object | None]]:
        self.calls.append((session, tuple(file_paths)))
        return self.rows


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


@pytest.mark.asyncio
async def test_repository_indexed_file_checksum_source_maps_repository_rows() -> None:
    session = object()
    repository = RecordingChecksumRepository(
        rows=[
            ("notes/a.md", "etag-a"),
            ("notes/b.md", None),
        ]
    )
    source = RepositoryIndexedFileChecksumSource(
        session_maker=cast(async_sessionmaker[AsyncSession], FakeSessionMaker(session)),
        entity_repository=repository,
    )

    checksums = await source.load_indexed_file_checksums(["notes/a.md", "notes/b.md"])

    assert checksums == {
        "notes/a.md": "etag-a",
        "notes/b.md": None,
    }
    assert repository.calls == [(session, ("notes/a.md", "notes/b.md"))]


@pytest.mark.asyncio
async def test_storage_current_file_checksum_source_loads_metadata_checksum() -> None:
    stub_source = StubCurrentMetadataSource()
    metadata_source: CurrentFileMetadataSource = stub_source
    source = StorageCurrentFileChecksumSource(metadata_source=metadata_source)

    assert await source.load_current_file_checksum("note.md") == "etag-current"
    assert await source.load_current_file_checksum("missing.md") is None
    assert stub_source.calls == ["note.md", "missing.md"]
