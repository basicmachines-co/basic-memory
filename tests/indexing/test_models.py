from dataclasses import FrozenInstanceError

import pytest

from basic_memory.indexing.embedding_index_planning import (
    EmbeddingIndexJobRequest,
    EmbeddingIndexTarget,
)
from basic_memory.indexing.file_index_planning import FileIndexDecision, FileIndexDecisionStatus
from basic_memory.indexing.index_file_runtime import IndexFileRuntimeRequest
from basic_memory.indexing.models import (
    CurrentMaterializedNoteEntity,
    CurrentMaterializedNotePlan,
    FileIndexOperation,
    FileIndexResult,
    IndexFileBatchJobResult,
    IndexFileEmbeddingJobContext,
    IndexFileJobResult,
    IndexFileJobStatus,
    IndexFileNoteLiveUpdateContext,
    IndexFileNoteLiveUpdatePlan,
    IndexFileNoteLiveUpdateType,
    IndexedEntity,
    IndexedFileLiveUpdatePlan,
    apply_project_index_batch_job_results,
    build_index_file_batch_job_result,
    index_file_job_result_from_decision,
    index_file_job_result_from_indexed_file,
    plan_current_materialized_note_result,
    plan_index_file_embedding_job,
    plan_index_file_note_live_update,
    plan_indexed_file_live_update_metadata,
    project_index_file_outcome_from_job_result,
    project_index_file_outcomes_from_job_results,
)
from basic_memory.indexing.project_index_progress import (
    ObservedObjectIndexCompletionContext,
    ProjectIndexBatchCounterUpdate,
    ProjectIndexCounters,
    ProjectIndexFileOutcome,
)
from basic_memory.indexing.relation_resolution import IndexFileRelationResolutionContext
from basic_memory.runtime.jobs import (
    RuntimeStorageFileIndexContext,
    RuntimeStorageFileIndexJobIdentity,
    RuntimeStorageFileIndexMode,
    RuntimeStorageObjectObservation,
)
from basic_memory.runtime.note_object_metadata import (
    NOTE_OBJECT_ACTOR_KIND_MCP_CLIENT,
    NOTE_OBJECT_ACTOR_KIND_METADATA,
    NOTE_OBJECT_ACTOR_NAME_METADATA,
    NOTE_OBJECT_ACTOR_USER_PROFILE_ID_METADATA,
    NOTE_OBJECT_DB_VERSION_METADATA,
    NOTE_OBJECT_FILE_CHECKSUM_METADATA,
    NOTE_OBJECT_SOURCE_METADATA,
)
from basic_memory.runtime.projects import ProjectRuntimeReference
from basic_memory.runtime.storage import (
    StorageEventPayload,
    StorageObjectIdentity,
    StorageObjectVersion,
)


def test_file_index_result_is_a_frozen_success_value():
    result = FileIndexResult(
        indexed_bytes=0,
        file_path="notes/a.md",
        entity_id=42,
        external_id="note-42",
        title="A Note",
        permalink="notes/a-note",
        checksum="checksum-1",
        content_checksum="content-1",
        operation=FileIndexOperation.created,
    )

    assert result.operation.value == "created"
    assert result.file_path == "notes/a.md"
    with pytest.raises(FrozenInstanceError):
        setattr(result, "checksum", "checksum-2")


def test_file_index_result_from_fields_validates_required_entity_text():
    result = FileIndexResult.from_fields(
        indexed_bytes=0,
        file_path="notes/a.md",
        entity_id=42,
        external_id=" note-42 ",
        title=" A Note ",
        permalink=" notes/a-note ",
        checksum="checksum-1",
        content_checksum="content-1",
        operation=FileIndexOperation.created,
    )

    assert result == FileIndexResult(
        indexed_bytes=0,
        file_path="notes/a.md",
        entity_id=42,
        external_id="note-42",
        title="A Note",
        permalink="notes/a-note",
        checksum="checksum-1",
        content_checksum="content-1",
        operation=FileIndexOperation.created,
    )

    with pytest.raises(RuntimeError, match="Indexed entity for notes/a.md is missing title"):
        FileIndexResult.from_fields(
            indexed_bytes=0,
            file_path="notes/a.md",
            entity_id=42,
            external_id="note-42",
            title="",
            permalink="notes/a-note",
            checksum="checksum-1",
            content_checksum="content-1",
            operation=FileIndexOperation.created,
        )


