"""Tests for portable project file change planning."""

from collections.abc import Mapping
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import AsyncIterator, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import basic_memory.indexing.change_detector as change_detector_module
from basic_memory.indexing.change_planning import (
    ChangeDetectionSnapshot,
    ChangeReport,
    FileMoveCandidate,
    plan_change_detection_snapshot,
    plan_file_changes,
    plan_move_target_checksums,
    storage_checksums_from_sources,
)
from basic_memory.indexing.change_detector import (
    MAX_QUERY_BIND_PARAMETERS,
    ChangeDetector,
    detect_project_file_changes,
)
from basic_memory.repository import EntityRepository


class StorageObject:
    def __init__(self, checksum: str) -> None:
        self.checksum = checksum


def test_plan_change_detection_snapshot_maps_typed_runtime_state() -> None:
    storage_checksum_by_path = storage_checksums_from_sources(
        {
            "unchanged.md": StorageObject("same-checksum"),
            "modified.md": StorageObject("new-checksum"),
            "new/moved.md": StorageObject("moved-checksum"),
            "new.md": StorageObject("new-file-checksum"),
        }
    )
    snapshot = ChangeDetectionSnapshot(
        storage_checksum_by_path=storage_checksum_by_path,
        db_checksum_by_path={
            "unchanged.md": "same-checksum",
            "modified.md": "old-checksum",
        },
        all_db_paths=("unchanged.md", "modified.md", "old/moved.md", "deleted.md"),
        move_candidates=(FileMoveCandidate(path="old/moved.md", checksum="moved-checksum"),),
    )

    assert plan_move_target_checksums(
        storage_checksum_by_path=snapshot.storage_checksum_by_path,
        db_checksum_by_path=snapshot.db_checksum_by_path,
    ) == {
        "new/moved.md": "moved-checksum",
        "new.md": "new-file-checksum",
    }
    assert plan_change_detection_snapshot(snapshot) == ChangeReport(
        new_files=["new.md"],
        modified_files=["modified.md"],
        deleted_files=["deleted.md"],
        moved_files={"old/moved.md": "new/moved.md"},
        unchanged_files=["unchanged.md"],
    )


def test_plan_file_changes_keeps_unobservable_files_out_of_deletes() -> None:
    """An existing file whose checksum could not be read is modified, not deleted."""
    report = plan_file_changes(
        storage_checksum_by_path={
            "unreadable.md": None,
            "unreadable-new.md": None,
        },
        db_checksum_by_path={"unreadable.md": "indexed-checksum"},
        all_db_paths=("unreadable.md", "moved-away.md"),
        move_candidates=(FileMoveCandidate(path="moved-away.md", checksum="indexed-checksum"),),
    )

    assert report == ChangeReport(
        new_files=["unreadable-new.md"],
        modified_files=["unreadable.md"],
        deleted_files=["moved-away.md"],
        moved_files={},
        unchanged_files=[],
    )


def test_plan_move_target_checksums_excludes_unknown_checksums() -> None:
    """Unknown checksums carry no content evidence, so they cannot claim a move."""
    move_target_checksums = plan_move_target_checksums(
        storage_checksum_by_path={"new.md": "new-checksum", "unreadable.md": None},
        db_checksum_by_path={},
    )

    assert move_target_checksums == {"new.md": "new-checksum"}


def test_plan_file_changes_detects_new_modified_unchanged_and_deleted_files() -> None:
    report = plan_file_changes(
        storage_checksum_by_path={
            "unchanged.md": "same-checksum",
            "modified.md": "new-checksum",
            "new.md": "new-file-checksum",
        },
        db_checksum_by_path={
            "unchanged.md": "same-checksum",
            "modified.md": "old-checksum",
        },
        all_db_paths=("unchanged.md", "modified.md", "deleted.md"),
        move_candidates=(),
    )

    assert report == ChangeReport(
        new_files=["new.md"],
        modified_files=["modified.md"],
        deleted_files=["deleted.md"],
        moved_files={},
        unchanged_files=["unchanged.md"],
    )


def test_plan_file_changes_removes_moved_files_from_new_and_deleted_sets() -> None:
    report = plan_file_changes(
        storage_checksum_by_path={"new/note.md": "move-checksum"},
        db_checksum_by_path={},
        all_db_paths=("old/note.md",),
        move_candidates=(FileMoveCandidate(path="old/note.md", checksum="move-checksum"),),
    )

    assert report.new_files == []
    assert report.deleted_files == []
    assert report.moved_files == {"old/note.md": "new/note.md"}
    assert report.total_changes == 1


