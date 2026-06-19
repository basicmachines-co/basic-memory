from dataclasses import FrozenInstanceError

import pytest

from basic_memory.indexing import (
    CurrentMaterializedNoteEntity,
    CurrentMaterializedNotePlan,
    EmbeddingIndexTarget,
    FileIndexOperation,
    FileIndexResult,
    IndexedFileLiveUpdatePlan,
    IndexFileBatchJobResult,
    IndexFileJobResult,
    IndexFileJobStatus,
    plan_indexed_file_live_update_metadata,
    plan_current_materialized_note_result,
)
from basic_memory.runtime import (
    NOTE_OBJECT_ACTOR_KIND_MCP_CLIENT,
    NOTE_OBJECT_ACTOR_KIND_METADATA,
    NOTE_OBJECT_ACTOR_NAME_METADATA,
    NOTE_OBJECT_ACTOR_USER_PROFILE_ID_METADATA,
    NOTE_OBJECT_DB_VERSION_METADATA,
    NOTE_OBJECT_FILE_CHECKSUM_METADATA,
    NOTE_OBJECT_SOURCE_METADATA,
    RuntimeStorageObjectChecksumSource,
)


def test_file_index_result_is_a_frozen_success_value():
    result = FileIndexResult(
        file_path="notes/a.md",
        entity_id=42,
        external_id="note-42",
        title="A Note",
        permalink="notes/a-note",
        checksum="checksum-1",
        operation=FileIndexOperation.created,
    )

    assert result.operation.value == "created"
    assert result.file_path == "notes/a.md"
    with pytest.raises(FrozenInstanceError):
        setattr(result, "checksum", "checksum-2")