def test_file_index_result_from_fields_validates_optional_permalink_text():
    result = FileIndexResult.from_fields(
        indexed_bytes=0,
        file_path="notes/a.md",
        entity_id=42,
        external_id="note-42",
        title="A Note",
        permalink=None,
        checksum="checksum-1",
        content_checksum="content-1",
        operation=FileIndexOperation.created,
    )

    assert result.permalink is None

    with pytest.raises(RuntimeError, match="Indexed entity for notes/a.md has invalid permalink"):
        FileIndexResult.from_fields(
            indexed_bytes=0,
            file_path="notes/a.md",
            entity_id=42,
            external_id="note-42",
            title="A Note",
            permalink=123,
            checksum="checksum-1",
            content_checksum="content-1",
            operation=FileIndexOperation.created,
        )

    with pytest.raises(RuntimeError, match="Indexed entity for notes/a.md has blank permalink"):
        FileIndexResult.from_fields(
            indexed_bytes=0,
            file_path="notes/a.md",
            entity_id=42,
            external_id="note-42",
            title="A Note",
            permalink="  ",
            checksum="checksum-1",
            content_checksum="content-1",
            operation=FileIndexOperation.created,
        )


def test_index_file_job_result_carries_live_update_metadata():
    result = IndexFileJobResult(
        status=IndexFileJobStatus.processed,
        reason="file indexed: notes/a.md",
        entity_id=42,
        note_external_id="note-42",
        title="A Note",
        permalink="notes/a-note",
        entity_checksum="checksum-1",
        operation=FileIndexOperation.updated,
        actor_user_profile_id="user-1",
        actor_kind="mcp_client",
        actor_name="Claude Code",
        live_update_source="mcp",
    )

    assert result.status.value == "processed"
    assert result.operation == FileIndexOperation.updated
    with pytest.raises(FrozenInstanceError):
        setattr(result, "reason", "changed")


def test_index_file_job_result_from_terminal_current_decision():
    decision = FileIndexDecision(
        path="notes/current.md",
        status=FileIndexDecisionStatus.current,
        reason="file already indexed: notes/current.md",
    )

    assert index_file_job_result_from_decision(decision) == IndexFileJobResult(
        status=IndexFileJobStatus.current,
        reason="file already indexed: notes/current.md",
    )


def test_index_file_job_result_from_terminal_missing_decision():
    decision = FileIndexDecision(
        path="notes/missing.md",
        status=FileIndexDecisionStatus.missing,
        reason="file not found: notes/missing.md",
    )

    assert index_file_job_result_from_decision(decision) == IndexFileJobResult(
        status=IndexFileJobStatus.missing,
        reason="file not found: notes/missing.md",
    )


def test_index_file_job_result_from_read_decision_fails_fast():
    decision = FileIndexDecision(
        path="notes/read.md",
        status=FileIndexDecisionStatus.read,
        reason="file needs indexing: notes/read.md",
    )

    with pytest.raises(RuntimeError, match="Unexpected file index decision"):
        index_file_job_result_from_decision(decision)


@pytest.mark.parametrize(
    ("indexed_superseded", "plan_superseded", "expected_bytes"),
    [(False, False, 120), (True, False, 0), (False, True, 0)],
)
def test_index_file_job_result_bills_bytes_only_for_current_content(
    indexed_superseded: bool,
    plan_superseded: bool,
    expected_bytes: int,
):
    """A superseded result describes replaced content; the newer write bills its bytes."""
    indexed_file = FileIndexResult(
        file_path="notes/a.md",
        entity_id=42,
        external_id="note-42",
        title="A Note",
        permalink="notes/a-note",
        checksum="checksum-1",
        content_checksum="content-1",
        operation=FileIndexOperation.updated,
        indexed_bytes=120,
        content_superseded=indexed_superseded,
    )

    result = index_file_job_result_from_indexed_file(
        indexed_file,
        live_update_plan=IndexedFileLiveUpdatePlan(content_superseded=plan_superseded),
    )

    assert result.indexed_bytes == expected_bytes


