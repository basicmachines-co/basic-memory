"""Tests for runtime mode resolution."""

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta
from uuid import UUID

import pytest

from basic_memory.runtime import (
    NOTE_OBJECT_ACTOR_KIND_MCP_CLIENT,
    NOTE_OBJECT_ACTOR_KIND_METADATA,
    NOTE_OBJECT_ACTOR_NAME_METADATA,
    NOTE_OBJECT_ACTOR_USER_PROFILE_ID_METADATA,
    NOTE_OBJECT_DB_CHECKSUM_METADATA,
    NOTE_OBJECT_DB_VERSION_METADATA,
    NOTE_OBJECT_ENTITY_ID_METADATA,
    NOTE_OBJECT_FILE_CHECKSUM_METADATA,
    NOTE_OBJECT_FILE_VERSION_METADATA,
    NOTE_OBJECT_SOURCE_METADATA,
    RuntimeAcceptedNoteChange,
    RuntimeMode,
    RuntimeNoteObjectMetadata,
    RuntimePreparedNoteWrite,
    RuntimeWrittenFileState,
    actor_kind_from_object_metadata,
    actor_name_from_object_metadata,
    actor_user_profile_id_from_object_metadata,
    db_version_from_object_metadata,
    file_checksum_from_object_metadata,
    normalize_actor_name,
    resolve_runtime_mode,
    source_from_object_metadata,
)
from basic_memory.runtime.contracts import (
    RuntimeDeleteStatus,
    RuntimeCapabilities,
    RuntimeExpectedFileState,
    RuntimeFileDeleteResult,
    RuntimeFileConflictError,
    RuntimeJobCounts,
    RuntimeJobRequest,
    RuntimeNoteMaterializationResult,
    RuntimeNoteMaterializationStatus,
    RuntimePendingNoteFileDelete,
    RuntimePendingNoteMaterialization,
    RuntimeProjectDeleteResult,
    RuntimeQueuedWorkflowMetadata,
    RuntimeStorageEventOperation,
    RuntimeStorageEventOperationKind,
    RuntimeStorageEventProcessingResult,
    RuntimeStorageEventProjectBatch,
    RuntimeStorageEventRoutingPlan,
    RuntimeStorageEventSkipReason,
    RuntimeWorkflowAttemptMetadata,
    RuntimeWorkflowCompletionMetadata,
    RuntimeWorkflowFailureMetadata,
    RuntimeWorkflowMetadataView,
    RuntimeWorkflowProgressMetadata,
    RuntimeWorkflowTransport,
    StorageEventPayload,
    StorageObjectIdentity,
    StorageObjectVersion,
    assert_runtime_file_matches_expected,
    plan_runtime_storage_event_operation,
    plan_runtime_storage_event_operations,
    plan_runtime_storage_events_by_project,
    plan_previous_note_file_delete,
    read_runtime_file_checksum,
    truncate_runtime_workflow_text,
)


class FakeRuntimeFileChecksumReader:
    def __init__(self, checksum: str | None) -> None:
        self.checksum = checksum
        self.exists_calls: list[str] = []
        self.compute_checksum_calls: list[str] = []

    async def exists(self, path: str) -> bool:
        self.exists_calls.append(path)
        return self.checksum is not None

    async def compute_checksum(self, path: str) -> str:
        self.compute_checksum_calls.append(path)
        if self.checksum is None:
            raise AssertionError("missing files should not compute a checksum")
        return self.checksum


class FakeJobRuntime:
    async def enqueue(self, request: RuntimeJobRequest) -> str:
        return f"fake:{request.entrypoint}"


class FakeStorageEventSource:
    def events_by_bucket(self) -> dict[str, tuple[StorageEventPayload, ...]]:
        return {}