def test_file_index_result_from_fields_validates_required_entity_text():
    result = FileIndexResult.from_fields(
        file_path="notes/a.md",
        entity_id=42,
        external_id="note-42",
        title="A Note",
        permalink="notes/a-note",
        checksum="checksum-1",
        operation=FileIndexOperation.created,
    )

    assert result == FileIndexResult(
        file_path="notes/a.md",
        entity_id=42,
        external_id="note-42",
        title="A Note",
        permalink="notes/a-note",
        checksum="checksum-1",
        operation=FileIndexOperation.created,
    )

    with pytest.raises(RuntimeError, match="Indexed entity for notes/a.md is missing title"):
        FileIndexResult.from_fields(
            file_path="notes/a.md",
            entity_id=42,
            external_id="note-42",
            title="",
            permalink="notes/a-note",
            checksum="checksum-1",
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


def test_plan_current_materialized_note_result_preserves_trusted_live_update_metadata():
    entity = CurrentMaterializedNoteEntity.from_fields(
        entity_id=42,
        external_id="note-42",
        title="A Note",
        permalink="notes/a-note",
        checksum="checksum-1",
        file_path="notes/a.md",
    )

    plan = plan_current_materialized_note_result(
        reason="file already indexed: notes/a.md",
        file_path="notes/a.md",
        object_checksum="storage-native-etag",
        object_metadata={
            NOTE_OBJECT_FILE_CHECKSUM_METADATA: "checksum-1",
            NOTE_OBJECT_ACTOR_USER_PROFILE_ID_METADATA: ("33333333-3333-3333-3333-333333333333"),
            NOTE_OBJECT_ACTOR_KIND_METADATA: NOTE_OBJECT_ACTOR_KIND_MCP_CLIENT,
            NOTE_OBJECT_ACTOR_NAME_METADATA: "Claude Code",
            NOTE_OBJECT_SOURCE_METADATA: "mcp",
            NOTE_OBJECT_DB_VERSION_METADATA: "1",
        },
        entity=entity,
    )

    assert plan == CurrentMaterializedNotePlan(
        job_result=IndexFileJobResult(
            status=IndexFileJobStatus.current,
            reason="file already indexed: notes/a.md",
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
        ),
        object_checksum_source=RuntimeStorageObjectChecksumSource.note_file_checksum,
        object_checksum="checksum-1",
        entity_checksum="checksum-1",
        source="mcp",
        checksum_matches_entity=True,
    )


def test_plan_current_materialized_note_result_omits_ambiguous_metadata():
    entity = CurrentMaterializedNoteEntity.from_fields(
        entity_id=42,
        external_id="note-42",
        title="A Note",
        permalink="notes/a-note",
        checksum="checksum-1",
        file_path="notes/a.md",
    )

    plan = plan_current_materialized_note_result(
        reason="file already indexed: notes/a.md",
        file_path="notes/a.md",
        object_checksum="storage-native-etag",
        object_metadata={
            NOTE_OBJECT_FILE_CHECKSUM_METADATA: "checksum-1",
            NOTE_OBJECT_SOURCE_METADATA: "mcp",
        },
        entity=entity,
    )

    assert plan == CurrentMaterializedNotePlan(
        job_result=IndexFileJobResult(
            status=IndexFileJobStatus.current,
            reason="file already indexed: notes/a.md",
        ),
        source="mcp",
    )


def test_plan_current_materialized_note_result_requests_entity_when_metadata_is_trusted():
    plan = plan_current_materialized_note_result(
        reason="file already indexed: notes/a.md",
        file_path="notes/a.md",
        object_checksum="storage-native-etag",
        object_metadata={
            NOTE_OBJECT_FILE_CHECKSUM_METADATA: "checksum-1",
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
        source="mcp",
    )


def test_plan_indexed_file_live_update_metadata_preserves_matching_metadata():
    indexed_file = FileIndexResult(
        file_path="notes/a.md",
        entity_id=42,
        external_id="note-42",
        title="A Note",
        permalink="notes/a-note",
        checksum="checksum-1",
        operation=FileIndexOperation.updated,
    )

    plan = plan_indexed_file_live_update_metadata(
        indexed_file=indexed_file,
        object_checksum="storage-native-etag",
        object_metadata={
            NOTE_OBJECT_FILE_CHECKSUM_METADATA: "checksum-1",
            NOTE_OBJECT_ACTOR_USER_PROFILE_ID_METADATA: ("33333333-3333-3333-3333-333333333333"),
            NOTE_OBJECT_ACTOR_KIND_METADATA: NOTE_OBJECT_ACTOR_KIND_MCP_CLIENT,
            NOTE_OBJECT_ACTOR_NAME_METADATA: "Claude Code",
            NOTE_OBJECT_SOURCE_METADATA: "mcp",
            NOTE_OBJECT_DB_VERSION_METADATA: "2",
        },
    )

    assert plan == IndexedFileLiveUpdatePlan(
        object_checksum_source=RuntimeStorageObjectChecksumSource.note_file_checksum,
        object_checksum="checksum-1",
        indexed_checksum="checksum-1",
        checksum_matches_indexed_file=True,
        metadata_actor_user_profile_id="33333333-3333-3333-3333-333333333333",
        metadata_actor_kind=NOTE_OBJECT_ACTOR_KIND_MCP_CLIENT,
        metadata_actor_name="Claude Code",
        metadata_source="mcp",
        actor_user_profile_id="33333333-3333-3333-3333-333333333333",
        actor_kind=NOTE_OBJECT_ACTOR_KIND_MCP_CLIENT,
        actor_name="Claude Code",
        live_update_source="mcp",
        operation=FileIndexOperation.updated,
    )


def test_plan_indexed_file_live_update_metadata_omits_mismatched_metadata():
    indexed_file = FileIndexResult(
        file_path="notes/a.md",
        entity_id=42,
        external_id="note-42",
        title="A Note",
        permalink="notes/a-note",
        checksum="checksum-1",
        operation=FileIndexOperation.updated,
    )

    plan = plan_indexed_file_live_update_metadata(
        indexed_file=indexed_file,
        object_checksum="storage-native-etag",
        object_metadata={
            NOTE_OBJECT_FILE_CHECKSUM_METADATA: "checksum-2",
            NOTE_OBJECT_ACTOR_USER_PROFILE_ID_METADATA: ("33333333-3333-3333-3333-333333333333"),
            NOTE_OBJECT_ACTOR_KIND_METADATA: NOTE_OBJECT_ACTOR_KIND_MCP_CLIENT,
            NOTE_OBJECT_ACTOR_NAME_METADATA: "Claude Code",
            NOTE_OBJECT_SOURCE_METADATA: "mcp",
            NOTE_OBJECT_DB_VERSION_METADATA: "2",
        },
    )

    assert plan == IndexedFileLiveUpdatePlan(
        object_checksum_source=RuntimeStorageObjectChecksumSource.note_file_checksum,
        object_checksum="checksum-2",
        indexed_checksum="checksum-1",
        checksum_matches_indexed_file=False,
        metadata_actor_user_profile_id="33333333-3333-3333-3333-333333333333",
        metadata_actor_kind=NOTE_OBJECT_ACTOR_KIND_MCP_CLIENT,
        metadata_actor_name="Claude Code",
        metadata_source="mcp",
        actor_user_profile_id=None,
        actor_kind=None,
        actor_name=None,
        live_update_source=None,
        operation=None,
    )