def test_index_file_job_result_from_indexed_file_uses_trusted_live_update_plan():
    indexed_file = FileIndexResult(
        indexed_bytes=0,
        file_path="notes/a.md",
        entity_id=42,
        external_id="note-42",
        title="A Note",
        permalink="notes/a-note",
        checksum="checksum-1",
        content_checksum="content-1",
        operation=FileIndexOperation.updated,
    )
    live_update_plan = IndexedFileLiveUpdatePlan(
        content_superseded=False,
        actor_user_profile_id="33333333-3333-3333-3333-333333333333",
        actor_kind=NOTE_OBJECT_ACTOR_KIND_MCP_CLIENT,
        actor_name="Claude Code",
        live_update_source="mcp",
        operation=FileIndexOperation.created,
    )

    assert index_file_job_result_from_indexed_file(
        indexed_file,
        live_update_plan=live_update_plan,
    ) == IndexFileJobResult(
        status=IndexFileJobStatus.processed,
        reason="file indexed: notes/a.md",
        entity_id=42,
        note_external_id="note-42",
        title="A Note",
        permalink="notes/a-note",
        entity_checksum="checksum-1",
        operation=FileIndexOperation.created,
        actor_user_profile_id="33333333-3333-3333-3333-333333333333",
        actor_kind=NOTE_OBJECT_ACTOR_KIND_MCP_CLIENT,
        actor_name="Claude Code",
        live_update_source="mcp",
    )

    assert index_file_job_result_from_indexed_file(indexed_file) == IndexFileJobResult(
        status=IndexFileJobStatus.processed,
        reason="file indexed: notes/a.md",
        entity_id=42,
        note_external_id="note-42",
        title="A Note",
        permalink="notes/a-note",
        entity_checksum="checksum-1",
        operation=FileIndexOperation.updated,
    )


def test_plan_index_file_embedding_job_requires_processed_entity_identity():
    processed = IndexFileJobResult(
        status=IndexFileJobStatus.processed,
        reason="file indexed: notes/a.md",
        entity_id=42,
        entity_checksum="checksum-1",
    )

    assert plan_index_file_embedding_job(
        IndexFileEmbeddingJobContext(
            project_id=7,
            index_embeddings=True,
            result=processed,
        )
    ) == EmbeddingIndexJobRequest(
        project_id=7,
        entity_id=42,
        entity_checksum="checksum-1",
    )
    assert (
        plan_index_file_embedding_job(
            IndexFileEmbeddingJobContext(
                project_id=7,
                index_embeddings=False,
                result=processed,
            )
        )
        is None
    )
    assert (
        plan_index_file_embedding_job(
            IndexFileEmbeddingJobContext(
                project_id=7,
                index_embeddings=True,
                result=IndexFileJobResult(
                    status=IndexFileJobStatus.current,
                    reason="file already indexed: notes/a.md",
                ),
            )
        )
        is None
    )

    with pytest.raises(RuntimeError, match="processed without an entity id"):
        plan_index_file_embedding_job(
            IndexFileEmbeddingJobContext(
                project_id=7,
                index_embeddings=True,
                result=IndexFileJobResult(
                    status=IndexFileJobStatus.processed,
                    reason="file indexed: notes/a.md",
                    entity_checksum="checksum-1",
                ),
            )
        )
    with pytest.raises(RuntimeError, match="processed without an entity checksum"):
        plan_index_file_embedding_job(
            IndexFileEmbeddingJobContext(
                project_id=7,
                index_embeddings=True,
                result=IndexFileJobResult(
                    status=IndexFileJobStatus.processed,
                    reason="file indexed: notes/a.md",
                    entity_id=42,
                ),
            )
        )