class TestRuntimeMode:
    """Tests for RuntimeMode enum."""

    def test_local_mode_properties(self):
        mode = RuntimeMode.LOCAL
        assert mode.is_local is True
        assert mode.is_cloud is False
        assert mode.is_test is False

    def test_cloud_mode_properties(self):
        mode = RuntimeMode.CLOUD
        assert mode.is_local is False
        assert mode.is_cloud is True
        assert mode.is_test is False

    def test_test_mode_properties(self):
        mode = RuntimeMode.TEST
        assert mode.is_local is False
        assert mode.is_cloud is False
        assert mode.is_test is True


class TestResolveRuntimeMode:
    """Tests for resolve_runtime_mode function."""

    def test_resolves_to_test_when_test_env(self):
        """Test environment resolves to TEST mode."""
        mode = resolve_runtime_mode(is_test_env=True)
        assert mode == RuntimeMode.TEST

    def test_resolves_to_local_when_not_test_env(self):
        """Non-test environments resolve to LOCAL mode."""
        mode = resolve_runtime_mode(is_test_env=False)
        assert mode == RuntimeMode.LOCAL

    def test_never_resolves_to_cloud_in_local_app_context(self):
        """Resolver no longer returns CLOUD for local app composition roots."""
        mode = resolve_runtime_mode(is_test_env=False)
        assert mode is not RuntimeMode.CLOUD