def test_plan_file_changes_never_moves_onto_existing_indexed_path() -> None:
    # Regression: target.md was edited in place and its new content happens to
    # match deleted source.md's checksum. Classifying that as a move would
    # redirect source.md's entity onto target.md's path and drop target.md's
    # edit. The edit and the delete must both survive.
    report = plan_file_changes(
        storage_checksum_by_path={"target.md": "source-checksum"},
        db_checksum_by_path={"target.md": "target-checksum"},
        all_db_paths=("target.md", "source.md"),
        move_candidates=(FileMoveCandidate(path="source.md", checksum="source-checksum"),),
    )

    assert report.moved_files == {}
    assert report.modified_files == ["target.md"]
    assert report.deleted_files == ["source.md"]
    assert report.new_files == []
    assert report.total_changes == 2


def test_plan_file_changes_treats_copy_as_new_when_original_path_still_exists() -> None:
    report = plan_file_changes(
        storage_checksum_by_path={
            "original.md": "shared-checksum",
            "copy.md": "shared-checksum",
        },
        db_checksum_by_path={"original.md": "shared-checksum"},
        all_db_paths=("original.md",),
        move_candidates=(FileMoveCandidate(path="original.md", checksum="shared-checksum"),),
    )

    assert report.moved_files == {}
    assert report.new_files == ["copy.md"]
    assert report.unchanged_files == ["original.md"]


def test_change_report_helper_properties_count_real_changes() -> None:
    report = ChangeReport(
        new_files=["new.md"],
        modified_files=["modified.md"],
        deleted_files=["deleted.md"],
        moved_files={"old.md": "new-name.md"},
        unchanged_files=["same.md"],
    )

    assert report.has_changes is True
    assert report.total_changes == 4
    assert ChangeReport().has_changes is False


class FakeChangeDetectionStore:
    def __init__(self) -> None:
        self.loaded_checksum_paths: tuple[str, ...] | None = None
        self.loaded_move_checksums: dict[str, str] | None = None

    async def load_indexed_file_checksums(self, paths: tuple[str, ...]) -> dict[str, str]:
        self.loaded_checksum_paths = paths
        return {
            "unchanged.md": "same-checksum",
            "modified.md": "old-checksum",
        }

    async def load_all_indexed_paths(self) -> tuple[str, ...]:
        return ("unchanged.md", "modified.md", "old/moved.md", "deleted.md")

    async def load_move_candidates(
        self,
        move_target_checksums: Mapping[str, str],
    ) -> tuple[FileMoveCandidate, ...]:
        self.loaded_move_checksums = dict(move_target_checksums)
        return (FileMoveCandidate(path="old/moved.md", checksum="moved-checksum"),)


class FakeEntityRepository:
    def __init__(self) -> None:
        self.loaded_checksum_paths: tuple[str, ...] | None = None
        self.loaded_move_checksums: tuple[str, ...] | None = None
        self.loaded_all_paths = False

    async def get_by_file_paths(
        self,
        session: object,
        paths: tuple[str, ...],
    ) -> list[tuple[str, str | None]]:
        self.loaded_checksum_paths = paths
        return [
            ("unchanged.md", "same-checksum"),
            ("modified.md", "old-checksum"),
            ("null-checksum.md", None),
        ]

    async def find_by_checksums(
        self,
        session: object,
        checksums: list[str],
    ) -> tuple[SimpleNamespace, ...]:
        self.loaded_move_checksums = tuple(checksums)
        return (
            SimpleNamespace(file_path="old/moved.md", checksum="moved-checksum"),
            SimpleNamespace(file_path="ignored.md", checksum=None),
        )

    async def get_all_file_paths(self, session: object) -> list[str]:
        self.loaded_all_paths = True
        return [
            "unchanged.md",
            "modified.md",
            "old/moved.md",
            "deleted.md",
            "null-checksum.md",
        ]


@pytest.mark.asyncio
async def test_detect_project_file_changes_loads_store_state_and_plans_moves() -> None:
    store = FakeChangeDetectionStore()

    report = await detect_project_file_changes(
        {
            "unchanged.md": StorageObject("same-checksum"),
            "modified.md": StorageObject("new-checksum"),
            "new/moved.md": StorageObject("moved-checksum"),
            "new.md": StorageObject("new-file-checksum"),
        },
        store=store,
    )

    assert store.loaded_checksum_paths == (
        "unchanged.md",
        "modified.md",
        "new/moved.md",
        "new.md",
    )
    # Only genuinely new paths (no indexed row) are eligible move destinations,
    # so candidate loading excludes modified paths.
    assert store.loaded_move_checksums == {
        "new/moved.md": "moved-checksum",
        "new.md": "new-file-checksum",
    }
    assert report == ChangeReport(
        new_files=["new.md"],
        modified_files=["modified.md"],
        deleted_files=["deleted.md"],
        moved_files={"old/moved.md": "new/moved.md"},
        unchanged_files=["unchanged.md"],
    )