def test_index_file_runtime_request_derives_worker_handoff_contexts():
    result = IndexFileJobResult(
        status=IndexFileJobStatus.processed,
        reason="file indexed: notes/a.md",
        entity_id=42,
        entity_checksum="checksum-1",
    )
    request = IndexFileRuntimeRequest(
        project_id=7,
        project_external_id="project-7",
        project_name="Project Seven",
        project_path="project",
        file_path="notes/a.md",
        mode=RuntimeStorageFileIndexMode.observed_object,
        object_observation=RuntimeStorageObjectObservation(etag='"etag-1"', size=12),
        index_embeddings=False,
    )

    assert request.storage_job_identity() == RuntimeStorageFileIndexJobIdentity(
        project_id=7,
        file_path="notes/a.md",
        mode=RuntimeStorageFileIndexMode.observed_object,
        object_etag='"etag-1"',
        object_size=12,
    )
    assert request.storage_index_context() == RuntimeStorageFileIndexContext(
        mode=RuntimeStorageFileIndexMode.observed_object,
        project_external_id="project-7",
        project_name="Project Seven",
    )
    assert request.note_live_update_context() == IndexFileNoteLiveUpdateContext(
        project_external_id="project-7",
        project_name="Project Seven",
        file_path="notes/a.md",
        mode=RuntimeStorageFileIndexMode.observed_object,
        object_etag='"etag-1"',
        object_size=12,
    )
    assert request.observed_object_completion_context() == ObservedObjectIndexCompletionContext(
        project_external_id="project-7",
        project_name="Project Seven",
        project_path="project",
        mode=RuntimeStorageFileIndexMode.observed_object,
    )
    assert request.relation_resolution_context(
        IndexFileJobStatus.processed
    ) == IndexFileRelationResolutionContext(
        project_id=7,
        project_path="project",
        status=IndexFileJobStatus.processed,
    )
    assert request.embedding_job_context(result) == IndexFileEmbeddingJobContext(
        project_id=7,
        index_embeddings=False,
        result=result,
    )

    with pytest.raises(FrozenInstanceError):
        setattr(request, "file_path", "notes/b.md")


def test_index_file_runtime_request_from_storage_event_uses_observed_object_identity():
    project = ProjectRuntimeReference(
        project_id=7,
        project_external_id="project-7",
        project_path="project",
        project_name="Project Seven",
    )
    storage_event = StorageEventPayload(
        event_name="OBJECT_CREATED_PUT",
        event_time="2026-06-19T12:00:00Z",
        object_version=StorageObjectVersion(
            identity=StorageObjectIdentity(
                bucket_name="tenant-bucket",
                key="project/notes/a.md",
            ),
            etag='"etag-1"',
            size=12,
        ),
    )

    assert IndexFileRuntimeRequest.from_storage_event(
        project=project,
        storage_event=storage_event,
    ) == IndexFileRuntimeRequest(
        project_id=7,
        project_external_id="project-7",
        project_name="Project Seven",
        project_path="project",
        file_path="notes/a.md",
        mode=RuntimeStorageFileIndexMode.observed_object,
        object_observation=RuntimeStorageObjectObservation(etag='"etag-1"', size=12),
    )


def test_plan_index_file_note_live_update_builds_typed_note_change_plan():
    context = IndexFileNoteLiveUpdateContext(
        project_external_id="project-1",
        project_name="Project One",
        file_path="notes/a.md",
        mode=RuntimeStorageFileIndexMode.observed_object,
        object_etag='"etag-1"',
        object_size=12,
    )
    result = IndexFileJobResult(
        status=IndexFileJobStatus.processed,
        reason="file indexed: notes/a.md",
        entity_id=42,
        note_external_id="note-42",
        title="A Note",
        permalink="notes/a-note",
        entity_checksum="checksum-1",
        operation=FileIndexOperation.created,
        actor_user_profile_id="33333333-3333-3333-3333-333333333333",
        actor_kind=NOTE_OBJECT_ACTOR_KIND_MCP_CLIENT,
        actor_name="Claude Code",
        live_update_source="mcp",
    )

    assert plan_index_file_note_live_update(context, result) == IndexFileNoteLiveUpdatePlan(
        event_type=IndexFileNoteLiveUpdateType.note_created,
        source="mcp",
        project_external_id="project-1",
        project_name="Project One",
        note_external_id="note-42",
        note_path="notes/a.md",
        note_version_etag="etag-1",
        content_checksum="checksum-1",
        file_checksum="etag-1",
        file_size_bytes=12,
        title="A Note",
        permalink="notes/a-note",
        actor_user_profile_id="33333333-3333-3333-3333-333333333333",
        actor_kind=NOTE_OBJECT_ACTOR_KIND_MCP_CLIENT,
        actor_name="Claude Code",
    )


