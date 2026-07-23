"""Tests for runtime mode resolution."""

from dataclasses import FrozenInstanceError, dataclass, replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest

from basic_memory.runtime.mode import RuntimeMode, resolve_runtime_mode
from basic_memory.runtime.note_content import RuntimeAcceptedNoteChange
from basic_memory.runtime.note_materialization import (
    RuntimePreparedNoteWrite,
    RuntimeWrittenFileState,
    plan_prepared_note_write,
)
from basic_memory.runtime.note_object_metadata import (
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
    RuntimeNoteActorOrigin,
    RuntimeNoteObjectMetadata,
    RuntimeNoteObjectProvenance,
    RuntimeStorageObjectChecksum,
    RuntimeStorageObjectChecksumSource,
    actor_kind_from_object_metadata,
    actor_name_from_object_metadata,
    actor_user_profile_id_from_object_metadata,
    db_version_from_object_metadata,
    file_checksum_from_object_metadata,
    normalize_actor_name,
    source_from_object_metadata,
    storage_object_checksum_for_index_match,
)
from basic_memory.runtime.cleanup import (
    RUNTIME_FILE_SNAPSHOT_TIMESTAMP_MATCH_EPSILON_SECONDS,
    RuntimeDeleteStatus,
    RuntimeDirectoryFileSnapshot,
    RuntimeExternalFileDeleteAction,
    RuntimeExternalFileDeletePlan,
    RuntimeExternalFileDeleteRequest,
    RuntimeFileDeleteResult,
    RuntimeNoteFileDeleteJobRequest,
    RuntimeNoteFileDeletePlan,
    RuntimeProjectDeleteResult,
    RuntimeProjectFileSnapshot,
    plan_directory_file_snapshot,
    plan_note_file_delete_cleanup,
    plan_note_file_delete_job_request,
)
from basic_memory.runtime.jobs import (
    RuntimeCapabilities,
    RuntimeIndexFileBatchJobRequest,
    RuntimeJobRequest,
    RuntimeObservedIndexFile,
    RuntimeProjectDeleteJobRequest,
    RuntimeProjectIndexJobRequest,
    RuntimeStorageFileIndexContext,
    RuntimeStorageFileIndexJobIdentity,
    RuntimeStorageFileIndexMode,
    RuntimeStorageObjectObservation,
    plan_project_index_job_request,
)
from basic_memory.runtime.note_content import (
    NOTE_CONTENT_EXTERNAL_CHANGE_SYNC_ERROR,
    RuntimeAcceptedNoteResponse,
    RuntimeDeletedNoteReference,
    RuntimeExpectedFileState,
    RuntimeFileConflictError,
    RuntimeNoteContentResource,
    RuntimeNoteContentState,
    RuntimeNoteMaterializationJobRequest,
    RuntimeNoteMaterializationResult,
    RuntimeNoteMaterializationStatus,
    RuntimePendingNoteFileDelete,
    RuntimePendingNoteMaterialization,
    assert_runtime_file_matches_expected,
    plan_note_materialization_job_request,
    plan_previous_note_file_delete,
    read_runtime_file_checksum,
)
from basic_memory.runtime.projects import ProjectRuntimeReference
from basic_memory.runtime.storage import (
    RUNTIME_MARKDOWN_CONTENT_TYPE,
    RuntimeJobCounts,
    RuntimeStorageEventOperation,
    RuntimeStorageEventOperationKind,
    RuntimeStorageEventProjectBatch,
    RuntimeStorageEventRoutingPlan,
    RuntimeStorageEventSkipReason,
    RuntimeStorageFileIndexRequest,
    StorageEventPayload,
    StorageObjectIdentity,
    StorageObjectVersion,
    normalize_storage_etag,
    plan_runtime_storage_event_operation,
    plan_runtime_storage_event_operations,
    plan_runtime_storage_events_by_project,
)
from basic_memory.runtime.workflows import (
    RUNTIME_ACTIVE_WORKFLOW_STATUSES,
    RUNTIME_TERMINAL_WORKFLOW_STATUSES,
    RuntimeWorkflowAttemptMetadata,
    RuntimeWorkflowCompletionMetadata,
    RuntimeWorkflowFailureMetadata,
    RuntimeWorkflowMetadataView,
    RuntimeWorkflowProgressMetadata,
    parse_runtime_workflow_id,
    runtime_job_status_from_workflow_status,
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


@dataclass(frozen=True, slots=True)
class FakeDeletedNoteEntity:
    id: int
    external_id: object | None
    title: object | None
    permalink: object | None
    content_type: str = RUNTIME_MARKDOWN_CONTENT_TYPE


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

    def test_normalize_storage_etag_matches_s3_quote_behavior(self):
        assert normalize_storage_etag('"etag-1"') == "etag-1"
        assert normalize_storage_etag("etag-1") == "etag-1"
        assert normalize_storage_etag('""etag-1""') == "etag-1"

    def test_runtime_storage_file_index_mode_names_existing_queue_producers(self):
        assert RuntimeStorageFileIndexMode.observed_object.value == "observed_object"
        assert RuntimeStorageFileIndexMode.current_file.value == "current_file"

    def test_runtime_storage_file_index_job_identity_matches_project_dedupe_keys(self):
        observed_identity = RuntimeStorageFileIndexJobIdentity(
            project_id=42,
            file_path="notes/a.md",
            mode=RuntimeStorageFileIndexMode.observed_object,
            object_etag='"etag-a"',
            object_size=123,
        )
        current_identity = RuntimeStorageFileIndexJobIdentity(
            project_id=42,
            file_path="notes/a.md",
            mode=RuntimeStorageFileIndexMode.current_file,
        )

        assert observed_identity.dedupe_key() == "index-file:42:notes/a.md:observed:etag-a:123"
        assert current_identity.dedupe_key() == "index-file:42:notes/a.md:current"

        with pytest.raises(FrozenInstanceError):
            setattr(observed_identity, "file_path", "notes/b.md")

        missing_observed_metadata = RuntimeStorageFileIndexJobIdentity(
            project_id=42,
            file_path="notes/a.md",
            mode=RuntimeStorageFileIndexMode.observed_object,
        )
        with pytest.raises(ValueError, match="object metadata"):
            missing_observed_metadata.dedupe_key()

    def test_runtime_storage_file_index_job_identity_builds_queue_request(self):
        identity = RuntimeStorageFileIndexJobIdentity(
            project_id=42,
            file_path="notes/a.md",
            mode=RuntimeStorageFileIndexMode.current_file,
        )

        request = identity.job_request(
            entrypoint="index_file",
            payload=b'{"file_path":"notes/a.md"}',
            headers={"source": "test"},
        )

        assert request == RuntimeJobRequest(
            entrypoint="index_file",
            payload=b'{"file_path":"notes/a.md"}',
            dedupe_key="index-file:42:notes/a.md:current",
            headers={
                "source": "test",
                "project_id": "42",
            },
        )

    def test_runtime_storage_object_observation_builds_observed_file_identity(self):
        observation = RuntimeStorageObjectObservation(etag='"etag-a"', size=123)

        identity = observation.to_file_index_job_identity(
            project_id=42,
            file_path="notes/a.md",
        )

        assert identity == RuntimeStorageFileIndexJobIdentity(
            project_id=42,
            file_path="notes/a.md",
            mode=RuntimeStorageFileIndexMode.observed_object,
            object_etag='"etag-a"',
            object_size=123,
        )
        assert identity.dedupe_key() == "index-file:42:notes/a.md:observed:etag-a:123"
        with pytest.raises(FrozenInstanceError):
            setattr(observation, "etag", "other")

    def test_runtime_project_index_job_request_matches_project_queue_identity(self):
        project = ProjectRuntimeReference(
            project_id=42,
            project_external_id="project-main",
            project_name="Main",
            project_permalink="main",
            project_path="main",
        )

        request = plan_project_index_job_request(
            project=project,
            force_full=True,
            search=True,
            embeddings=False,
        )

        assert request == RuntimeProjectIndexJobRequest(
            project=project,
            force_full=True,
            search=True,
            embeddings=False,
        )
        assert request.dedupe_key() == "index-project:42"
        assert request.routing_headers({"source": "test"}) == {
            "source": "test",
            "project_id": "42",
            "project_path": "main",
        }

        with pytest.raises(FrozenInstanceError):
            setattr(request, "force_full", False)

    def test_runtime_project_delete_job_request_matches_project_queue_identity(self):
        request = RuntimeProjectDeleteJobRequest(
            project_id=42,
            project_external_id="project-main",
            project_name="Main",
            project_path="main",
            delete_notes=False,
        )

        assert request.dedupe_key() == "delete-project:42"
        assert request.routing_headers({"source": "test"}) == {
            "source": "test",
            "project_id": "42",
        }

        with pytest.raises(FrozenInstanceError):
            setattr(request, "delete_notes", True)

    def test_runtime_index_file_batch_job_request_carries_observed_targets(self):
        project = ProjectRuntimeReference(
            project_id=42,
            project_external_id="project-main",
            project_path="main",
        )
        observed_file = RuntimeObservedIndexFile(
            path="notes/a.md",
            checksum="etag-a",
            size=123,
        )
        request = RuntimeIndexFileBatchJobRequest(
            project=project,
            batch_index=2,
            batch_count=5,
            file_paths=("notes/a.md",),
            observed_files=(observed_file,),
            index_embeddings=False,
        )

        assert request.dedupe_key() == "index-file-batch:42:2"
        assert request.routing_headers({"source": "test"}) == {
            "source": "test",
            "project_id": "42",
            "project_external_id": "project-main",
            "project_path": "main",
        }
        assert request.target_paths() == ("notes/a.md",)
        assert RuntimeIndexFileBatchJobRequest(
            project=project,
            batch_index=0,
            batch_count=1,
            file_paths=("notes/legacy.md",),
        ).target_paths() == ("notes/legacy.md",)

        with pytest.raises(FrozenInstanceError):
            setattr(observed_file, "path", "notes/b.md")

    def test_runtime_storage_file_index_context_requires_observed_project_context(self):
        RuntimeStorageFileIndexContext(
            mode=RuntimeStorageFileIndexMode.observed_object,
            project_external_id="project-main",
            project_name="Main",
        ).require_enqueue_context()
        RuntimeStorageFileIndexContext(
            mode=RuntimeStorageFileIndexMode.current_file,
        ).require_enqueue_context()

        with pytest.raises(ValueError, match="project_external_id"):
            RuntimeStorageFileIndexContext(
                mode=RuntimeStorageFileIndexMode.observed_object,
                project_name="Main",
            ).require_enqueue_context()
        with pytest.raises(ValueError, match="project_name"):
            RuntimeStorageFileIndexContext(
                mode=RuntimeStorageFileIndexMode.observed_object,
                project_external_id="project-main",
            ).require_enqueue_context()

        context = RuntimeStorageFileIndexContext(
            mode=RuntimeStorageFileIndexMode.observed_object,
            project_external_id="project-main",
            project_name="Main",
        )
        with pytest.raises(FrozenInstanceError):
            setattr(context, "project_name", "Other")

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
        markdown_created_event = event("project/notes/longform.markdown", "OBJECT_CREATED_POST")
        deleted_event = event("project/notes/b.md", "OBJECT_DELETED")
        root_event = event("project/", "OBJECT_CREATED_PUT")
        regular_file_created_event = event("project/image.png", "OBJECT_CREATED_POST")
        unknown_event = event("project/notes/c.md", "OBJECT_RESTORED")

        operations = plan_runtime_storage_event_operations(
            [
                created_event,
                markdown_created_event,
                deleted_event,
                root_event,
                regular_file_created_event,
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
                kind=RuntimeStorageEventOperationKind.index_file,
                storage_event=markdown_created_event,
                relative_path="notes/longform.markdown",
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
                kind=RuntimeStorageEventOperationKind.index_file,
                storage_event=regular_file_created_event,
                relative_path="image.png",
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

        root_operation = next(
            operation for operation in operations if operation.storage_event == root_event
        )
        with pytest.raises(RuntimeError, match="Storage event operation has no relative path"):
            root_operation.require_relative_path()
        with pytest.raises(FrozenInstanceError):
            setattr(operations[0], "kind", RuntimeStorageEventOperationKind.skip)

    def test_runtime_storage_file_index_request_preserves_project_and_object_identity(self):
        project = ProjectRuntimeReference(
            project_id=42,
            project_external_id="project-42",
            project_name="Project 42",
            project_path="project",
        )
        storage_event = StorageEventPayload(
            event_name="OBJECT_CREATED_PUT",
            event_time="2026-06-19T12:00:00Z",
            object_version=StorageObjectVersion(
                identity=StorageObjectIdentity(
                    bucket_name="memory-bucket",
                    key="project/notes/a.md",
                ),
                etag="etag-a",
                size=123,
            ),
        )

        request = RuntimeStorageFileIndexRequest.from_project_event(
            project=project,
            storage_event=storage_event,
        )

        assert request == RuntimeStorageFileIndexRequest(
            project_id=42,
            project_external_id="project-42",
            project_name="Project 42",
            project_path="project",
            file_path="notes/a.md",
            object_etag="etag-a",
            object_size=123,
        )

        with pytest.raises(FrozenInstanceError):
            setattr(request, "file_path", "notes/b.md")

        deleted_event = StorageEventPayload(
            event_name="OBJECT_DELETED",
            event_time="2026-06-19T12:01:00Z",
            object_version=StorageObjectVersion(
                identity=StorageObjectIdentity(
                    bucket_name="memory-bucket",
                    key="project/notes/a.md",
                ),
                etag="etag-a",
            ),
        )
        with pytest.raises(ValueError, match="cannot produce an index request"):
            RuntimeStorageFileIndexRequest.from_project_event(
                project=project,
                storage_event=deleted_event,
            )

    def test_runtime_job_request_is_immutable(self):
        request = RuntimeJobRequest(
            entrypoint="index_file",
            payload=b"{}",
            execute_after=timedelta(seconds=30),
            headers={"project_id": "42"},
        )

        with pytest.raises(FrozenInstanceError):
            setattr(request, "entrypoint", "other")

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
            transport_event_data={"queue_job_id": "job-7"},
        ) == {
            "attempt_number": 2,
            "queue_job_id": "job-7",
            "phase": "running",
            "progress": "loading files",
        }
        assert attempt.attempt_started_event_data(
            attempt_number=1,
            transport_event_data=None,
        ) == {
            "attempt_number": 1,
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

    def test_runtime_workflow_status_helpers_match_job_status_vocabulary(self):
        workflow_id = UUID("22222222-2222-2222-2222-222222222222")

        assert RUNTIME_ACTIVE_WORKFLOW_STATUSES == frozenset({"queued", "running"})
        assert RUNTIME_TERMINAL_WORKFLOW_STATUSES == frozenset({"completed", "failed", "cancelled"})
        assert runtime_job_status_from_workflow_status("queued") == "queued"
        assert runtime_job_status_from_workflow_status("running") == "in_progress"
        assert runtime_job_status_from_workflow_status("completed") == "complete"
        assert runtime_job_status_from_workflow_status("failed") == "failed"
        assert runtime_job_status_from_workflow_status("cancelled") == "cancelled"
        assert runtime_job_status_from_workflow_status("paused") == "unknown"
        assert parse_runtime_workflow_id(str(workflow_id)) == workflow_id
        assert parse_runtime_workflow_id("not-a-workflow-id") is None

    def test_runtime_job_counts_are_immutable_accumulators(self):
        result = (
            RuntimeJobCounts()
            .with_processed(2)
            .with_failed()
            .with_skipped(2)
            .add(RuntimeJobCounts(skipped=1))
        )

        assert result == RuntimeJobCounts(processed=2, failed=1, skipped=3)
        assert result.as_dict() == {"processed": 2, "failed": 1, "skipped": 3}

        with pytest.raises(FrozenInstanceError):
            setattr(result, "processed", 0)

    def test_runtime_deleted_note_reference_validates_live_update_identity(self):
        reference = RuntimeDeletedNoteReference.from_entity(
            FakeDeletedNoteEntity(
                id=1,
                external_id=" note-1 ",
                title=" Deleted note ",
                permalink=" deleted-note ",
            ),
            file_path="notes/deleted.md",
        )

        assert reference == RuntimeDeletedNoteReference(
            external_id="note-1",
            title="Deleted note",
            permalink="deleted-note",
        )

        with pytest.raises(RuntimeError, match="missing title"):
            RuntimeDeletedNoteReference.from_entity(
                FakeDeletedNoteEntity(
                    id=1,
                    external_id="note-1",
                    title="",
                    permalink="deleted-note",
                ),
                file_path="notes/deleted.md",
            )

        # A markdown entity indexed without a permalink still needs a stable
        # live-update identity, so the file path stands in for it.
        fallback_reference = RuntimeDeletedNoteReference.from_entity(
            FakeDeletedNoteEntity(
                id=1,
                external_id="note-1",
                title="Deleted note",
                permalink=None,
            ),
            file_path="notes/deleted.md",
        )

        assert fallback_reference.permalink == "notes/deleted.md"

    def test_runtime_external_file_delete_plan_distinguishes_adapter_work(self):
        entity = FakeDeletedNoteEntity(
            id=7,
            external_id=" note-7 ",
            title=" Deleted note ",
            permalink=" deleted-note ",
        )

        missing_plan = RuntimeExternalFileDeletePlan.missing_entity(file_path="notes/deleted.md")
        assert missing_plan.action == RuntimeExternalFileDeleteAction.missing_entity
        assert missing_plan.entity_id is None
        assert missing_plan.deleted_note is None
        assert missing_plan.should_delete_entity is False
        with pytest.raises(RuntimeError, match="does not delete an entity"):
            missing_plan.require_delete_request()

        stale_plan = RuntimeExternalFileDeletePlan.from_existing_entity(
            entity,
            file_path="notes/deleted.md",
            object_exists=True,
        )
        assert stale_plan.action == RuntimeExternalFileDeleteAction.stale_object
        assert stale_plan.entity_id == 7
        assert stale_plan.deleted_note is None
        assert stale_plan.should_delete_entity is False

        delete_plan = RuntimeExternalFileDeletePlan.from_existing_entity(
            entity,
            file_path="notes/deleted.md",
            object_exists=False,
        )
        assert delete_plan.action == RuntimeExternalFileDeleteAction.delete_entity
        assert delete_plan.should_delete_entity is True
        assert delete_plan.require_delete_request() == RuntimeExternalFileDeleteRequest(
            entity_id=7,
            file_path="notes/deleted.md",
            deleted_note=RuntimeDeletedNoteReference(
                external_id="note-7",
                title="Deleted note",
                permalink="deleted-note",
            ),
        )

    def test_runtime_capabilities_require_configured_adapters(self):
        empty_capabilities = RuntimeCapabilities()

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

    def test_plan_note_file_delete_cleanup_selects_safe_storage_action(self):
        no_guard = plan_note_file_delete_cleanup(
            entity_id=1,
            file_path="notes/a.md",
            accepted_checksum=None,
            actual_checksum=None,
        )
        assert no_guard == RuntimeNoteFileDeletePlan(
            result=RuntimeFileDeleteResult.no_accepted_checksum(
                entity_id=1,
                file_path="notes/a.md",
            ),
            actual_checksum=None,
        )
        assert no_guard.should_delete_file is False

        missing = plan_note_file_delete_cleanup(
            entity_id=1,
            file_path="notes/a.md",
            accepted_checksum="file-sum",
            actual_checksum=None,
        )
        assert missing.result == RuntimeFileDeleteResult.already_absent(
            entity_id=1,
            file_path="notes/a.md",
        )
        assert missing.should_delete_file is False

        changed = plan_note_file_delete_cleanup(
            entity_id=1,
            file_path="notes/a.md",
            accepted_checksum="file-sum",
            actual_checksum="new-file-sum",
        )
        assert changed.result == RuntimeFileDeleteResult.changed_before_delete(
            entity_id=1,
            file_path="notes/a.md",
        )
        assert changed.should_delete_file is False

        matching = plan_note_file_delete_cleanup(
            entity_id=1,
            file_path="notes/a.md",
            accepted_checksum="file-sum",
            actual_checksum="file-sum",
        )
        assert matching.result == RuntimeFileDeleteResult.deleted(
            entity_id=1,
            file_path="notes/a.md",
        )
        assert matching.should_delete_file is True

        with pytest.raises(FrozenInstanceError):
            setattr(matching, "actual_checksum", "other")

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

    def test_note_object_provenance_parses_trusted_actor_and_source(self):
        provenance = RuntimeNoteObjectProvenance.from_object_metadata(
            {
                NOTE_OBJECT_ACTOR_USER_PROFILE_ID_METADATA: " user-1 ",
                NOTE_OBJECT_ACTOR_KIND_METADATA: NOTE_OBJECT_ACTOR_KIND_MCP_CLIENT,
                NOTE_OBJECT_ACTOR_NAME_METADATA: " Pat\t\n<script>! ",
                NOTE_OBJECT_SOURCE_METADATA: "mcp",
                NOTE_OBJECT_DB_VERSION_METADATA: "7",
            }
        )

        assert provenance == RuntimeNoteObjectProvenance(
            actor_user_profile_id="user-1",
            actor_kind=NOTE_OBJECT_ACTOR_KIND_MCP_CLIENT,
            actor_name="Pat script",
            source="mcp",
            db_version=7,
        )

        untrusted_actor_name = RuntimeNoteObjectProvenance.from_object_metadata(
            {
                NOTE_OBJECT_ACTOR_KIND_METADATA: "spoofed",
                NOTE_OBJECT_ACTOR_NAME_METADATA: "Pat",
                NOTE_OBJECT_SOURCE_METADATA: "spoofed",
            }
        )
        assert untrusted_actor_name == RuntimeNoteObjectProvenance(
            actor_user_profile_id=None,
            actor_kind=None,
            actor_name=None,
            source=None,
        )

        with pytest.raises(FrozenInstanceError):
            setattr(provenance, "actor_name", "changed")

    def test_note_actor_origin_uses_mcp_client_labels_only(self):
        assert RuntimeNoteActorOrigin.from_actor_metadata(
            actor_kind=NOTE_OBJECT_ACTOR_KIND_MCP_CLIENT,
            actor_name="Claude Code",
        ) == RuntimeNoteActorOrigin(
            actor_kind=NOTE_OBJECT_ACTOR_KIND_MCP_CLIENT,
            actor_name="Claude Code",
        )
        assert (
            RuntimeNoteActorOrigin.from_actor_metadata(
                actor_kind=NOTE_OBJECT_ACTOR_KIND_MCP_CLIENT,
                actor_name=None,
            )
            is None
        )
        assert (
            RuntimeNoteActorOrigin.from_actor_metadata(
                actor_kind="user",
                actor_name="Pat",
            )
            is None
        )

    def test_storage_object_checksum_for_index_match_prefers_note_file_checksum(self):
        assert storage_object_checksum_for_index_match(
            object_checksum="etag-sum",
            object_metadata={NOTE_OBJECT_FILE_CHECKSUM_METADATA: " file-sum "},
        ) == RuntimeStorageObjectChecksum(
            checksum="file-sum",
            source=RuntimeStorageObjectChecksumSource.note_file_checksum,
        )
        assert storage_object_checksum_for_index_match(
            object_checksum="etag-sum",
            object_metadata={},
        ) == RuntimeStorageObjectChecksum(
            checksum="etag-sum",
            source=RuntimeStorageObjectChecksumSource.storage_etag,
        )

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

    def test_plan_note_materialization_job_request_flattens_pending_work(self):
        actor_user_profile_id = UUID("33333333-3333-3333-3333-333333333333")
        materialization = RuntimePendingNoteMaterialization(
            project_id=7,
            entity_id=42,
            db_version=3,
            db_checksum="db-checksum",
            actor_user_profile_id=actor_user_profile_id,
            actor_kind="mcp_client",
            actor_name="Claude Code",
            source="mcp",
            cleanup_after_write=RuntimePendingNoteFileDelete(
                project_id=7,
                entity_id=42,
                file_path="notes/old.md",
                file_checksum="old-checksum",
            ),
        )

        request = plan_note_materialization_job_request(materialization)

        assert request == RuntimeNoteMaterializationJobRequest(
            project_id=7,
            entity_id=42,
            db_version=3,
            db_checksum="db-checksum",
            actor_user_profile_id=actor_user_profile_id,
            actor_kind="mcp_client",
            actor_name="Claude Code",
            source="mcp",
            cleanup_file_path="notes/old.md",
            cleanup_file_checksum="old-checksum",
        )
        assert request.dedupe_key() == "materialize-note-file:7:42:3:db-checksum"
        assert request.routing_headers({"source": "test"}) == {
            "source": "test",
            "project_id": "7",
        }

        with pytest.raises(FrozenInstanceError):
            setattr(request, "db_version", 4)

    def test_plan_note_file_delete_job_request_flattens_pending_cleanup(self):
        file_delete = RuntimePendingNoteFileDelete(
            project_id=7,
            entity_id=42,
            file_path="notes/old.md",
            file_checksum="old-checksum",
        )

        request = plan_note_file_delete_job_request(file_delete)

        assert request == RuntimeNoteFileDeleteJobRequest(
            project_id=7,
            entity_id=42,
            file_path="notes/old.md",
            file_checksum="old-checksum",
        )
        assert request.dedupe_key() == "delete-note-file:7:42:notes/old.md:old-checksum"
        assert request.routing_headers({"source": "test"}) == {
            "source": "test",
            "project_id": "7",
        }
        assert (
            RuntimeNoteFileDeleteJobRequest(
                project_id=7,
                entity_id=42,
                file_path="notes/old.md",
                file_checksum=None,
            ).dedupe_key()
            == "delete-note-file:7:42:notes/old.md:unknown"
        )

        with pytest.raises(FrozenInstanceError):
            setattr(request, "file_path", "notes/new.md")

    def test_runtime_project_file_snapshot_builds_pending_cleanup(self):
        snapshot = RuntimeProjectFileSnapshot(
            entity_id=42,
            file_path="notes/project.md",
            file_checksum="accepted-checksum",
        )

        assert snapshot.to_pending_note_file_delete(project_id=7) == RuntimePendingNoteFileDelete(
            project_id=7,
            entity_id=42,
            file_path="notes/project.md",
            file_checksum="accepted-checksum",
        )

        with pytest.raises(FrozenInstanceError):
            setattr(snapshot, "file_path", "notes/other.md")

    def test_plan_directory_file_snapshot_prefers_fresh_note_content_guard(self):
        snapshot = plan_directory_file_snapshot(
            entity_id=7,
            file_path="notes/example.md",
            entity_checksum="entity-sha",
            entity_mtime=100.0,
            entity_size=42,
            note_file_checksum="note-sha",
            note_file_updated_at=datetime.fromtimestamp(160.0, tz=UTC),
        )

        assert snapshot == RuntimeDirectoryFileSnapshot(
            entity_id=7,
            file_path="notes/example.md",
            file_checksum="note-sha",
            last_modified_at=160.0,
            size=None,
        )
        assert snapshot.to_pending_note_file_delete(project_id=3) == RuntimePendingNoteFileDelete(
            project_id=3,
            entity_id=7,
            file_path="notes/example.md",
            file_checksum="note-sha",
        )

        with pytest.raises(FrozenInstanceError):
            setattr(snapshot, "size", 99)

    def test_plan_directory_file_snapshot_keeps_size_for_matching_generation(self):
        aligned_timestamp = 200.0 + (RUNTIME_FILE_SNAPSHOT_TIMESTAMP_MATCH_EPSILON_SECONDS / 2)

        snapshot = plan_directory_file_snapshot(
            entity_id=7,
            file_path="notes/example.md",
            entity_checksum="entity-sha",
            entity_mtime=200.0,
            entity_size=42,
            note_file_checksum="note-sha",
            note_file_updated_at=datetime.fromtimestamp(aligned_timestamp, tz=UTC),
        )

        assert snapshot.file_checksum == "note-sha"
        assert snapshot.last_modified_at == aligned_timestamp
        assert snapshot.size == 42

    def test_plan_directory_file_snapshot_falls_back_to_entity_metadata(self):
        snapshot = plan_directory_file_snapshot(
            entity_id=7,
            file_path="notes/example.md",
            entity_checksum="entity-sha",
            entity_mtime=123.0,
            entity_size=99,
            note_file_checksum=None,
            note_file_updated_at=None,
        )

        assert snapshot == RuntimeDirectoryFileSnapshot(
            entity_id=7,
            file_path="notes/example.md",
            file_checksum="entity-sha",
            last_modified_at=123.0,
            size=99,
        )

    def test_plan_directory_file_snapshot_uses_checksum_without_storage_timestamp(self):
        snapshot = plan_directory_file_snapshot(
            entity_id=7,
            file_path="notes/example.md",
            entity_checksum="entity-sha",
            entity_mtime=None,
            entity_size=99,
            note_file_checksum=None,
            note_file_updated_at=None,
        )

        assert snapshot == RuntimeDirectoryFileSnapshot(
            entity_id=7,
            file_path="notes/example.md",
            file_checksum="entity-sha",
            last_modified_at=None,
            size=None,
        )

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

    def test_plan_prepared_note_write_builds_storage_snapshot(self):
        attempted_at = datetime(2026, 6, 18, 14, 17, tzinfo=UTC)
        actor_user_profile_id = UUID("33333333-3333-3333-3333-333333333333")
        request = RuntimeNoteMaterializationJobRequest(
            project_id=7,
            entity_id=42,
            db_version=4,
            db_checksum="db-checksum",
            actor_user_profile_id=actor_user_profile_id,
            actor_kind=NOTE_OBJECT_ACTOR_KIND_MCP_CLIENT,
            actor_name="Claude Code",
            source="mcp",
            cleanup_file_path="notes/old.md",
            cleanup_file_checksum="old-cleanup-sum",
        )

        prepared_write = plan_prepared_note_write(
            request=request,
            file_path="notes/a.md",
            markdown_content="# A note\n",
            previous_file_checksum="old-file-sum",
            attempted_at=attempted_at,
        )

        assert prepared_write == RuntimePreparedNoteWrite(
            file_path="notes/a.md",
            markdown_content="# A note\n",
            previous_file_checksum="old-file-sum",
            cleanup_file_path="notes/old.md",
            cleanup_file_checksum="old-cleanup-sum",
            attempted_at=attempted_at,
            object_metadata=RuntimeNoteObjectMetadata(
                entity_id=42,
                db_version=4,
                db_checksum="db-checksum",
                actor_user_profile_id=actor_user_profile_id,
                actor_kind=NOTE_OBJECT_ACTOR_KIND_MCP_CLIENT,
                actor_name="Claude Code",
                source="mcp",
            ),
        )

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

    def test_runtime_accepted_note_response_serializes_existing_payload_shape(self):
        created_at = datetime(2026, 4, 13, 12, 0, tzinfo=UTC)
        updated_at = datetime(2026, 4, 13, 12, 30, tzinfo=UTC)
        file_updated_at = datetime(2026, 4, 13, 15, 0, tzinfo=UTC)
        entity = SimpleNamespace(
            external_id="entity-1",
            id=42,
            title="Accepted",
            note_type="note",
            content_type="text/markdown",
            permalink="main/accepted",
            file_path="notes/accepted.md",
            entity_metadata={"topic": "tests"},
            created_at=created_at,
            updated_at=updated_at,
            created_by="creator",
            last_updated_by="editor",
        )

        response = RuntimeAcceptedNoteResponse.from_entity(
            entity,
            markdown_content="# Accepted\n",
            db_version=4,
            db_checksum="db-checksum",
            file_version=3,
            file_checksum="file-checksum",
            file_write_status="pending",
            last_source="api",
            file_updated_at=file_updated_at,
            last_materialization_error=None,
        )

        assert response.to_response_payload() == {
            "external_id": "entity-1",
            "id": 42,
            "title": "Accepted",
            "note_type": "note",
            "content_type": "text/markdown",
            "permalink": "main/accepted",
            "file_path": "notes/accepted.md",
            "content": "# Accepted\n",
            "entity_metadata": {"topic": "tests"},
            "observations": [],
            "relations": [],
            "created_at": created_at.isoformat(),
            "updated_at": updated_at.isoformat(),
            "created_by": "creator",
            "last_updated_by": "editor",
            "api_version": "v2",
            "db_version": 4,
            "db_checksum": "db-checksum",
            "file_version": 3,
            "file_checksum": "file-checksum",
            "file_write_status": "pending",
            "last_source": "api",
            "file_updated_at": file_updated_at.isoformat(),
            "last_materialization_error": None,
        }

        with pytest.raises(FrozenInstanceError):
            setattr(response, "file_write_status", "synced")

        nullable_source_response = replace(response, last_source=None)
        assert nullable_source_response.to_response_payload()["last_source"] is None

    def test_runtime_note_content_state_adds_external_change_sync_error(self):
        now = datetime(2026, 4, 13, 12, 0, tzinfo=UTC)
        entity = SimpleNamespace(
            external_id="entity-1",
            id=42,
            title="Accepted",
            note_type="note",
            content_type="text/markdown",
            permalink="main/accepted",
            file_path="notes/accepted.md",
            entity_metadata={"topic": "tests"},
            created_at=now,
            updated_at=now,
            created_by="creator",
            last_updated_by="editor",
        )
        state = RuntimeNoteContentState(
            markdown_content="# Accepted\n",
            db_version=4,
            db_checksum="db-checksum",
            file_version=3,
            file_checksum="file-checksum",
            file_write_status="external_change_detected",
            last_source="s3_webhook",
            file_updated_at=now,
            last_materialization_error="Refusing to overwrite unexpected file",
        )

        response = RuntimeAcceptedNoteResponse.from_entity_and_content_state(
            entity,
            state,
        )

        assert response.to_response_payload()["sync_error"] == (
            NOTE_CONTENT_EXTERNAL_CHANGE_SYNC_ERROR
        )

    def test_runtime_note_content_state_builds_accepted_note_response(self):
        created_at = datetime(2026, 4, 13, 12, 0, tzinfo=UTC)
        updated_at = datetime(2026, 4, 13, 12, 30, tzinfo=UTC)
        file_updated_at = datetime(2026, 4, 13, 15, 0, tzinfo=UTC)
        entity = SimpleNamespace(
            external_id="entity-1",
            id=42,
            title="Accepted",
            note_type="note",
            content_type="text/markdown",
            permalink="main/accepted",
            file_path="notes/accepted.md",
            entity_metadata={"topic": "tests"},
            created_at=created_at,
            updated_at=updated_at,
            created_by="creator",
            last_updated_by="editor",
        )
        note_content = SimpleNamespace(
            markdown_content="# Accepted\n",
            db_version=4,
            db_checksum="db-checksum",
            file_version=3,
            file_checksum="file-checksum",
            file_write_status="synced",
            last_source=None,
            file_updated_at=file_updated_at,
            last_materialization_error=None,
        )

        state = RuntimeNoteContentState.from_source(note_content)
        response = RuntimeAcceptedNoteResponse.from_entity_and_content_state(
            entity,
            state,
        )

        assert state.markdown_content == "# Accepted\n"
        assert state.last_source is None
        assert response.to_response_payload()["content"] == "# Accepted\n"
        assert response.to_response_payload()["last_source"] is None
        assert response.to_response_payload()["file_updated_at"] == file_updated_at.isoformat()

        with pytest.raises(FrozenInstanceError):
            setattr(state, "file_write_status", "pending")

    def test_runtime_note_content_resource_uses_accepted_markdown(self):
        entity = SimpleNamespace(content_type="text/markdown")
        state = RuntimeNoteContentState(
            markdown_content="# Accepted\n",
            db_version=4,
            db_checksum="db-checksum",
            file_version=None,
            file_checksum=None,
            file_write_status="pending",
            last_source="api",
            file_updated_at=None,
            last_materialization_error=None,
        )

        resource = RuntimeNoteContentResource.from_entity_and_content_state(
            entity,
            state,
        )

        assert resource.content == "# Accepted\n"
        assert resource.content_type == "text/markdown"

        with pytest.raises(FrozenInstanceError):
            setattr(resource, "content", "# Other\n")

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
            # Accepted destination rides along for the local case-only-rename
            # skip guard (P0).
            live_file_path="notes/new.md",
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