@pytest.mark.asyncio
async def test_change_detector_adapts_entity_repository_with_explicit_sessions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = FakeEntityRepository()
    session_maker = cast(async_sessionmaker[AsyncSession], object())
    sessions: list[object] = []

    @asynccontextmanager
    async def fake_scoped_session(
        scoped_session_maker: async_sessionmaker[AsyncSession],
    ) -> AsyncIterator[object]:
        assert scoped_session_maker is session_maker
        session = object()
        sessions.append(session)
        yield session

    monkeypatch.setattr(change_detector_module.db, "scoped_session", fake_scoped_session)

    detector = ChangeDetector(cast(EntityRepository, repository), session_maker)
    report = await detector.detect_all_changes(
        {
            "unchanged.md": StorageObject("same-checksum"),
            "modified.md": StorageObject("new-checksum"),
            "new/moved.md": StorageObject("moved-checksum"),
            "new.md": StorageObject("new-file-checksum"),
            "null-checksum.md": StorageObject("now-has-checksum"),
        }
    )

    assert repository.loaded_checksum_paths == (
        "unchanged.md",
        "modified.md",
        "new/moved.md",
        "new.md",
        "null-checksum.md",
    )
    assert repository.loaded_all_paths is True
    # Modified and null-checksum paths keep their existing entity rows, so they
    # are not move destinations and their checksums are not candidate queries.
    assert repository.loaded_move_checksums == (
        "moved-checksum",
        "new-file-checksum",
    )
    assert len(sessions) == 3
    assert report == ChangeReport(
        new_files=["new.md", "null-checksum.md"],
        modified_files=["modified.md"],
        deleted_files=["deleted.md"],
        moved_files={"old/moved.md": "new/moved.md"},
        unchanged_files=["unchanged.md"],
    )


class BatchRecordingEntityRepository:
    """Records each IN() batch so we can prove queries stay under the bind limit."""

    def __init__(self) -> None:
        self.path_batch_sizes: list[int] = []
        self.checksum_batch_sizes: list[int] = []

    async def get_by_file_paths(
        self,
        session: object,
        paths: tuple[str, ...],
    ) -> list[tuple[str, str | None]]:
        self.path_batch_sizes.append(len(paths))
        # Echo each requested path back as an indexed row so the merged result
        # can be checked for completeness across batches.
        return [(path, f"checksum-{path}") for path in paths]

    async def find_by_checksums(
        self,
        session: object,
        checksums: tuple[str, ...],
    ) -> tuple[SimpleNamespace, ...]:
        self.checksum_batch_sizes.append(len(checksums))
        return tuple(
            SimpleNamespace(file_path=f"path-{checksum}", checksum=checksum)
            for checksum in checksums
        )

    async def get_all_file_paths(self, session: object) -> list[str]:  # pragma: no cover
        return []


@pytest.mark.asyncio
async def test_load_indexed_file_checksums_batches_beyond_bind_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """More paths than the bind limit must split into multiple capped IN() queries."""
    repository = BatchRecordingEntityRepository()
    session_maker = cast(async_sessionmaker[AsyncSession], object())

    @asynccontextmanager
    async def fake_scoped_session(
        scoped_session_maker: async_sessionmaker[AsyncSession],
    ) -> AsyncIterator[object]:
        yield object()

    monkeypatch.setattr(change_detector_module.db, "scoped_session", fake_scoped_session)

    detector = ChangeDetector(cast(EntityRepository, repository), session_maker)
    paths = tuple(f"notes/file-{index}.md" for index in range(MAX_QUERY_BIND_PARAMETERS + 5))

    result = await detector.load_indexed_file_checksums(paths)

    # Two batches: one full batch at the cap, then the remainder.
    assert repository.path_batch_sizes == [MAX_QUERY_BIND_PARAMETERS, 5]
    assert all(size <= MAX_QUERY_BIND_PARAMETERS for size in repository.path_batch_sizes)
    # Every path survives the merge across batches.
    assert result == {path: f"checksum-{path}" for path in paths}


@pytest.mark.asyncio
async def test_load_move_candidates_batches_beyond_bind_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """More checksums than the bind limit must split into multiple capped IN() queries."""
    repository = BatchRecordingEntityRepository()
    session_maker = cast(async_sessionmaker[AsyncSession], object())

    @asynccontextmanager
    async def fake_scoped_session(
        scoped_session_maker: async_sessionmaker[AsyncSession],
    ) -> AsyncIterator[object]:
        yield object()

    monkeypatch.setattr(change_detector_module.db, "scoped_session", fake_scoped_session)

    detector = ChangeDetector(cast(EntityRepository, repository), session_maker)
    move_target_checksums = {
        f"new/file-{index}.md": f"checksum-{index:05d}"
        for index in range(MAX_QUERY_BIND_PARAMETERS + 5)
    }

    candidates = await detector.load_move_candidates(move_target_checksums)

    assert repository.checksum_batch_sizes == [MAX_QUERY_BIND_PARAMETERS, 5]
    assert all(size <= MAX_QUERY_BIND_PARAMETERS for size in repository.checksum_batch_sizes)
    # Every distinct checksum yields a merged candidate.
    assert {candidate.checksum for candidate in candidates} == set(move_target_checksums.values())
