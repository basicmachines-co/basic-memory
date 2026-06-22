"""Tests for local project-wide event-index adapters."""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.index import (
    LocalIndexProjectDependencies,
    LocalProjectIndexBatchEnqueuer,
    LocalProjectIndexObservedFileSource,
    LocalProjectIndexRuntimeFactory,
    LocalProjectIndexRuntime,
    local_project_index_file_paths,
    run_local_project_index,
    run_local_project_index_for_project,
)
from basic_memory.indexing import (
    ChangeDetector,
    ChangeReport,
    FileIndexPlan,
    FileIndexTarget,
    IndexFileBatchJobResult,
    IndexFileBatchReadResult,
    IndexFileJobResult,
    IndexFileJobStatus,
    FileIndexOperation,
    FileIndexResult,
    EmbeddingIndexTarget,
    IndexedEntity,
    IndexingBatchResult,
    IndexInputFile,
    ProjectIndexDeleteRun,
    ProjectIndexMoveRun,
    RepositoryRelationResolutionRuntime,
    ResolvedRelationTarget,
    StoreProjectIndexMaintenanceRunner,
    UnresolvedRelation,
)
from basic_memory.models import Entity, Project
from basic_memory.repository.note_content_repository import NoteContentRepository
from basic_memory.runtime import (
    ProjectRuntimeReference,
    RuntimeIndexFileBatchJobRequest,
    RuntimeObservedIndexFile,
    RuntimeProjectIndexJobRequest,
)
from basic_memory.schemas.search import SearchItemType, SearchQuery
from basic_memory.services import FileService


TENANT_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
WORKFLOW_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


def test_local_project_index_file_paths_filter_and_sort(tmp_path: Path) -> None:
    """Local project scans use the same ignore and storage-event path rules."""
    (tmp_path / "notes").mkdir()
    (tmp_path / "ignored").mkdir()
    (tmp_path / ".hidden").mkdir()
    (tmp_path / "notes" / "b.md").write_text("# B\n", encoding="utf-8")
    (tmp_path / "notes" / "a.md").write_text("# A\n", encoding="utf-8")
    (tmp_path / "notes" / "longform.markdown").write_text("# Longform\n", encoding="utf-8")
    (tmp_path / "notes" / "image.png").write_bytes(b"image")
    (tmp_path / "notes" / "todo.txt").write_text("todo\n", encoding="utf-8")
    (tmp_path / "notes" / "scratch.tmp").write_text("tmp\n", encoding="utf-8")
    (tmp_path / "ignored" / "skip.md").write_text("# Skip\n", encoding="utf-8")
    (tmp_path / ".hidden" / "secret.md").write_text("# Secret\n", encoding="utf-8")

    assert local_project_index_file_paths(tmp_path, ignore_patterns={"ignored"}) == (
        "notes/a.md",
        "notes/b.md",
        "notes/image.png",
        "notes/longform.markdown",
        "notes/todo.txt",
    )


async def test_local_project_index_observed_file_source_returns_runtime_targets(
    tmp_path: Path,
) -> None:
    """Local project scans feed the same observed-file values as hosted storage."""
    (tmp_path / "notes").mkdir()
    note_path = tmp_path / "notes" / "a.md"
    note_content = "# A\n"
    note_path.write_text(note_content, encoding="utf-8")
    regular_path = tmp_path / "notes" / "asset.pdf"
    regular_content = b"pdf-ish"
    regular_path.write_bytes(regular_content)

    observed = await LocalProjectIndexObservedFileSource(
        FileService(tmp_path),
        ignore_patterns=set(),
    ).list_observed_index_files()

    assert observed == (
        RuntimeObservedIndexFile(
            path="notes/a.md",
            checksum=sha256(note_content.encode("utf-8")).hexdigest(),
            size=len(note_content.encode("utf-8")),
        ),
        RuntimeObservedIndexFile(
            path="notes/asset.pdf",
            checksum=sha256(regular_content).hexdigest(),
            size=len(regular_content),
        ),
    )


async def test_local_project_index_skips_hidden_markdown_files(
    test_project: Project,
    project_config,
    entity_repository,
    session_maker: async_sessionmaker[AsyncSession],
    config_manager,
    monkeypatch,
) -> None:
    """Hidden markdown files are filtered before project indexing."""
    del config_manager

    concept_dir = project_config.home / "concept"
    concept_dir.mkdir(parents=True, exist_ok=True)
    hidden_path = concept_dir / ".hidden.md"
    hidden_path.write_text(
        "# Hidden\n\nThis file should stay out of the index.\n", encoding="utf-8"
    )

    async def fail_legacy_sync_service(_project):
        raise AssertionError("local hidden-file parity test must not build SyncService")

    monkeypatch.setattr(
        "basic_memory.sync.sync_service.get_sync_service",
        fail_legacy_sync_service,
    )

    result = await run_local_project_index_for_project(
        test_project,
        runtime_factory=LocalProjectIndexRuntimeFactory(batch_size=10),
        force_full=True,
    )

    assert result.total_files == 0
    assert result.enqueued_files == 0
    async with db.scoped_session(session_maker) as session:
        hidden_entity = await entity_repository.get_by_file_path(session, "concept/.hidden.md")

    assert hidden_entity is None