def test_plan_index_file_note_live_update_skips_non_note_change_results():
    context = IndexFileNoteLiveUpdateContext(
        project_external_id="project-1",
        project_name="Project One",
        file_path="notes/a.md",
        mode=RuntimeStorageFileIndexMode.observed_object,
        object_etag='"etag-1"',
        object_size=12,
    )

    assert (
        plan_index_file_note_live_update(
            IndexFileNoteLiveUpdateContext(
                project_external_id=context.project_external_id,
                project_name=context.project_name,
                file_path=context.file_path,
                mode=RuntimeStorageFileIndexMode.current_file,
            ),
            IndexFileJobResult(
                status=IndexFileJobStatus.processed,
                reason="file indexed: notes/a.md",
            ),
        )
        is None
    )
    assert (
        plan_index_file_note_live_update(
            context,
            IndexFileJobResult(
                status=IndexFileJobStatus.current,
                reason="file already indexed: notes/a.md",
            ),
        )
        is None
    )
    assert (
        plan_index_file_note_live_update(
            context,
            IndexFileJobResult(
                status=IndexFileJobStatus.missing,
                reason="file not found: notes/a.md",
            ),
        )
        is None
    )


def test_plan_index_file_note_live_update_validates_required_observed_object_fields():
    context = IndexFileNoteLiveUpdateContext(
        project_external_id=None,
        project_name="Project One",
        file_path="notes/a.md",
        mode=RuntimeStorageFileIndexMode.observed_object,
        object_etag='"etag-1"',
        object_size=12,
    )
    result = IndexFileJobResult(
        status=IndexFileJobStatus.processed,
        reason="file indexed: notes/a.md",
        note_external_id="note-42",
        title="A Note",
        permalink="notes/a-note",
        operation=FileIndexOperation.updated,
    )

    with pytest.raises(RuntimeError, match="missing project_external_id"):
        plan_index_file_note_live_update(context, result)


def test_index_file_batch_job_result_carries_ordered_file_and_vector_targets():
    file_result = IndexFileJobResult(
        status=IndexFileJobStatus.processed,
        reason="file indexed: notes/a.md",
        entity_id=42,
        entity_checksum="checksum-1",
    )
    vector_target = EmbeddingIndexTarget(entity_id=42, entity_checksum="checksum-1")

    result = IndexFileBatchJobResult(
        total_files=1,
        processed_files=1,
        missing_files=0,
        failed_files=0,
        file_results=(file_result,),
        vector_targets=(vector_target,),
    )

    assert result.file_results == (file_result,)
    assert result.vector_targets == (vector_target,)
    with pytest.raises(FrozenInstanceError):
        setattr(result, "processed_files", 2)


def test_build_index_file_batch_job_result_preserves_order_and_embedding_targets():
    current_result = IndexFileJobResult(
        status=IndexFileJobStatus.current,
        reason="file already indexed: notes/current.md",
    )
    missing_result = IndexFileJobResult(
        status=IndexFileJobStatus.missing,
        reason="file not found: notes/missing.md",
    )

    result = build_index_file_batch_job_result(
        target_paths=(
            "notes/current.md",
            "notes/processed.md",
            "notes/missing.md",
            "notes/failed.md",
        ),
        terminal_results={
            "notes/current.md": current_result,
            "notes/missing.md": missing_result,
        },
        indexed_files=(
            IndexedEntity(
                indexed_bytes=0,
                path="notes/processed.md",
                entity_id=42,
                permalink="notes/processed",
                checksum="checksum-processed",
            ),
            IndexedEntity(
                indexed_bytes=0,
                path="notes/failed.md",
                entity_id=43,
                permalink="notes/failed",
                checksum="checksum-failed",
            ),
        ),
        errors={"notes/failed.md": "parse failed"},
        index_embeddings=True,
        embedding_eligible_paths=("notes/processed.md",),
    )

    assert result == IndexFileBatchJobResult(
        total_files=4,
        processed_files=2,
        missing_files=1,
        failed_files=1,
        file_results=(
            current_result,
            IndexFileJobResult(
                status=IndexFileJobStatus.processed,
                reason="file indexed: notes/processed.md",
                entity_id=42,
                entity_checksum="checksum-processed",
            ),
            missing_result,
            IndexFileJobResult(
                status=IndexFileJobStatus.failed,
                reason="file indexing failed: notes/failed.md: parse failed",
                entity_id=43,
                entity_checksum="checksum-failed",
            ),
        ),
        vector_targets=(EmbeddingIndexTarget(entity_id=42, entity_checksum="checksum-processed"),),
    )


