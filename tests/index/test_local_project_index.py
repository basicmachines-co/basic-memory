"""Tests for local project-wide event-index adapters."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
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
