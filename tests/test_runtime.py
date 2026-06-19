"""Tests for runtime mode resolution."""

from dataclasses import FrozenInstanceError
from datetime import timedelta
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
    RuntimeFileDeleteResult,
    RuntimeJobCounts,
    RuntimeJobRequest,
    RuntimeNoteMaterializationResult,
    RuntimeNoteMaterializationStatus,
    RuntimePendingNoteFileDelete,
    RuntimePendingNoteMaterialization,
    RuntimeProjectDeleteResult,
    StorageObjectIdentity,
    plan_previous_note_file_delete,
)


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

    def test_runtime_job_request_is_immutable(self):
        request = RuntimeJobRequest(
            entrypoint="index_file",
            payload=b"{}",
            execute_after=timedelta(seconds=30),
            headers={"tenant_id": "tenant-1"},
        )

        with pytest.raises(FrozenInstanceError):
            setattr(request, "entrypoint", "other")

    def test_runtime_job_counts_are_immutable_accumulators(self):
        result = RuntimeJobCounts().with_processed(2).with_failed().add(RuntimeJobCounts(skipped=3))

        assert result.as_dict() == {"processed": 2, "failed": 1, "skipped": 3}

    def test_runtime_capabilities_fail_fast_when_factories_are_missing(self):
        capabilities: RuntimeCapabilities[object, object] = RuntimeCapabilities()

        with pytest.raises(RuntimeError, match="Snapshot provider factory"):
            capabilities.require_snapshot_provider_factory()

        with pytest.raises(RuntimeError, match="Note history provider factory"):
            capabilities.require_note_history_provider_factory()

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