def test_project_index_outcomes_from_file_job_results_update_batch_counters():
    results = (
        IndexFileJobResult(
            status=IndexFileJobStatus.processed,
            reason="file indexed: notes/processed.md",
        ),
        IndexFileJobResult(
            status=IndexFileJobStatus.current,
            reason="file already indexed: notes/current.md",
        ),
        IndexFileJobResult(
            status=IndexFileJobStatus.missing,
            reason="file not found: notes/missing.md",
        ),
        IndexFileJobResult(
            status=IndexFileJobStatus.failed,
            reason="file indexing failed: notes/failed.md: parse failed",
        ),
    )

    assert (
        project_index_file_outcome_from_job_result(results[0]) == ProjectIndexFileOutcome.processed
    )
    assert project_index_file_outcomes_from_job_results(results) == (
        ProjectIndexFileOutcome.processed,
        ProjectIndexFileOutcome.current,
        ProjectIndexFileOutcome.missing,
        ProjectIndexFileOutcome.failed,
    )

    update = apply_project_index_batch_job_results(
        counters=ProjectIndexCounters(
            total=4,
            processed=0,
            succeeded=0,
            missing=0,
            failed=0,
        ),
        recorded_batch_indexes=[],
        batch_index=0,
        batch_count=1,
        results=results,
    )

    assert update == ProjectIndexBatchCounterUpdate(
        counters=ProjectIndexCounters(
            total=4,
            processed=4,
            succeeded=2,
            missing=1,
            failed=1,
        ),
        recorded_batch_indexes=[0],
        already_recorded=False,
        all_batches_recorded=True,
    )


def test_current_materialized_note_entity_from_fields_requires_indexed_permalink():
    with pytest.raises(RuntimeError, match="Current entity for notes/a.md is missing permalink"):
        CurrentMaterializedNoteEntity.from_fields(
            entity_id=42,
            external_id="note-42",
            title="A Note",
            permalink=None,
            storage_checksum="etag-1",
            content_checksum="content-1",
            file_path="notes/a.md",
        )


def test_current_materialized_note_entity_from_fields_validates_identity_text():
    entity = CurrentMaterializedNoteEntity.from_fields(
        entity_id=42,
        external_id=" note-42 ",
        title=" A Note ",
        permalink=" notes/a-note ",
        storage_checksum="etag-1",
        content_checksum="content-1",
        file_path="notes/a.md",
    )

    assert entity == CurrentMaterializedNoteEntity(
        entity_id=42,
        external_id="note-42",
        title="A Note",
        permalink="notes/a-note",
        storage_checksum="etag-1",
        content_checksum="content-1",
    )

    not_yet_indexed = CurrentMaterializedNoteEntity.from_fields(
        entity_id=42,
        external_id="note-42",
        title="A Note",
        permalink="notes/a-note",
        storage_checksum=None,
        content_checksum=None,
        file_path="notes/a.md",
    )

    assert not_yet_indexed.storage_checksum is None
    assert not_yet_indexed.content_checksum is None

    with pytest.raises(RuntimeError, match="Current entity for notes/a.md is missing title"):
        CurrentMaterializedNoteEntity.from_fields(
            entity_id=42,
            external_id="note-42",
            title="  ",
            permalink="notes/a-note",
            storage_checksum="etag-1",
            content_checksum="content-1",
            file_path="notes/a.md",
        )