async def test_local_project_index_repairs_null_checksum_entities(
    test_project: Project,
    project_config,
    entity_repository,
    session_maker: async_sessionmaker[AsyncSession],
    config_manager,
    monkeypatch,
) -> None:
    """Incomplete markdown entities are reindexed by the event project index."""
    del config_manager

    entity = Entity(
        permalink=f"{test_project.permalink}/concept/incomplete",
        title="Incomplete",
        note_type="test",
        file_path="concept/incomplete.md",
        checksum=None,
        content_type="text/markdown",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    async with db.scoped_session(session_maker) as session:
        await entity_repository.add(session, entity)

    incomplete_path = project_config.home / "concept" / "incomplete.md"
    incomplete_path.parent.mkdir(parents=True, exist_ok=True)
    incomplete_path.write_text(
        """---
type: knowledge
id: concept/incomplete
created: 2024-01-01
modified: 2024-01-01
---
# Incomplete Entity

## Observations
- Testing cleanup
""",
        encoding="utf-8",
    )

    async def fail_legacy_sync_service(_project):
        raise AssertionError("local null-checksum parity test must not build SyncService")

    monkeypatch.setattr(
        "basic_memory.sync.sync_service.get_sync_service",
        fail_legacy_sync_service,
    )

    result = await run_local_project_index_for_project(
        test_project,
        runtime_factory=LocalProjectIndexRuntimeFactory(batch_size=10),
    )

    assert result.enqueued_files == 1
    assert result.batch_results[0].file_results[0].status == IndexFileJobStatus.processed

    async with db.scoped_session(session_maker) as session:
        repaired = await entity_repository.get_by_file_path(
            session,
            "concept/incomplete.md",
        )

    assert repaired is not None
    assert repaired.checksum is not None


async def test_local_project_index_uses_file_mtime_for_new_markdown_entities(
    test_project: Project,
    project_config,
    entity_repository,
    session_maker: async_sessionmaker[AsyncSession],
    config_manager,
    monkeypatch,
) -> None:
    """New markdown entities keep the observed file modification timestamp."""
    del config_manager

    note_path = project_config.home / "notes" / "timestamped.md"
    note_path.parent.mkdir(parents=True, exist_ok=True)
    note_path.write_text("# Timestamped\n\nInitial content.\n", encoding="utf-8")
    expected_mtime = datetime(2024, 1, 2, 3, 4, 5, tzinfo=timezone.utc).timestamp()
    os.utime(note_path, (expected_mtime, expected_mtime))

    async def fail_legacy_sync_service(_project):
        raise AssertionError("local timestamp parity test must not build SyncService")

    monkeypatch.setattr(
        "basic_memory.sync.sync_service.get_sync_service",
        fail_legacy_sync_service,
    )

    result = await run_local_project_index_for_project(
        test_project,
        runtime_factory=LocalProjectIndexRuntimeFactory(batch_size=10),
    )

    assert result.enqueued_files == 1

    async with db.scoped_session(session_maker) as session:
        entity = await entity_repository.get_by_file_path(session, "notes/timestamped.md")

    assert entity is not None
    assert abs(entity.updated_at.timestamp() - expected_mtime) < 2
    assert entity.mtime is not None
    assert abs(entity.mtime - expected_mtime) < 2


async def test_local_project_index_updates_entity_mtime_on_file_modification(
    test_project: Project,
    project_config,
    entity_repository,
    session_maker: async_sessionmaker[AsyncSession],
    config_manager,
    monkeypatch,
) -> None:
    """Modified markdown entities use the current file modification timestamp."""
    del config_manager

    note_path = project_config.home / "notes" / "timestamp-update.md"
    note_path.parent.mkdir(parents=True, exist_ok=True)
    note_path.write_text("# Timestamp Update\n\nInitial content.\n", encoding="utf-8")
    initial_mtime = datetime(2024, 1, 2, 3, 4, 5, tzinfo=timezone.utc).timestamp()
    os.utime(note_path, (initial_mtime, initial_mtime))

    async def fail_legacy_sync_service(_project):
        raise AssertionError("local timestamp parity test must not build SyncService")

    monkeypatch.setattr(
        "basic_memory.sync.sync_service.get_sync_service",
        fail_legacy_sync_service,
    )

    first = await run_local_project_index_for_project(
        test_project,
        runtime_factory=LocalProjectIndexRuntimeFactory(batch_size=10),
    )
    assert first.enqueued_files == 1

    note_path.write_text(
        "# Timestamp Update\n\nModified content.\n\n## Observations\n- [test] Timestamp moved.\n",
        encoding="utf-8",
    )
    modified_mtime = datetime(2024, 1, 2, 4, 5, 6, tzinfo=timezone.utc).timestamp()
    os.utime(note_path, (modified_mtime, modified_mtime))

    second = await run_local_project_index_for_project(
        test_project,
        runtime_factory=LocalProjectIndexRuntimeFactory(batch_size=10),
    )

    assert second.enqueued_files == 1

    async with db.scoped_session(session_maker) as session:
        entity = await entity_repository.get_by_file_path(session, "notes/timestamp-update.md")

    assert entity is not None
    assert entity.updated_at.timestamp() != initial_mtime
    assert abs(entity.updated_at.timestamp() - modified_mtime) < 2
    assert entity.mtime is not None
    assert abs(entity.mtime - modified_mtime) < 2
    assert len(entity.observations) == 1
    assert entity.observations[0].content == "Timestamp moved."


async def test_local_project_index_indexes_regular_files(
    test_project: Project,
    project_config,
    entity_repository,
    session_maker: async_sessionmaker[AsyncSession],
    config_manager,
    monkeypatch,
) -> None:
    """Project indexing creates regular-file entities without SyncService."""
    del config_manager

    pdf_path = project_config.home / "doc.pdf"
    image_path = project_config.home / "image.png"
    pdf_path.write_bytes(b"pdf-ish")
    image_path.write_bytes(b"png-ish")

    async def fail_legacy_sync_service(_project):
        raise AssertionError("local regular-file parity test must not build SyncService")

    monkeypatch.setattr(
        "basic_memory.sync.sync_service.get_sync_service",
        fail_legacy_sync_service,
    )

    result = await run_local_project_index_for_project(
        test_project,
        runtime_factory=LocalProjectIndexRuntimeFactory(batch_size=10),
    )

    assert result.enqueued_files == 2

    async with db.scoped_session(session_maker) as session:
        pdf_entity = await entity_repository.get_by_file_path(session, "doc.pdf")
        image_entity = await entity_repository.get_by_file_path(session, "image.png")

    assert pdf_entity is not None
    assert pdf_entity.permalink is None
    assert pdf_entity.content_type == "application/pdf"
    assert pdf_entity.checksum == sha256(b"pdf-ish").hexdigest()
    assert image_entity is not None
    assert image_entity.permalink is None
    assert image_entity.content_type == "image/png"
    assert image_entity.checksum == sha256(b"png-ish").hexdigest()


async def test_local_project_index_updates_regular_file_checksum(
    test_project: Project,
    project_config,
    entity_repository,
    session_maker: async_sessionmaker[AsyncSession],
    config_manager,
    monkeypatch,
) -> None:
    """Project indexing updates regular-file checksums when file bytes change."""
    del config_manager

    pdf_path = project_config.home / "doc.pdf"
    pdf_path.write_bytes(b"original")

    async def fail_legacy_sync_service(_project):
        raise AssertionError("local regular-file parity test must not build SyncService")

    monkeypatch.setattr(
        "basic_memory.sync.sync_service.get_sync_service",
        fail_legacy_sync_service,
    )

    first = await run_local_project_index_for_project(
        test_project,
        runtime_factory=LocalProjectIndexRuntimeFactory(batch_size=10),
    )
    assert first.enqueued_files == 1

    async with db.scoped_session(session_maker) as session:
        before = await entity_repository.get_by_file_path(session, "doc.pdf")
        assert before is not None
        before_id = before.id
        before_checksum = before.checksum

    pdf_path.write_bytes(b"changed")

    second = await run_local_project_index_for_project(
        test_project,
        runtime_factory=LocalProjectIndexRuntimeFactory(batch_size=10),
    )

    assert second.enqueued_files == 1

    async with db.scoped_session(session_maker) as session:
        after = await entity_repository.get_by_file_path(session, "doc.pdf")

    assert after is not None
    assert after.id == before_id
    assert after.checksum != before_checksum
    assert after.checksum == sha256(b"changed").hexdigest()
    assert after.size == len(b"changed")


async def test_local_project_index_moves_and_deletes_regular_file_entities(
    test_project: Project,
    project_config,
    entity_repository,
    session_maker: async_sessionmaker[AsyncSession],
    config_manager,
    monkeypatch,
) -> None:
    """Project indexing moves and deletes regular-file entities by storage state."""
    del config_manager

    pdf_path = project_config.home / "doc.pdf"
    pdf_path.write_bytes(b"pdf-ish")

    async def fail_legacy_sync_service(_project):
        raise AssertionError("local regular-file parity test must not build SyncService")

    monkeypatch.setattr(
        "basic_memory.sync.sync_service.get_sync_service",
        fail_legacy_sync_service,
    )

    first = await run_local_project_index_for_project(
        test_project,
        runtime_factory=LocalProjectIndexRuntimeFactory(batch_size=10),
    )
    assert first.enqueued_files == 1

    async with db.scoped_session(session_maker) as session:
        original = await entity_repository.get_by_file_path(session, "doc.pdf")
        assert original is not None
        original_id = original.id

    moved_path = project_config.home / "moved_doc.pdf"
    pdf_path.rename(moved_path)

    second = await run_local_project_index_for_project(
        test_project,
        runtime_factory=LocalProjectIndexRuntimeFactory(batch_size=10),
    )

    assert second.moved_files == 1
    assert second.enqueued_files == 0

    async with db.scoped_session(session_maker) as session:
        old_entity = await entity_repository.get_by_file_path(session, "doc.pdf")
        moved_entity = await entity_repository.get_by_file_path(session, "moved_doc.pdf")

    assert old_entity is None
    assert moved_entity is not None
    assert moved_entity.id == original_id
    assert moved_entity.permalink is None
    assert moved_entity.content_type == "application/pdf"

    moved_path.unlink()

    third = await run_local_project_index_for_project(
        test_project,
        runtime_factory=LocalProjectIndexRuntimeFactory(batch_size=10),
    )

    assert third.deleted_files == 1
    assert third.enqueued_files == 0

    async with db.scoped_session(session_maker) as session:
        deleted = await session.get(Entity, original_id)

    assert deleted is None


async def test_local_project_index_resolves_regular_file_relations(
    test_project: Project,
    project_config,
    entity_repository,
    session_maker: async_sessionmaker[AsyncSession],
    config_manager,
    monkeypatch,
) -> None:
    """Project indexing resolves markdown relations to regular-file entities."""
    del config_manager

    asset_path = project_config.home / "asset.pdf"
    source_path = project_config.home / "note.md"
    asset_path.write_bytes(b"pdf-ish")
    source_path.write_text(
        """---
title: a note
type: note
tags: []
---

- relates_to [[asset.pdf]]
""",
        encoding="utf-8",
    )

    async def fail_legacy_sync_service(_project):
        raise AssertionError("local regular-file relation test must not build SyncService")

    monkeypatch.setattr(
        "basic_memory.sync.sync_service.get_sync_service",
        fail_legacy_sync_service,
    )

    result = await run_local_project_index_for_project(
        test_project,
        runtime_factory=LocalProjectIndexRuntimeFactory(batch_size=10),
        force_full=True,
    )

    assert result.enqueued_files == 2

    expected_permalink = f"{test_project.permalink}/note"
    assert f"permalink: {expected_permalink}" in source_path.read_text(encoding="utf-8")

    async with db.scoped_session(session_maker) as session:
        source_entity = await entity_repository.get_by_file_path(session, "note.md")
        target_entity = await entity_repository.get_by_file_path(session, "asset.pdf")

    assert source_entity is not None
    assert source_entity.permalink == expected_permalink
    assert target_entity is not None
    assert target_entity.permalink is None
    assert target_entity.content_type == "application/pdf"
    assert len(source_entity.outgoing_relations) == 1
    relation = source_entity.outgoing_relations[0]
    assert relation.to_id == target_entity.id


@dataclass(slots=True)
class RecordingObservedFileSource:
    observed_files: tuple[RuntimeObservedIndexFile, ...]

    async def list_observed_index_files(self) -> tuple[RuntimeObservedIndexFile, ...]:
        return self.observed_files


@dataclass(slots=True)
class RecordingChangeDetector:
    report: ChangeReport
    observed_paths: list[tuple[str, ...]] = field(default_factory=list)

    async def detect_all_changes(
        self,
        storage_files: Mapping[str, RuntimeObservedIndexFile],
    ) -> ChangeReport:
        self.observed_paths.append(tuple(storage_files))
        return self.report


@dataclass(slots=True)
class RecordingMaintenanceRunner:
    moved_files: list[dict[str, str]] = field(default_factory=list)
    deleted_paths: list[tuple[str, ...]] = field(default_factory=list)

    async def run_move_batches(
        self,
        *,
        moved_files: Mapping[str, str],
        batch_size: int,
        metadata_reporter: object | None = None,
    ) -> ProjectIndexMoveRun:
        del metadata_reporter
        self.moved_files.append(dict(moved_files))
        return ProjectIndexMoveRun(
            total_moves=len(moved_files),
            total_updated_files=len(moved_files),
            records=(),
        )

    async def run_delete_batches(
        self,
        *,
        deleted_paths: Sequence[str],
        batch_size: int,
        metadata_reporter: object | None = None,
    ) -> ProjectIndexDeleteRun:
        del metadata_reporter
        self.deleted_paths.append(tuple(deleted_paths))
        return ProjectIndexDeleteRun(
            total_deletes=len(deleted_paths),
            total_deleted_entities=len(deleted_paths),
            relation_cleanup_entity_ids=frozenset(),
            records=(),
        )


@dataclass(slots=True)
class RecordingBatchEnqueuer:
    requests: list[RuntimeIndexFileBatchJobRequest] = field(default_factory=list)
    results: list[IndexFileBatchJobResult | None] = field(default_factory=list)

    async def enqueue_index_file_batch(
        self,
        request: RuntimeIndexFileBatchJobRequest,
    ) -> IndexFileBatchJobResult | None:
        self.requests.append(request)
        if self.results:
            return self.results.pop(0)
        return None


@dataclass(slots=True)
class RecordingCompletionRelationRuntime:
    events: list[str]
    resolve_calls: int = 0
    count_calls: int = 0

    async def count_unresolved_relations(self) -> int:
        self.events.append("relations:count")
        self.count_calls += 1
        return 2 if self.count_calls == 1 else 0

    async def resolve_relations(self) -> set[int]:
        self.events.append("relations:resolve")
        self.resolve_calls += 1
        return {10} if self.resolve_calls == 1 else set()


@dataclass(slots=True)
class RecordingMovedEntitySearchRefresher:
    entity_ids: list[tuple[int, ...]] = field(default_factory=list)

    async def refresh_moved_entities(self, entity_ids: Sequence[int]) -> None:
        self.entity_ids.append(tuple(entity_ids))


def project_ref() -> ProjectRuntimeReference:
    return ProjectRuntimeReference(
        project_id=12,
        project_external_id="project-12",
        project_name="Local",
        project_path="local-project",
    )


async def test_run_local_project_index_uses_core_project_fanout() -> None:
    """Local full-project indexing goes through the same coordinator as cloud."""
    observed = (
        RuntimeObservedIndexFile(path="notes/a.md", checksum="a", size=1),
        RuntimeObservedIndexFile(path="notes/b.md", checksum="b", size=2),
        RuntimeObservedIndexFile(path="notes/c.md", checksum="c", size=3),
    )
    observed_source = RecordingObservedFileSource(observed)
    change_detector = RecordingChangeDetector(
        ChangeReport(
            new_files=["notes/a.md", "notes/b.md", "notes/c.md"],
        )
    )
    maintenance_runner = RecordingMaintenanceRunner()
    batch_enqueuer = RecordingBatchEnqueuer()

    result = await run_local_project_index(
        RuntimeProjectIndexJobRequest(
            tenant_id=TENANT_ID,
            workflow_id=WORKFLOW_ID,
            project=project_ref(),
            search=True,
            embeddings=False,
        ),
        runtime=LocalProjectIndexRuntime(
            observed_file_source=observed_source,
            change_detector=change_detector,
            maintenance_runner=maintenance_runner,
            moved_entity_search_refresher=RecordingMovedEntitySearchRefresher(),
            batch_enqueuer=batch_enqueuer,
            batch_size=2,
        ),
    )

    assert result.total_files == 3
    assert result.enqueued_batches == 2
    assert result.enqueued_files == 3
    assert change_detector.observed_paths == [("notes/a.md", "notes/b.md", "notes/c.md")]
    assert maintenance_runner.moved_files == [{}]
    assert maintenance_runner.deleted_paths == [()]
    assert [request.target_paths() for request in batch_enqueuer.requests] == [
        ("notes/a.md", "notes/b.md"),
        ("notes/c.md",),
    ]
    assert batch_enqueuer.requests[0].observed_files == observed[:2]
    assert batch_enqueuer.requests[0].index_embeddings is False


async def test_run_local_project_index_resolves_relations_after_inline_fanout() -> None:
    """Local project indexing runs completion relation resolution after child batches."""
    events: list[str] = []
    observed = (RuntimeObservedIndexFile(path="notes/a.md", checksum="a", size=1),)
    observed_source = RecordingObservedFileSource(observed)
    change_detector = RecordingChangeDetector(ChangeReport(new_files=["notes/a.md"]))
    maintenance_runner = RecordingMaintenanceRunner()

    class EventBatchEnqueuer(RecordingBatchEnqueuer):
        async def enqueue_index_file_batch(self, request: RuntimeIndexFileBatchJobRequest) -> None:
            events.append("batch")
            await super().enqueue_index_file_batch(request)

    relation_runtime = RecordingCompletionRelationRuntime(events)
    batch_enqueuer = EventBatchEnqueuer()

    await run_local_project_index(
        RuntimeProjectIndexJobRequest(
            tenant_id=TENANT_ID,
            workflow_id=WORKFLOW_ID,
            project=project_ref(),
            search=True,
            embeddings=False,
        ),
        runtime=LocalProjectIndexRuntime(
            observed_file_source=observed_source,
            change_detector=change_detector,
            maintenance_runner=maintenance_runner,
            moved_entity_search_refresher=RecordingMovedEntitySearchRefresher(),
            batch_enqueuer=batch_enqueuer,
            batch_size=10,
            completion_relation_runtime=relation_runtime,
        ),
    )

    assert events == [
        "batch",
        "relations:count",
        "relations:resolve",
        "relations:resolve",
        "relations:count",
    ]
    assert relation_runtime.resolve_calls == 2


async def test_run_local_project_index_preserves_inline_batch_results() -> None:
    """Local project-index exposes child batch outcomes for cloud/local parity checks."""
    observed = (RuntimeObservedIndexFile(path="notes/a.md", checksum="a", size=1),)
    batch_result = IndexFileBatchJobResult(
        total_files=1,
        processed_files=1,
        missing_files=0,
        failed_files=0,
        file_results=(
            IndexFileJobResult(
                status=IndexFileJobStatus.processed,
                reason="file indexed: notes/a.md",
                entity_id=42,
                entity_checksum="checksum-a",
            ),
        ),
        vector_targets=(),
    )
    batch_enqueuer = RecordingBatchEnqueuer(results=[batch_result])

    result = await run_local_project_index(
        RuntimeProjectIndexJobRequest(
            tenant_id=TENANT_ID,
            workflow_id=WORKFLOW_ID,
            project=project_ref(),
            search=True,
            embeddings=False,
        ),
        runtime=LocalProjectIndexRuntime(
            observed_file_source=RecordingObservedFileSource(observed),
            change_detector=RecordingChangeDetector(ChangeReport(new_files=["notes/a.md"])),
            maintenance_runner=RecordingMaintenanceRunner(),
            moved_entity_search_refresher=RecordingMovedEntitySearchRefresher(),
            batch_enqueuer=batch_enqueuer,
            batch_size=10,
        ),
    )

    assert result.batch_results == (batch_result,)


def test_local_project_index_runtime_uses_optional_workflow_hooks() -> None:
    """Local inline project indexing does not install hidden workflow adapters."""
    runtime = LocalProjectIndexRuntime(
        observed_file_source=RecordingObservedFileSource(()),
        change_detector=RecordingChangeDetector(ChangeReport()),
        maintenance_runner=RecordingMaintenanceRunner(),
        moved_entity_search_refresher=RecordingMovedEntitySearchRefresher(),
        batch_enqueuer=RecordingBatchEnqueuer(),
    )

    assert runtime.workflow_starter is None
    assert runtime.fanout_failure_recorder is None


@dataclass(slots=True)
class RecordingBatchChecker:
    seen_targets: list[tuple[FileIndexTarget, ...]] = field(default_factory=list)

    async def detect(self, targets: Sequence[FileIndexTarget]) -> FileIndexPlan:
        self.seen_targets.append(tuple(targets))
        return FileIndexPlan(
            paths_to_read=tuple(target.path for target in targets),
            decisions=(),
        )


@dataclass(slots=True)
class RecordingBatchReader:
    files: Mapping[str, IndexInputFile]
    seen_reads: list[tuple[tuple[str, ...], int]] = field(default_factory=list)

    async def read_current_files(
        self,
        file_paths: Sequence[str],
        *,
        max_concurrent: int,
    ) -> IndexFileBatchReadResult[IndexInputFile]:
        self.seen_reads.append((tuple(file_paths), max_concurrent))
        return IndexFileBatchReadResult(
            files={file_path: self.files[file_path] for file_path in file_paths},
            terminal_results={},
        )


@dataclass(slots=True)
class RecordingBatchIndexer:
    seen_batches: list[tuple[tuple[str, ...], int, int | None, int | None]] = field(
        default_factory=list
    )

    async def index_files(
        self,
        files: Mapping[str, IndexInputFile],
        *,
        max_concurrent: int,
        parse_max_concurrent: int | None = None,
        metadata_update_max_concurrent: int | None = None,
        bound_logger: object | None = None,
    ) -> IndexingBatchResult:
        del bound_logger
        self.seen_batches.append(
            (
                tuple(files),
                max_concurrent,
                parse_max_concurrent,
                metadata_update_max_concurrent,
            )
        )
        return IndexingBatchResult(
            indexed=[
                IndexedEntity(
                    path=file_path,
                    entity_id=index + 1,
                    permalink=None,
                    checksum=file_info.checksum or f"checksum-{index}",
                    content_type=file_info.content_type,
                )
                for index, (file_path, file_info) in enumerate(files.items())
            ]
        )


class RuntimePathClassifier:
    def is_markdown(self, path: str) -> bool:
        return path.endswith((".md", ".markdown"))


async def test_local_project_index_batch_enqueuer_runs_shared_batch_contract() -> None:
    """Local project fanout uses the same batch runner contract as cloud."""
    files = {
        "notes/a.md": IndexInputFile(
            path="notes/a.md",
            size=11,
            checksum="checksum-a",
            content_type="text/markdown",
            content=b"# A\n",
        ),
        "assets/file.pdf": IndexInputFile(
            path="assets/file.pdf",
            size=14,
            checksum="checksum-pdf",
            content_type="application/pdf",
            content=b"pdf-ish",
        ),
    }
    checker = RecordingBatchChecker()
    reader = RecordingBatchReader(files=files)
    indexer = RecordingBatchIndexer()
    enqueuer = LocalProjectIndexBatchEnqueuer(
        checker=checker,
        reader=reader,
        indexer=indexer,
        content_classifier=RuntimePathClassifier(),
        read_max_concurrent=3,
        index_max_concurrent=5,
    )

    result = await enqueuer.enqueue_index_file_batch(
        RuntimeIndexFileBatchJobRequest(
            tenant_id=TENANT_ID,
            project=project_ref(),
            workflow_id=WORKFLOW_ID,
            batch_index=1,
            batch_count=2,
            observed_files=(
                RuntimeObservedIndexFile(path="notes/a.md", checksum="etag-a", size=11),
                RuntimeObservedIndexFile(path="assets/file.pdf", checksum="etag-d", size=14),
            ),
            index_embeddings=True,
        )
    )

    assert checker.seen_targets == [
        (
            FileIndexTarget(path="notes/a.md", observed_checksum="etag-a", observed_size=11),
            FileIndexTarget(path="assets/file.pdf", observed_checksum="etag-d", observed_size=14),
        )
    ]
    assert reader.seen_reads == [(("notes/a.md", "assets/file.pdf"), 3)]
    assert indexer.seen_batches == [(("notes/a.md", "assets/file.pdf"), 5, 5, 5)]
    assert result == IndexFileBatchJobResult(
        total_files=2,
        processed_files=2,
        missing_files=0,
        failed_files=0,
        file_results=(
            IndexFileJobResult(
                status=IndexFileJobStatus.processed,
                reason="file indexed: notes/a.md",
                entity_id=1,
                entity_checksum="checksum-a",
            ),
            IndexFileJobResult(
                status=IndexFileJobStatus.processed,
                reason="file indexed: assets/file.pdf",
                entity_id=2,
                entity_checksum="checksum-pdf",
            ),
        ),
        vector_targets=(EmbeddingIndexTarget(entity_id=1, entity_checksum="checksum-a"),),
    )


async def test_local_project_index_force_full_reindexes_unchanged_files(
    test_project: Project,
    project_config,
    entity_repository,
    session_maker: async_sessionmaker[AsyncSession],
    config_manager,
    monkeypatch,
) -> None:
    """Force-full local project indexing refreshes files even when checksums match."""
    del config_manager

    note_path = project_config.home / "notes" / "force-full.md"
    note_path.parent.mkdir(parents=True, exist_ok=True)
    note_path.write_text("# Force Full\n\nThis should be reprocessed.\n", encoding="utf-8")

    async def fail_legacy_sync_service(_project):
        raise AssertionError("local project index parity test must not build SyncService")

    monkeypatch.setattr(
        "basic_memory.sync.sync_service.get_sync_service",
        fail_legacy_sync_service,
    )

    first = await run_local_project_index_for_project(
        test_project,
        runtime_factory=LocalProjectIndexRuntimeFactory(batch_size=10),
        force_full=True,
    )
    assert first.batch_results[0].file_results[0].status == IndexFileJobStatus.processed

    second = await run_local_project_index_for_project(
        test_project,
        runtime_factory=LocalProjectIndexRuntimeFactory(batch_size=10),
        force_full=True,
    )

    assert second.enqueued_files == 1
    assert second.batch_results[0].processed_files == 1
    assert second.batch_results[0].file_results[0].status == IndexFileJobStatus.processed

    async with db.scoped_session(session_maker) as session:
        entity = await entity_repository.get_by_file_path(session, "notes/force-full.md")
        assert entity is not None
        note_content = await NoteContentRepository(test_project.id).get_by_file_path(
            session,
            "notes/force-full.md",
        )
    assert note_content is not None
    assert "This should be reprocessed." in note_content.markdown_content


async def test_local_project_index_move_updates_note_content_identity(
    test_project: Project,
    project_config,
    entity_repository,
    session_maker: async_sessionmaker[AsyncSession],
    search_service,
    config_manager,
    monkeypatch,
) -> None:
    """Move maintenance keeps note_content mirrored to the current entity path."""
    del config_manager

    original_path = project_config.home / "notes" / "move-me.md"
    moved_path = project_config.home / "archive" / "move-me.md"
    original_path.parent.mkdir(parents=True, exist_ok=True)
    moved_path.parent.mkdir(parents=True, exist_ok=True)
    original_path.write_text(
        "# Move Me\n\nThis note should keep content identity.\n", encoding="utf-8"
    )

    async def fail_legacy_sync_service(_project):
        raise AssertionError("local project index parity test must not build SyncService")

    monkeypatch.setattr(
        "basic_memory.sync.sync_service.get_sync_service",
        fail_legacy_sync_service,
    )

    first = await run_local_project_index_for_project(
        test_project,
        runtime_factory=LocalProjectIndexRuntimeFactory(batch_size=10),
        force_full=True,
    )
    assert first.batch_results[0].file_results[0].status == IndexFileJobStatus.processed

    async with db.scoped_session(session_maker) as session:
        original_entity = await entity_repository.get_by_file_path(session, "notes/move-me.md")
        assert original_entity is not None
        original_note_content = await NoteContentRepository(test_project.id).get_by_entity_id(
            session,
            original_entity.id,
        )
        assert original_note_content is not None
        original_entity_id = original_entity.id

    original_path.rename(moved_path)

    second = await run_local_project_index_for_project(
        test_project,
        runtime_factory=LocalProjectIndexRuntimeFactory(batch_size=10),
    )

    assert second.moved_files == 1
    assert second.enqueued_files == 0

    async with db.scoped_session(session_maker) as session:
        assert await entity_repository.get_by_file_path(session, "notes/move-me.md") is None
        moved_entity = await entity_repository.get_by_file_path(session, "archive/move-me.md")
        assert moved_entity is not None
        moved_note_content = await NoteContentRepository(test_project.id).get_by_entity_id(
            session,
            moved_entity.id,
        )

    assert moved_entity.id == original_entity_id
    assert moved_note_content is not None
    assert moved_note_content.file_path == "archive/move-me.md"

    results = await search_service.search(SearchQuery(text="content identity"))
    assert len(results) == 1
    assert results[0].file_path == "archive/move-me.md"


async def test_local_project_index_move_updates_permalink_when_configured(
    test_project: Project,
    project_config,
    entity_repository,
    session_maker: async_sessionmaker[AsyncSession],
    search_service,
    app_config,
    config_manager,
    monkeypatch,
) -> None:
    """Move maintenance mirrors sync permalink policy without using SyncService."""
    app_config.update_permalinks_on_move = True
    config_manager.save_config(app_config)

    original_path = project_config.home / "notes" / "rename-me.md"
    moved_path = project_config.home / "archive" / "renamed-note.md"
    original_path.parent.mkdir(parents=True, exist_ok=True)
    moved_path.parent.mkdir(parents=True, exist_ok=True)
    original_path.write_text("# Rename Me\n\nThis note moves with a permalink.\n", encoding="utf-8")

    async def fail_legacy_sync_service(_project):
        raise AssertionError("local project index parity test must not build SyncService")

    monkeypatch.setattr(
        "basic_memory.sync.sync_service.get_sync_service",
        fail_legacy_sync_service,
    )

    first = await run_local_project_index_for_project(
        test_project,
        runtime_factory=LocalProjectIndexRuntimeFactory(batch_size=10),
        force_full=True,
    )
    assert first.batch_results[0].file_results[0].status == IndexFileJobStatus.processed

    async with db.scoped_session(session_maker) as session:
        original_entity = await entity_repository.get_by_file_path(session, "notes/rename-me.md")
        assert original_entity is not None
        original_permalink = original_entity.permalink

    assert original_permalink == f"{test_project.permalink}/notes/rename-me"
    assert f"permalink: {original_permalink}" in original_path.read_text(encoding="utf-8")

    original_path.rename(moved_path)

    second = await run_local_project_index_for_project(
        test_project,
        runtime_factory=LocalProjectIndexRuntimeFactory(batch_size=10),
    )

    expected_permalink = f"{test_project.permalink}/archive/renamed-note"
    assert second.moved_files == 1
    assert second.enqueued_files == 0
    assert f"permalink: {expected_permalink}" in moved_path.read_text(encoding="utf-8")

    async with db.scoped_session(session_maker) as session:
        moved_entity = await entity_repository.get_by_file_path(session, "archive/renamed-note.md")
        assert moved_entity is not None
        moved_note_content = await NoteContentRepository(test_project.id).get_by_entity_id(
            session,
            moved_entity.id,
        )

    assert moved_entity.permalink == expected_permalink
    assert moved_note_content is not None
    assert f"permalink: {expected_permalink}" in moved_note_content.markdown_content

    results = await search_service.search(SearchQuery(permalink=expected_permalink))
    assert len(results) == 1
    assert results[0].permalink == expected_permalink
    assert results[0].file_path == "archive/renamed-note.md"


async def test_local_project_index_move_repairs_observation_search_permalinks(
    test_project: Project,
    project_config,
    session_maker: async_sessionmaker[AsyncSession],
    search_service,
    app_config,
    config_manager,
    monkeypatch,
) -> None:
    """Moved markdown notes refresh derived observation search rows."""
    app_config.update_permalinks_on_move = True
    config_manager.save_config(app_config)

    original_path = project_config.home / "notes" / "observed.md"
    moved_path = project_config.home / "archive" / "observed.md"
    original_path.parent.mkdir(parents=True, exist_ok=True)
    moved_path.parent.mkdir(parents=True, exist_ok=True)
    original_path.write_text(
        "# Observed\n\n- [fact] Search repair follows the moved note.\n",
        encoding="utf-8",
    )

    async def fail_legacy_sync_service(_project):
        raise AssertionError("local project index parity test must not build SyncService")

    monkeypatch.setattr(
        "basic_memory.sync.sync_service.get_sync_service",
        fail_legacy_sync_service,
    )

    first = await run_local_project_index_for_project(
        test_project,
        runtime_factory=LocalProjectIndexRuntimeFactory(batch_size=10),
        force_full=True,
    )
    assert first.batch_results[0].file_results[0].status == IndexFileJobStatus.processed

    original_path.rename(moved_path)

    second = await run_local_project_index_for_project(
        test_project,
        runtime_factory=LocalProjectIndexRuntimeFactory(batch_size=10),
    )

    expected_permalink_prefix = f"{test_project.permalink}/archive/observed/observations/"
    assert second.moved_files == 1
    assert second.enqueued_files == 0

    async with db.scoped_session(session_maker) as session:
        search_rows = await search_service.repository.search(
            permalink_match=f"{expected_permalink_prefix}*",
            search_item_types=[SearchItemType.OBSERVATION],
            session=session,
        )

    assert len(search_rows) == 1
    assert search_rows[0].permalink is not None
    assert search_rows[0].permalink.startswith(expected_permalink_prefix)
    assert search_rows[0].file_path == "archive/observed.md"


async def test_local_project_index_resolves_order_dependent_relations_after_batches(
    test_project: Project,
    project_config,
    entity_repository,
    session_maker: async_sessionmaker[AsyncSession],
    search_service,
    config_manager,
    monkeypatch,
) -> None:
    """Project indexing resolves relations after all batches finish."""
    del config_manager

    concept_dir = project_config.home / "concept"
    concept_dir.mkdir(parents=True, exist_ok=True)
    (concept_dir / "entity_a.md").write_text(
        """---
type: knowledge
permalink: concept/entity-a
---
# Entity A

## Relations
- depends_on [[concept/entity-b]]
- depends_on [[concept/entity-c]]
""",
        encoding="utf-8",
    )
    (concept_dir / "entity_b.md").write_text(
        """---
type: knowledge
permalink: concept/entity-b
---
# Entity B

## Relations
- depends_on [[concept/entity-c]]
""",
        encoding="utf-8",
    )
    (concept_dir / "entity_c.md").write_text(
        """---
type: knowledge
permalink: concept/entity-c
---
# Entity C

## Relations
- depends_on [[concept/entity-a]]
""",
        encoding="utf-8",
    )

    async def fail_legacy_sync_service(_project):
        raise AssertionError("local relation parity test must not build SyncService")

    monkeypatch.setattr(
        "basic_memory.sync.sync_service.get_sync_service",
        fail_legacy_sync_service,
    )

    result = await run_local_project_index_for_project(
        test_project,
        runtime_factory=LocalProjectIndexRuntimeFactory(batch_size=1),
        force_full=True,
    )

    assert result.enqueued_files == 3
    assert result.enqueued_batches == 3

    async with db.scoped_session(session_maker) as session:
        entity_a = await entity_repository.get_by_file_path(session, "concept/entity_a.md")
        entity_b = await entity_repository.get_by_file_path(session, "concept/entity_b.md")
        entity_c = await entity_repository.get_by_file_path(session, "concept/entity_c.md")
        relation_search_rows = await search_service.repository.search(
            search_item_types=[SearchItemType.RELATION],
            session=session,
        )

    assert entity_a is not None
    assert entity_b is not None
    assert entity_c is not None

    a_targets = {relation.to_id for relation in entity_a.outgoing_relations}
    b_targets = {relation.to_id for relation in entity_b.outgoing_relations}
    c_targets = {relation.to_id for relation in entity_c.outgoing_relations}

    assert a_targets == {entity_b.id, entity_c.id}
    assert b_targets == {entity_c.id}
    assert c_targets == {entity_a.id}

    a_incoming_sources = {relation.from_id for relation in entity_a.incoming_relations}
    b_incoming_sources = {relation.from_id for relation in entity_b.incoming_relations}
    c_incoming_sources = {relation.from_id for relation in entity_c.incoming_relations}

    assert a_incoming_sources == {entity_c.id}
    assert b_incoming_sources == {entity_a.id}
    assert c_incoming_sources == {entity_a.id, entity_b.id}
    assert {row.to_id for row in relation_search_rows} == {entity_a.id, entity_b.id, entity_c.id}


async def test_local_project_index_deduplicates_relations_by_type(
    test_project: Project,
    project_config,
    entity_repository,
    session_maker: async_sessionmaker[AsyncSession],
    search_service,
    config_manager,
    monkeypatch,
) -> None:
    """Duplicate relation declarations collapse without losing distinct relation types."""
    del config_manager

    concept_dir = project_config.home / "concept"
    concept_dir.mkdir(parents=True, exist_ok=True)
    (concept_dir / "target.md").write_text(
        """---
type: knowledge
permalink: concept/target
---
# Target Entity
""",
        encoding="utf-8",
    )
    (concept_dir / "duplicate_relations.md").write_text(
        """---
type: knowledge
permalink: concept/duplicate-relations
---
# Test Duplicates

## Relations
- depends_on [[concept/target]]
- depends_on [[concept/target]]
- uses [[concept/target]]
- uses [[concept/target]]
""",
        encoding="utf-8",
    )

    async def fail_legacy_sync_service(_project):
        raise AssertionError("local duplicate relation parity test must not build SyncService")

    monkeypatch.setattr(
        "basic_memory.sync.sync_service.get_sync_service",
        fail_legacy_sync_service,
    )

    result = await run_local_project_index_for_project(
        test_project,
        runtime_factory=LocalProjectIndexRuntimeFactory(batch_size=1),
        force_full=True,
    )

    assert result.enqueued_files == 2
    assert result.enqueued_batches == 2

    async with db.scoped_session(session_maker) as session:
        source = await entity_repository.get_by_file_path(session, "concept/duplicate_relations.md")
        target = await entity_repository.get_by_file_path(session, "concept/target.md")
        relation_search_rows = await search_service.repository.search(
            search_item_types=[SearchItemType.RELATION],
            session=session,
        )

    assert source is not None
    assert target is not None
    relation_counts: dict[str, int] = {}
    for relation in source.outgoing_relations:
        assert relation.to_id == target.id
        relation_counts[relation.relation_type] = relation_counts.get(relation.relation_type, 0) + 1

    assert relation_counts == {"depends_on": 1, "uses": 1}
    assert len(relation_search_rows) == 2
    assert {row.to_id for row in relation_search_rows} == {target.id}


async def test_local_project_index_directory_delete_removes_notes_and_repairs_survivors(
    test_project: Project,
    project_config,
    entity_repository,
    session_maker: async_sessionmaker[AsyncSession],
    search_service,
    config_manager,
    monkeypatch,
) -> None:
    """Deleted local directories should use core delete orchestration without SyncService."""
    del config_manager

    archive_dir = project_config.home / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    target_path = archive_dir / "deleted-target.md"
    other_path = archive_dir / "other.md"
    source_path = project_config.home / "keeper.md"
    target_path.write_text(
        """---
title: Deleted Target
type: note
---
# Deleted Target
""",
        encoding="utf-8",
    )
    other_path.write_text("# Other\n\nThis note disappears with the directory.\n", encoding="utf-8")
    source_path.write_text(
        """---
title: Keeper
type: note
---
# Keeper

- relates_to [[Deleted Target]]
""",
        encoding="utf-8",
    )

    async def fail_legacy_sync_service(_project):
        raise AssertionError("local directory delete parity test must not build SyncService")

    monkeypatch.setattr(
        "basic_memory.sync.sync_service.get_sync_service",
        fail_legacy_sync_service,
    )

    first = await run_local_project_index_for_project(
        test_project,
        runtime_factory=LocalProjectIndexRuntimeFactory(batch_size=10),
        force_full=True,
    )
    assert first.enqueued_files == 3

    async with db.scoped_session(session_maker) as session:
        source_before = await entity_repository.get_by_file_path(session, "keeper.md")
        target_before = await entity_repository.get_by_file_path(
            session, "archive/deleted-target.md"
        )
        other_before = await entity_repository.get_by_file_path(session, "archive/other.md")
        target_note_content_before = await NoteContentRepository(test_project.id).get_by_file_path(
            session,
            "archive/deleted-target.md",
        )
        assert source_before is not None
        assert target_before is not None
        assert other_before is not None
        assert target_note_content_before is not None
        assert len(source_before.outgoing_relations) == 1
        relation_before = source_before.outgoing_relations[0]
        assert relation_before.to_id == target_before.id
        relation_permalink = relation_before.permalink
        assert relation_permalink is not None
        source_entity_id = source_before.id
        target_entity_id = target_before.id
        other_entity_id = other_before.id

    target_path.unlink()
    other_path.unlink()
    archive_dir.rmdir()

    second = await run_local_project_index_for_project(
        test_project,
        runtime_factory=LocalProjectIndexRuntimeFactory(batch_size=10),
    )

    assert second.deleted_files == 2
    assert second.enqueued_files == 0
    assert source_entity_id in second.relation_cleanup_entity_ids

    async with db.scoped_session(session_maker) as session:
        source_after = await entity_repository.get_by_file_path(session, "keeper.md")
        deleted_target = await session.get(Entity, target_entity_id)
        deleted_other = await session.get(Entity, other_entity_id)
        target_note_content_after = await NoteContentRepository(test_project.id).get_by_file_path(
            session,
            "archive/deleted-target.md",
        )
        stale_relation_rows = await search_service.repository.search(
            permalink=relation_permalink,
            search_item_types=[SearchItemType.RELATION],
            session=session,
        )

    assert source_after is not None
    assert source_after.id == source_entity_id
    assert source_after.outgoing_relations == []
    assert deleted_target is None
    assert deleted_other is None
    assert target_note_content_after is None
    assert stale_relation_rows == []


async def test_local_project_index_resolves_duplicate_permalink_update(
    test_project: Project,
    project_config,
    entity_repository,
    session_maker: async_sessionmaker[AsyncSession],
    config_manager,
    monkeypatch,
) -> None:
    """Updated notes with duplicate permalinks should repair to their current path."""
    del config_manager

    one_path = project_config.home / "one.md"
    two_path = project_config.home / "two.md"
    one_path.write_text(
        """---
permalink: one
---

original one content
""",
        encoding="utf-8",
    )
    two_path.write_text(
        """---
permalink: two
---

original two content
""",
        encoding="utf-8",
    )

    async def fail_legacy_sync_service(_project):
        raise AssertionError("local permalink conflict parity test must not build SyncService")

    monkeypatch.setattr(
        "basic_memory.sync.sync_service.get_sync_service",
        fail_legacy_sync_service,
    )

    first = await run_local_project_index_for_project(
        test_project,
        runtime_factory=LocalProjectIndexRuntimeFactory(batch_size=10),
        force_full=True,
    )
    assert first.enqueued_files == 2
    assert "permalink: one" in one_path.read_text(encoding="utf-8")
    assert "permalink: two" in two_path.read_text(encoding="utf-8")

    two_path.write_text(
        """---
title: two.md
type: note
permalink: one
tags: []
---

updated two content
""",
        encoding="utf-8",
    )

    second = await run_local_project_index_for_project(
        test_project,
        runtime_factory=LocalProjectIndexRuntimeFactory(batch_size=10),
    )

    assert second.enqueued_files == 1
    repaired_content = two_path.read_text(encoding="utf-8")
    assert "permalink: two" in repaired_content
    assert "permalink: one-1" not in repaired_content

    async with db.scoped_session(session_maker) as session:
        one_entity = await entity_repository.get_by_file_path(session, "one.md")
        two_entity = await entity_repository.get_by_file_path(session, "two.md")
        two_note_content = await NoteContentRepository(test_project.id).get_by_file_path(
            session,
            "two.md",
        )

    assert one_entity is not None
    assert one_entity.permalink == "one"
    assert two_entity is not None
    assert two_entity.permalink == "two"
    assert two_note_content is not None
    assert "updated two content" in two_note_content.markdown_content
    assert "permalink: two" in two_note_content.markdown_content


async def test_local_project_index_does_not_add_frontmatter_when_disabled(
    test_project: Project,
    project_config,
    app_config,
    config_manager,
    monkeypatch,
) -> None:
    """Plain markdown files stay plain when missing-frontmatter rewrites are disabled."""
    app_config.ensure_frontmatter_on_sync = False
    config_manager.save_config(app_config)

    plain_path = project_config.home / "plain.md"
    plain_path.write_text("# Plain\n\nNo frontmatter should be created.\n", encoding="utf-8")

    async def fail_legacy_sync_service(_project):
        raise AssertionError("local frontmatter policy parity test must not build SyncService")

    monkeypatch.setattr(
        "basic_memory.sync.sync_service.get_sync_service",
        fail_legacy_sync_service,
    )

    result = await run_local_project_index_for_project(
        test_project,
        runtime_factory=LocalProjectIndexRuntimeFactory(batch_size=10),
        force_full=True,
    )

    assert result.enqueued_files == 1
    indexed_content = plain_path.read_text(encoding="utf-8")
    assert "permalink:" not in indexed_content
    assert "type:" not in indexed_content


async def test_local_project_index_writes_frontmatter_when_enabled_even_if_permalinks_disabled(
    test_project: Project,
    project_config,
    entity_repository,
    session_maker: async_sessionmaker[AsyncSession],
    app_config,
    config_manager,
    monkeypatch,
) -> None:
    """Missing-frontmatter project indexing writes identity metadata when configured."""
    app_config.ensure_frontmatter_on_sync = True
    app_config.disable_permalinks = True
    config_manager.save_config(app_config)

    note_path = project_config.home / "override.md"
    note_path.write_text("# Override\n", encoding="utf-8")

    async def fail_legacy_sync_service(_project):
        raise AssertionError("local frontmatter policy parity test must not build SyncService")

    monkeypatch.setattr(
        "basic_memory.sync.sync_service.get_sync_service",
        fail_legacy_sync_service,
    )

    result = await run_local_project_index_for_project(
        test_project,
        runtime_factory=LocalProjectIndexRuntimeFactory(batch_size=10),
        force_full=True,
    )

    expected_permalink = f"{test_project.permalink}/override"
    assert result.enqueued_files == 1
    indexed_content = note_path.read_text(encoding="utf-8")
    assert "title: override" in indexed_content
    assert "type: note" in indexed_content
    assert f"permalink: {expected_permalink}" in indexed_content

    async with db.scoped_session(session_maker) as session:
        entity = await entity_repository.get_by_file_path(session, "override.md")

    assert entity is not None
    assert entity.permalink == expected_permalink


@dataclass(slots=True)
class RecordingMarkdownFileIndexer:
    indexed_paths: list[str] = field(default_factory=list)

    async def index_file(self, file_path: str, *, source: str) -> FileIndexResult:
        self.indexed_paths.append(file_path)
        return FileIndexResult.from_fields(
            file_path=file_path,
            entity_id=99,
            external_id="note-99",
            title="Note 99",
            permalink="notes/note-99",
            checksum="indexed-checksum",
            operation=FileIndexOperation.updated,
        )


class RuntimeFactoryEntityRepository:
    project_id: int | None = 12

    async def find_by_id(self, session: AsyncSession, entity_id: int) -> Entity | None:
        return None

    async def get_all_file_paths(self, session: AsyncSession) -> Sequence[str]:
        return ()

    async def get_by_file_path(
        self,
        session: AsyncSession,
        file_path: Path | str,
        *,
        load_relations: bool = True,
    ) -> Entity | None:
        return None

    async def get_by_file_paths(
        self,
        session: AsyncSession,
        file_paths: Sequence[Path | str],
    ) -> Sequence[Row[Any]]:
        return ()

    async def find_by_ids(
        self,
        session: AsyncSession,
        ids: list[Any],
    ) -> Sequence[Entity]:
        return ()

    async def find_by_checksums(
        self,
        session: AsyncSession,
        checksums: Sequence[str],
    ) -> Sequence[Entity]:
        return ()

    async def update(
        self,
        session: AsyncSession,
        entity_id: Any,
        entity_data: dict[str, Any] | Entity,
    ) -> Entity | None:
        return None

    async def delete_by_fields(
        self,
        session: AsyncSession,
        **filters: object,
    ) -> bool:
        return False


class RuntimeFactorySearchIndex:
    async def handle_delete(self, entity: Entity) -> None:
        return None

    async def index_entity(self, entity: Entity) -> None:
        return None


class RuntimeFactoryRelationRepository:
    async def find_unresolved_relations(
        self, session: AsyncSession
    ) -> Sequence[UnresolvedRelation]:
        return ()

    async def find_unresolved_relations_for_entity(
        self,
        session: AsyncSession,
        entity_id: int,
    ) -> Sequence[UnresolvedRelation]:
        return ()

    async def update(
        self,
        session: AsyncSession,
        entity_id: int,
        entity_data: dict[str, object],
    ) -> object | None:
        return None

    async def delete(self, session: AsyncSession, entity_id: int) -> bool:
        return False


class RuntimeFactoryLinkResolver:
    async def resolve_link(
        self,
        link_text: str,
        *,
        strict: bool,
        session: AsyncSession,
    ) -> ResolvedRelationTarget | None:
        return None


class RuntimeFactoryEntityService:
    app_config = None

    async def resolve_permalink(self, *args: object, **kwargs: object) -> str:
        return "local"


@dataclass(slots=True)
class RecordingLocalIndexProjectDependencyProvider:
    dependencies: LocalIndexProjectDependencies
    seen_projects: list[Project] = field(default_factory=list)

    async def dependencies_for_project(self, project: Project) -> LocalIndexProjectDependencies:
        self.seen_projects.append(project)
        return self.dependencies


async def test_local_project_index_runtime_factory_composes_inline_runtime(
    tmp_path: Path,
) -> None:
    """Local project indexing can be wired from explicit index dependencies."""
    dependencies = LocalIndexProjectDependencies(
        file_service=FileService(tmp_path),
        file_indexer=RecordingMarkdownFileIndexer(),
        file_batch_indexer=RecordingBatchIndexer(),
        session_maker=async_sessionmaker(),
        project_id=12,
        entity_repository=RuntimeFactoryEntityRepository(),
        relation_repository=RuntimeFactoryRelationRepository(),
        link_resolver=RuntimeFactoryLinkResolver(),
        search_service=RuntimeFactorySearchIndex(),
        entity_service=RuntimeFactoryEntityService(),
    )
    dependency_provider = RecordingLocalIndexProjectDependencyProvider(dependencies)

    factory = LocalProjectIndexRuntimeFactory(
        dependency_provider=dependency_provider,
        batch_size=3,
    )
    project = Project(
        id=12,
        external_id="project-12",
        name="Local",
        permalink="local",
        path="local-project",
    )

    runtime = await factory.runtime_for_project(project)

    assert dependency_provider.seen_projects == [project]
    assert isinstance(runtime.observed_file_source, LocalProjectIndexObservedFileSource)
    assert isinstance(runtime.change_detector, ChangeDetector)
    assert isinstance(runtime.maintenance_runner, StoreProjectIndexMaintenanceRunner)
    assert isinstance(runtime.completion_relation_runtime, RepositoryRelationResolutionRuntime)
    assert isinstance(runtime.batch_enqueuer, LocalProjectIndexBatchEnqueuer)
    assert runtime.batch_size == 3