class TestRuntimeContracts:
    """Tests for portable runtime contracts shared with hosted adapters."""

    def test_storage_object_identity_splits_project_relative_paths(self):
        identity = StorageObjectIdentity(bucket_name="memory-bucket", key="project/notes/a.md")

        assert identity.project_path == "project"
        assert identity.relative_path == "notes/a.md"

    def test_runtime_storage_event_routing_plan_groups_projects_and_skips_root_objects(self):
        alpha_put = StorageEventPayload(
            event_name="OBJECT_CREATED_PUT",
            event_time="2026-06-19T12:00:00Z",
            object_version=StorageObjectVersion(
                identity=StorageObjectIdentity(
                    bucket_name="memory-bucket",
                    key="alpha/notes/a.md",
                ),
                etag="alpha-a",
            ),
        )
        root_put = StorageEventPayload(
            event_name="OBJECT_CREATED_PUT",
            event_time="2026-06-19T12:01:00Z",
            object_version=StorageObjectVersion(
                identity=StorageObjectIdentity(
                    bucket_name="memory-bucket",
                    key="root.md",
                ),
                etag="root",
            ),
        )
        beta_deleted = StorageEventPayload(
            event_name="OBJECT_DELETED",
            event_time="2026-06-19T12:02:00Z",
            object_version=StorageObjectVersion(
                identity=StorageObjectIdentity(
                    bucket_name="memory-bucket",
                    key="beta/notes/b.md",
                ),
                etag="beta-b",
            ),
        )
        alpha_post = StorageEventPayload(
            event_name="OBJECT_CREATED_POST",
            event_time="2026-06-19T12:03:00Z",
            object_version=StorageObjectVersion(
                identity=StorageObjectIdentity(
                    bucket_name="memory-bucket",
                    key="alpha/notes/c.md",
                ),
                etag="alpha-c",
            ),
        )

        plan = plan_runtime_storage_events_by_project(
            [alpha_put, root_put, beta_deleted, alpha_post]
        )

        assert plan == RuntimeStorageEventRoutingPlan(
            project_batches=(
                RuntimeStorageEventProjectBatch(
                    project_path="alpha",
                    events=(alpha_put, alpha_post),
                ),
                RuntimeStorageEventProjectBatch(
                    project_path="beta",
                    events=(beta_deleted,),
                ),
            ),
            skipped_events=(root_put,),
        )
        assert plan.skipped_count == 1
        assert plan.skipped_counts.as_dict() == {"processed": 0, "failed": 0, "skipped": 1}

        with pytest.raises(FrozenInstanceError):
            setattr(plan, "skipped_events", ())

    def test_runtime_storage_event_operation_plans_index_delete_and_skip_work(self):
        def event(key: str, event_name: str) -> StorageEventPayload:
            return StorageEventPayload(
                event_name=event_name,
                event_time="2026-06-19T12:00:00Z",
                object_version=StorageObjectVersion(
                    identity=StorageObjectIdentity(
                        bucket_name="memory-bucket",
                        key=key,
                    ),
                    etag=f"{event_name}-{key}",
                ),
            )

        created_event = event("project/notes/a.md", "OBJECT_CREATED_PUT")
        deleted_event = event("project/notes/b.md", "OBJECT_DELETED")
        root_event = event("project/", "OBJECT_CREATED_PUT")
        non_markdown_event = event("project/image.png", "OBJECT_CREATED_POST")
        unknown_event = event("project/notes/c.md", "OBJECT_RESTORED")

        operations = plan_runtime_storage_event_operations(
            [
                created_event,
                deleted_event,
                root_event,
                non_markdown_event,
                unknown_event,
            ]
        )

        assert operations == (
            RuntimeStorageEventOperation(
                kind=RuntimeStorageEventOperationKind.index_file,
                storage_event=created_event,
                relative_path="notes/a.md",
            ),
            RuntimeStorageEventOperation(
                kind=RuntimeStorageEventOperationKind.delete_file,
                storage_event=deleted_event,
                relative_path="notes/b.md",
            ),
            RuntimeStorageEventOperation(
                kind=RuntimeStorageEventOperationKind.skip,
                storage_event=root_event,
                skip_reason=RuntimeStorageEventSkipReason.project_root,
            ),
            RuntimeStorageEventOperation(
                kind=RuntimeStorageEventOperationKind.skip,
                storage_event=non_markdown_event,
                relative_path="image.png",
                skip_reason=RuntimeStorageEventSkipReason.non_markdown,
            ),
            RuntimeStorageEventOperation(
                kind=RuntimeStorageEventOperationKind.skip,
                storage_event=unknown_event,
                relative_path="notes/c.md",
                skip_reason=RuntimeStorageEventSkipReason.unknown_event,
            ),
        )
        assert plan_runtime_storage_event_operation(created_event).require_relative_path() == (
            "notes/a.md"
        )

        with pytest.raises(RuntimeError, match="Storage event operation has no relative path"):
            operations[2].require_relative_path()
        with pytest.raises(FrozenInstanceError):
            setattr(operations[0], "kind", RuntimeStorageEventOperationKind.skip)

    def test_runtime_job_request_is_immutable(self):
        request = RuntimeJobRequest(
            entrypoint="index_file",
            payload=b"{}",
            execute_after=timedelta(seconds=30),
            headers={"tenant_id": "tenant-1"},
        )

        with pytest.raises(FrozenInstanceError):
            setattr(request, "entrypoint", "other")

    def test_runtime_queued_workflow_metadata_serializes_existing_shapes(self):
        workflow_id = UUID("22222222-2222-2222-2222-222222222222")
        payload = {"tenant_id": "tenant-1", "project_id": 42}
        metadata = RuntimeQueuedWorkflowMetadata(
            workflow_id=workflow_id,
            progress="queued for indexing",
            payload=payload,
            transport=RuntimeWorkflowTransport(
                broker="pgq",
                entrypoint="index_project",
            ),
        )

        assert metadata.workflow_metadata() == {
            "job_id": str(workflow_id),
            "phase": "queued",
            "progress": "queued for indexing",
            "payload": payload,
            "transport": {
                "broker": "pgq",
                "entrypoint": "index_project",
            },
        }
        assert metadata.queued_event_data(logical_key="index-tenant-project") == {
            "logical_key": "index-tenant-project",
            "entrypoint": "index_project",
            "phase": "queued",
            "progress": "queued for indexing",
            "tenant_id": "tenant-1",
            "project_id": 42,
        }

        with pytest.raises(FrozenInstanceError):
            setattr(metadata, "progress", "changed")

    def test_runtime_workflow_update_metadata_serializes_existing_shapes(self):
        attempt = RuntimeWorkflowAttemptMetadata(
            progress="loading files",
            metadata_patch={"worker_id": "worker-1"},
        )
        assert attempt.workflow_metadata_patch() == {
            "phase": "running",
            "progress": "loading files",
            "worker_id": "worker-1",
        }
        assert attempt.attempt_started_event_data(
            attempt_number=2,
            pgq_job_id="pgq-7",
        ) == {
            "attempt_number": 2,
            "pgq_job_id": "pgq-7",
            "phase": "running",
            "progress": "loading files",
        }

        progress = RuntimeWorkflowProgressMetadata(
            progress="indexing notes",
            phase="running",
            metadata_patch={"indexed": 3},
        )
        assert progress.workflow_metadata_patch() == {
            "progress": "indexing notes",
            "phase": "running",
            "indexed": 3,
        }
        assert progress.progress_event_data() == {
            "phase": "running",
            "progress": "indexing notes",
        }

        no_phase_progress = RuntimeWorkflowProgressMetadata(progress="still queued")
        assert no_phase_progress.workflow_metadata_patch() == {
            "progress": "still queued",
        }
        assert no_phase_progress.progress_event_data() == {
            "phase": None,
            "progress": "still queued",
        }

        completion = RuntimeWorkflowCompletionMetadata(
            result={"processed": 4},
            metadata_patch={"finished_by": "worker-1"},
        )
        assert completion.workflow_metadata_patch() == {
            "phase": "completed",
            "progress": "completed",
            "result": {"processed": 4},
            "finished_by": "worker-1",
        }
        assert completion.completed_event_data() == {
            "phase": "completed",
            "progress": "completed",
            "result": {"processed": 4},
        }

        failure = RuntimeWorkflowFailureMetadata(
            error_message="worker crashed",
            progress="failed while indexing",
            metadata_patch={"retryable": False},
        )
        assert failure.workflow_metadata_patch() == {
            "phase": "failed",
            "progress": "failed while indexing",
            "error_message": "worker crashed",
            "retryable": False,
        }
        assert failure.failed_event_data() == {
            "phase": "failed",
            "progress": "failed while indexing",
            "error_message": "worker crashed",
        }

        with pytest.raises(FrozenInstanceError):
            setattr(failure, "progress", "changed")

    def test_truncate_runtime_workflow_text_matches_existing_preview_shape(self):
        assert truncate_runtime_workflow_text("short text", max_chars=32) == "short text"

        long_text = "x" * 50
        assert (
            truncate_runtime_workflow_text(long_text, max_chars=32)
            == "xxxxxxxx... [truncated 18 chars]"
        )

    def test_runtime_workflow_metadata_view_reads_existing_status_fields(self):
        view = RuntimeWorkflowMetadataView.from_metadata(
            {
                "phase": "indexing_batches",
                "progress": "Indexed batch 1/3",
                "checkpoint": {"batch_number": 1, "files_processed": 10},
                "result": {"files_processed": 30},
            }
        )

        assert view.phase == "indexing_batches"
        assert view.progress == "Indexed batch 1/3"
        assert view.checkpoint == {"batch_number": 1, "files_processed": 10}
        assert view.result == {"files_processed": 30}

        queued_view = RuntimeWorkflowMetadataView.from_metadata({"phase": "queued"})
        assert queued_view.phase == "queued"
        assert queued_view.progress == "queued"
        assert queued_view.checkpoint is None
        assert queued_view.result is None

        empty_view = RuntimeWorkflowMetadataView.from_metadata(None)
        assert empty_view.phase is None
        assert empty_view.progress is None
        assert empty_view.checkpoint is None
        assert empty_view.result is None

        with pytest.raises(FrozenInstanceError):
            setattr(view, "metadata", {})

    def test_runtime_job_counts_are_immutable_accumulators(self):
        result = RuntimeJobCounts().with_processed(2).with_failed().add(RuntimeJobCounts(skipped=3))

        assert result.as_dict() == {"processed": 2, "failed": 1, "skipped": 3}

    def test_runtime_storage_event_processing_result_wraps_counts_for_internal_handoffs(self):
        result = (
            RuntimeStorageEventProcessingResult.empty()
            .with_processed(2)
            .with_failed()
            .add(RuntimeStorageEventProcessingResult.from_counts(skipped=3))
        )

        assert result.counts == RuntimeJobCounts(processed=2, failed=1, skipped=3)
        assert result.as_dict() == {"processed": 2, "failed": 1, "skipped": 3}
        assert result.add_counts(RuntimeJobCounts(processed=4)).as_dict() == {
            "processed": 6,
            "failed": 1,
            "skipped": 3,
        }

        with pytest.raises(FrozenInstanceError):
            setattr(result, "counts", RuntimeJobCounts())

    def test_runtime_capabilities_fail_fast_when_factories_are_missing(self):
        capabilities: RuntimeCapabilities[object, object] = RuntimeCapabilities()

        with pytest.raises(RuntimeError, match="Snapshot provider factory"):
            capabilities.require_snapshot_provider_factory()

        with pytest.raises(RuntimeError, match="Note history provider factory"):
            capabilities.require_note_history_provider_factory()

    def test_runtime_capabilities_require_configured_adapters(self):
        empty_capabilities: RuntimeCapabilities[object, object] = RuntimeCapabilities()

        with pytest.raises(RuntimeError, match="Job runtime"):
            empty_capabilities.require_job_runtime()

        with pytest.raises(RuntimeError, match="Storage event source"):
            empty_capabilities.require_storage_event_source()

        job_runtime = FakeJobRuntime()
        storage_event_source = FakeStorageEventSource()
        capabilities = RuntimeCapabilities(
            job_runtime=job_runtime,
            storage_event_source=storage_event_source,
        )

        assert capabilities.require_job_runtime() is job_runtime
        assert capabilities.require_storage_event_source() is storage_event_source

    def test_runtime_file_delete_result_factories_preserve_cleanup_reasons(self):
        assert RuntimeFileDeleteResult.no_accepted_checksum(
            entity_id=1,
            file_path="notes/a.md",
        ) == RuntimeFileDeleteResult(
            entity_id=1,
            file_path="notes/a.md",
            status=RuntimeDeleteStatus.skipped,
            reason="no accepted file checksum for notes/a.md",
        )
        assert RuntimeFileDeleteResult.already_absent(
            entity_id=1,
            file_path="notes/a.md",
        ) == RuntimeFileDeleteResult(
            entity_id=1,
            file_path="notes/a.md",
            status=RuntimeDeleteStatus.missing,
            reason="file already absent: notes/a.md",
        )
        assert RuntimeFileDeleteResult.changed_before_delete(
            entity_id=1,
            file_path="notes/a.md",
        ) == RuntimeFileDeleteResult(
            entity_id=1,
            file_path="notes/a.md",
            status=RuntimeDeleteStatus.skipped,
            reason="file changed before delete: notes/a.md",
        )
        assert RuntimeFileDeleteResult.deleted(
            entity_id=1,
            file_path="notes/a.md",
        ) == RuntimeFileDeleteResult(
            entity_id=1,
            file_path="notes/a.md",
            status=RuntimeDeleteStatus.deleted,
            reason="file deleted: notes/a.md",
        )

    def test_runtime_note_materialization_result_is_a_frozen_outcome(self):
        result = RuntimeNoteMaterializationResult(
            entity_id=42,
            status=RuntimeNoteMaterializationStatus.written,
            reason="note file written: notes/a.md",
            file_path="notes/a.md",
            file_checksum="checksum-1",
        )

        assert result.status.value == "written"
        assert result.file_path == "notes/a.md"
        assert result.file_checksum == "checksum-1"

        with pytest.raises(FrozenInstanceError):
            setattr(result, "reason", "changed")

    @pytest.mark.asyncio
    async def test_runtime_file_checksum_reader_skips_missing_objects(self):
        reader = FakeRuntimeFileChecksumReader(checksum=None)

        checksum = await read_runtime_file_checksum(reader, "notes/a.md")

        assert checksum is None
        assert reader.exists_calls == ["notes/a.md"]
        assert reader.compute_checksum_calls == []

    @pytest.mark.asyncio
    async def test_runtime_file_checksum_reader_returns_existing_checksum(self):
        reader = FakeRuntimeFileChecksumReader(checksum="file-sum")

        checksum = await read_runtime_file_checksum(reader, "notes/a.md")

        assert checksum == "file-sum"
        assert reader.exists_calls == ["notes/a.md"]
        assert reader.compute_checksum_calls == ["notes/a.md"]

    @pytest.mark.asyncio
    async def test_runtime_file_expected_state_accepts_matching_or_missing_objects(self):
        matching_reader = FakeRuntimeFileChecksumReader(checksum="file-sum")
        missing_reader = FakeRuntimeFileChecksumReader(checksum=None)

        await assert_runtime_file_matches_expected(
            matching_reader,
            RuntimeExpectedFileState(
                file_path="notes/a.md",
                expected_checksum="file-sum",
            ),
        )
        await assert_runtime_file_matches_expected(
            missing_reader,
            RuntimeExpectedFileState(
                file_path="notes/a.md",
                expected_checksum="file-sum",
            ),
        )

        assert matching_reader.compute_checksum_calls == ["notes/a.md"]
        assert missing_reader.compute_checksum_calls == []

    @pytest.mark.asyncio
    async def test_runtime_file_expected_state_reports_conflicts(self):
        reader = FakeRuntimeFileChecksumReader(checksum="external-sum")

        with pytest.raises(RuntimeFileConflictError) as exc_info:
            await assert_runtime_file_matches_expected(
                reader,
                RuntimeExpectedFileState(
                    file_path="notes/a.md",
                    expected_checksum="file-sum",
                ),
            )

        assert exc_info.value.file_path == "notes/a.md"
        assert exc_info.value.expected_checksum == "file-sum"
        assert exc_info.value.actual_checksum == "external-sum"
        assert (
            str(exc_info.value) == "Refusing to overwrite unexpected file at notes/a.md: "
            "expected checksum file-sum, found external-sum"
        )

    @pytest.mark.asyncio
    async def test_runtime_file_expected_state_reports_unexpected_first_write(self):
        reader = FakeRuntimeFileChecksumReader(checksum="external-sum")

        with pytest.raises(RuntimeFileConflictError) as exc_info:
            await assert_runtime_file_matches_expected(
                reader,
                RuntimeExpectedFileState(
                    file_path="notes/a.md",
                    expected_checksum=None,
                ),
            )

        assert str(exc_info.value) == (
            "Refusing to overwrite unexpected file at notes/a.md: "
            "expected no existing object, found checksum external-sum"
        )

    def test_note_object_metadata_serializes_storage_metadata(self):
        metadata = RuntimeNoteObjectMetadata(
            entity_id=42,
            db_version=4,
            db_checksum="db-sum",
            actor_user_profile_id=UUID("33333333-3333-3333-3333-333333333333"),
            actor_kind=NOTE_OBJECT_ACTOR_KIND_MCP_CLIENT,
            actor_name="Claude Code",
            source="mcp",
        )

        assert metadata.to_storage_metadata() == {
            NOTE_OBJECT_ENTITY_ID_METADATA: "42",
            NOTE_OBJECT_DB_VERSION_METADATA: "4",
            NOTE_OBJECT_DB_CHECKSUM_METADATA: "db-sum",
            NOTE_OBJECT_FILE_VERSION_METADATA: "4",
            NOTE_OBJECT_FILE_CHECKSUM_METADATA: "db-sum",
            NOTE_OBJECT_ACTOR_USER_PROFILE_ID_METADATA: ("33333333-3333-3333-3333-333333333333"),
            NOTE_OBJECT_ACTOR_KIND_METADATA: NOTE_OBJECT_ACTOR_KIND_MCP_CLIENT,
            NOTE_OBJECT_ACTOR_NAME_METADATA: "Claude Code",
            NOTE_OBJECT_SOURCE_METADATA: "mcp",
        }

    def test_note_object_metadata_parses_safe_values_only(self):
        metadata = {
            NOTE_OBJECT_ACTOR_USER_PROFILE_ID_METADATA: " user-1 ",
            NOTE_OBJECT_ACTOR_KIND_METADATA: NOTE_OBJECT_ACTOR_KIND_MCP_CLIENT,
            NOTE_OBJECT_ACTOR_NAME_METADATA: " Pat\t\n<script>! ",
            NOTE_OBJECT_FILE_CHECKSUM_METADATA: " checksum-1 ",
            NOTE_OBJECT_DB_VERSION_METADATA: " 7 ",
            NOTE_OBJECT_SOURCE_METADATA: "mcp",
        }

        assert actor_user_profile_id_from_object_metadata(metadata) == "user-1"
        assert actor_kind_from_object_metadata(metadata) == NOTE_OBJECT_ACTOR_KIND_MCP_CLIENT
        assert actor_name_from_object_metadata(metadata) == "Pat script"
        assert file_checksum_from_object_metadata(metadata) == "checksum-1"
        assert db_version_from_object_metadata(metadata) == 7
        assert source_from_object_metadata(metadata) == "mcp"

        unsafe_metadata = {
            NOTE_OBJECT_ACTOR_KIND_METADATA: "spoofed",
            NOTE_OBJECT_DB_VERSION_METADATA: "-1",
            NOTE_OBJECT_SOURCE_METADATA: "spoofed",
        }
        assert actor_kind_from_object_metadata(unsafe_metadata) is None
        assert db_version_from_object_metadata(unsafe_metadata) is None
        assert source_from_object_metadata(unsafe_metadata) is None

    def test_normalize_actor_name_strips_unsafe_characters_and_limits_length(self):
        assert normalize_actor_name(" Pat\t\n<script>! ") == "Pat script"
        assert normalize_actor_name("x" * 121) == "x" * 120
        assert normalize_actor_name("!@#$") is None

    def test_pending_note_materialization_carries_cleanup_work(self):
        cleanup = RuntimePendingNoteFileDelete(
            project_id=7,
            entity_id=42,
            file_path="notes/old.md",
            file_checksum="old-checksum",
        )
        materialization = RuntimePendingNoteMaterialization(
            project_id=7,
            entity_id=42,
            db_version=3,
            db_checksum="db-checksum",
            actor_kind="user",
            actor_name="Pat",
            source="mcp",
            cleanup_after_write=cleanup,
        )

        assert materialization.cleanup_after_write == cleanup
        assert materialization.db_checksum == "db-checksum"

        with pytest.raises(FrozenInstanceError):
            setattr(materialization, "db_version", 4)

    def test_runtime_prepared_note_write_carries_materialization_inputs(self):
        attempted_at = datetime(2026, 6, 18, 14, 15)
        metadata = RuntimeNoteObjectMetadata(
            entity_id=42,
            db_version=4,
            db_checksum="db-checksum",
            actor_kind=NOTE_OBJECT_ACTOR_KIND_MCP_CLIENT,
        )

        prepared_write = RuntimePreparedNoteWrite(
            file_path="notes/a.md",
            markdown_content="# A note\n",
            previous_file_checksum="old-file-sum",
            cleanup_file_path="notes/old.md",
            cleanup_file_checksum="old-cleanup-sum",
            attempted_at=attempted_at,
            object_metadata=metadata,
        )

        assert prepared_write.object_metadata == metadata
        assert prepared_write.cleanup_file_path == "notes/old.md"
        with pytest.raises(FrozenInstanceError):
            setattr(prepared_write, "file_path", "notes/b.md")

    def test_runtime_written_file_state_carries_materialized_storage_result(self):
        file_updated_at = datetime(2026, 6, 18, 14, 16)

        written_file = RuntimeWrittenFileState(
            file_path="notes/a.md",
            file_checksum="new-file-sum",
            file_updated_at=file_updated_at,
        )

        assert written_file.file_checksum == "new-file-sum"
        assert written_file.file_updated_at == file_updated_at
        with pytest.raises(FrozenInstanceError):
            setattr(written_file, "file_checksum", "other")

    def test_runtime_accepted_note_change_carries_payload_and_followup_work(self):
        cleanup = RuntimePendingNoteFileDelete(
            project_id=7,
            entity_id=42,
            file_path="notes/old.md",
            file_checksum="old-checksum",
        )
        materialization = RuntimePendingNoteMaterialization(
            project_id=7,
            entity_id=42,
            db_version=3,
            db_checksum="db-checksum",
            cleanup_after_write=cleanup,
        )
        change = RuntimeAcceptedNoteChange[dict[str, object]](
            status_code=202,
            payload={"file_write_status": "pending"},
            materialization=materialization,
        )

        assert change.status_code == 202
        assert change.payload == {"file_write_status": "pending"}
        assert change.materialization == materialization
        assert change.file_delete is None

        with pytest.raises(FrozenInstanceError):
            setattr(change, "status_code", 500)

    def test_plan_previous_note_file_delete_returns_cleanup_for_materialized_moves(self):
        cleanup = plan_previous_note_file_delete(
            project_id=7,
            entity_id=42,
            existing_file_path="notes/old.md",
            accepted_file_path="notes/new.md",
            file_checksum="old-checksum",
        )

        assert cleanup == RuntimePendingNoteFileDelete(
            project_id=7,
            entity_id=42,
            file_path="notes/old.md",
            file_checksum="old-checksum",
        )

    def test_plan_previous_note_file_delete_skips_unmoved_or_unmaterialized_notes(self):
        assert (
            plan_previous_note_file_delete(
                project_id=7,
                entity_id=42,
                existing_file_path=None,
                accepted_file_path="notes/new.md",
                file_checksum="old-checksum",
            )
            is None
        )
        assert (
            plan_previous_note_file_delete(
                project_id=7,
                entity_id=42,
                existing_file_path="notes/same.md",
                accepted_file_path="notes/same.md",
                file_checksum="old-checksum",
            )
            is None
        )
        assert (
            plan_previous_note_file_delete(
                project_id=7,
                entity_id=42,
                existing_file_path="notes/old.md",
                accepted_file_path="notes/new.md",
                file_checksum=None,
            )
            is None
        )

    def test_runtime_project_delete_result_counts_file_outcomes(self):
        result = RuntimeProjectDeleteResult.from_file_results(
            project_id=42,
            project_external_id="project-main",
            status=RuntimeDeleteStatus.deleted,
            deleted_project=True,
            file_results=[
                RuntimeFileDeleteResult(
                    entity_id=1,
                    file_path="notes/a.md",
                    status=RuntimeDeleteStatus.deleted,
                    reason="file deleted",
                ),
                RuntimeFileDeleteResult(
                    entity_id=2,
                    file_path="notes/missing.md",
                    status=RuntimeDeleteStatus.missing,
                    reason="file missing",
                ),
                RuntimeFileDeleteResult(
                    entity_id=3,
                    file_path="notes/skipped.md",
                    status=RuntimeDeleteStatus.skipped,
                    reason="file skipped",
                ),
            ],
            reason="project deleted",
        )

        assert result == RuntimeProjectDeleteResult(
            project_id=42,
            project_external_id="project-main",
            status=RuntimeDeleteStatus.deleted,
            deleted_project=True,
            deleted_files=1,
            skipped_files=1,
            missing_files=1,
            reason="project deleted",
        )