# Cloud shape for every planner test below: storage checksums are S3 ETags
# ("etag-*"); bm-file-checksum and indexed content checksums are content sha256s
# ("content-*"). The two kinds never equal each other.
MCP_NOTE_OBJECT_METADATA = {
    NOTE_OBJECT_FILE_CHECKSUM_METADATA: "content-1",
    NOTE_OBJECT_ACTOR_USER_PROFILE_ID_METADATA: "33333333-3333-3333-3333-333333333333",
    NOTE_OBJECT_ACTOR_KIND_METADATA: NOTE_OBJECT_ACTOR_KIND_MCP_CLIENT,
    NOTE_OBJECT_ACTOR_NAME_METADATA: "Claude Code",
    NOTE_OBJECT_SOURCE_METADATA: "mcp",
    NOTE_OBJECT_DB_VERSION_METADATA: "1",
}


def materialized_entity(*, storage_checksum: str, content_checksum: str | None):
    return CurrentMaterializedNoteEntity(
        entity_id=42,
        external_id="note-42",
        title="A Note",
        permalink="notes/a-note",
        storage_checksum=storage_checksum,
        content_checksum=content_checksum,
    )


def test_plan_current_materialized_note_result_trusts_provenance_of_the_indexed_object():
    """The index holds this object and its bm-file-checksum names the indexed markdown."""
    plan = plan_current_materialized_note_result(
        reason="file already indexed: notes/a.md",
        file_path="notes/a.md",
        object_checksum="etag-1",
        object_metadata=MCP_NOTE_OBJECT_METADATA,
        entity=materialized_entity(storage_checksum="etag-1", content_checksum="content-1"),
    )

    assert plan == CurrentMaterializedNotePlan(
        job_result=IndexFileJobResult(
            status=IndexFileJobStatus.current,
            reason="file already indexed: notes/a.md",
            entity_id=42,
            note_external_id="note-42",
            title="A Note",
            permalink="notes/a-note",
            entity_checksum="etag-1",
            operation=FileIndexOperation.created,
            actor_user_profile_id="33333333-3333-3333-3333-333333333333",
            actor_kind=NOTE_OBJECT_ACTOR_KIND_MCP_CLIENT,
            actor_name="Claude Code",
            live_update_source="mcp",
            db_version=1,
        ),
    )


@pytest.mark.parametrize(
    ("storage_checksum", "content_checksum"),
    [
        # Stale bm-* metadata: the index holds different markdown than it names.
        ("etag-1", "content-2"),
        # The index holds a different object than this one.
        ("etag-2", "content-1"),
        # The note's markdown lineage is not recorded yet.
        ("etag-1", None),
    ],
)
def test_plan_current_materialized_note_result_withholds_untrusted_provenance(
    storage_checksum: str, content_checksum: str | None
):
    plan = plan_current_materialized_note_result(
        reason="file already indexed: notes/a.md",
        file_path="notes/a.md",
        object_checksum="etag-1",
        object_metadata=MCP_NOTE_OBJECT_METADATA,
        entity=materialized_entity(
            storage_checksum=storage_checksum, content_checksum=content_checksum
        ),
    )

    assert plan == CurrentMaterializedNotePlan(
        job_result=IndexFileJobResult(
            status=IndexFileJobStatus.current,
            reason="file already indexed: notes/a.md",
        ),
    )


def test_plan_current_materialized_note_result_omits_ambiguous_metadata():
    plan = plan_current_materialized_note_result(
        reason="file already indexed: notes/a.md",
        file_path="notes/a.md",
        object_checksum="etag-1",
        object_metadata={
            NOTE_OBJECT_FILE_CHECKSUM_METADATA: "content-1",
            NOTE_OBJECT_SOURCE_METADATA: "mcp",
        },
        entity=materialized_entity(storage_checksum="etag-1", content_checksum="content-1"),
    )

    assert plan == CurrentMaterializedNotePlan(
        job_result=IndexFileJobResult(
            status=IndexFileJobStatus.current,
            reason="file already indexed: notes/a.md",
        ),
    )


def test_plan_current_materialized_note_result_requests_entity_when_metadata_is_trusted():
    plan = plan_current_materialized_note_result(
        reason="file already indexed: notes/a.md",
        file_path="notes/a.md",
        object_checksum="etag-1",
        object_metadata={
            NOTE_OBJECT_FILE_CHECKSUM_METADATA: "content-1",
            NOTE_OBJECT_SOURCE_METADATA: "mcp",
            NOTE_OBJECT_DB_VERSION_METADATA: "2",
        },
        entity=None,
    )

    assert plan == CurrentMaterializedNotePlan(
        job_result=IndexFileJobResult(
            status=IndexFileJobStatus.current,
            reason="file already indexed: notes/a.md",
        ),
        requires_entity=True,
    )


def indexed_note(*, checksum: str = "etag-1", content_checksum: str | None = "content-1"):
    return FileIndexResult(
        indexed_bytes=0,
        file_path="notes/a.md",
        entity_id=42,
        external_id="note-42",
        title="A Note",
        permalink="notes/a-note",
        checksum=checksum,
        content_checksum=content_checksum,
        operation=FileIndexOperation.updated,
    )


def test_plan_indexed_file_live_update_metadata_trusts_an_app_written_note():
    """Regression: an ETag-backed note the app wrote is not superseded (plan test D10).

    Comparing bm-file-checksum (a content sha256) with the indexed ETag marked every
    such note superseded, which dropped its embeddings and its provenance.
    """
    plan = plan_indexed_file_live_update_metadata(
        indexed_file=indexed_note(),
        object_checksum="etag-1",
        object_metadata={**MCP_NOTE_OBJECT_METADATA, NOTE_OBJECT_DB_VERSION_METADATA: "2"},
    )

    assert plan == IndexedFileLiveUpdatePlan(
        content_superseded=False,
        actor_user_profile_id="33333333-3333-3333-3333-333333333333",
        actor_kind=NOTE_OBJECT_ACTOR_KIND_MCP_CLIENT,
        actor_name="Claude Code",
        live_update_source="mcp",
        db_version=2,
        operation=FileIndexOperation.updated,
    )


def test_plan_indexed_file_live_update_metadata_supersedes_a_replaced_object():
    """A newer write replaced the object after this job read it, whoever wrote it."""
    plan = plan_indexed_file_live_update_metadata(
        indexed_file=indexed_note(),
        object_checksum="etag-2",
        object_metadata=MCP_NOTE_OBJECT_METADATA,
    )

    assert plan == IndexedFileLiveUpdatePlan(content_superseded=True)


@pytest.mark.parametrize(
    "object_metadata",
    [
        # Stale bm-* metadata re-uploaded on different bytes.
        {**MCP_NOTE_OBJECT_METADATA, NOTE_OBJECT_FILE_CHECKSUM_METADATA: "content-2"},
        # A file written outside the app carries no provenance.
        {},
    ],
)
def test_plan_indexed_file_live_update_metadata_withholds_untrusted_provenance(
    object_metadata: dict[str, str],
):
    plan = plan_indexed_file_live_update_metadata(
        indexed_file=indexed_note(),
        object_checksum="etag-1",
        object_metadata=object_metadata,
    )

    assert plan == IndexedFileLiveUpdatePlan(content_superseded=False)


def test_plan_index_file_note_live_update_superseded_content_omits_checksum():
    context = IndexFileNoteLiveUpdateContext(
        project_external_id="project-1",
        project_name="Project One",
        file_path="notes/a.md",
        mode=RuntimeStorageFileIndexMode.observed_object,
        object_etag='"etag-1"',
        object_size=12,
    )
    result = IndexFileJobResult(
        status=IndexFileJobStatus.processed,
        reason="file indexed: notes/a.md",
        entity_id=42,
        note_external_id="note-42",
        title="A Note",
        permalink="notes/a-note",
        entity_checksum="checksum-1",
        operation=FileIndexOperation.updated,
        content_superseded=True,
    )

    plan = plan_index_file_note_live_update(context, result)

    assert plan is not None
    # Superseded content publishes a state refresh only: no content_checksum,
    # so a collaboration relay never reconciles open docs to the stale version.
    assert plan.content_checksum is None
    assert plan.file_checksum == "etag-1"
