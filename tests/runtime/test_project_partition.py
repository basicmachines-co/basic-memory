"""Portable project partition evidence and propagation tests."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from basic_memory.runtime.cleanup import plan_note_file_delete_job_request
from basic_memory.runtime.note_content_deletes import RuntimePendingNoteFileDelete
from basic_memory.runtime.note_materialization_planning import (
    RuntimePendingNoteMaterialization,
    plan_note_materialization_job_request,
)
from basic_memory.runtime.project_partition import (
    RuntimeAcceptedProjectNoteChange,
    RuntimeProjectNoteOperation,
)


def _project_change(
    operation: RuntimeProjectNoteOperation = RuntimeProjectNoteOperation.updated,
) -> RuntimeAcceptedProjectNoteChange:
    return RuntimeAcceptedProjectNoteChange(
        project_id=7,
        project_external_id="project-123",
        partition_position=4,
        entity_id=42,
        note_external_id="note-123",
        permalink="accepted",
        title="Accepted",
        operation=operation,
        file_path="notes/accepted.md",
        accepted_at=datetime(2026, 8, 29, 12, tzinfo=UTC),
        source="api",
        previous_file_path=(
            "notes/old.md" if operation is RuntimeProjectNoteOperation.moved else None
        ),
        db_version=3,
        db_checksum="db-checksum",
        actor_user_profile_id=UUID("11111111-1111-4111-8111-111111111111"),
        actor_kind="user",
        actor_name="Ada",
    )


def test_project_change_requires_complete_revision_identity() -> None:
    with pytest.raises(ValueError, match="both db_version and db_checksum"):
        RuntimeAcceptedProjectNoteChange(
            project_id=7,
            project_external_id="project-123",
            partition_position=1,
            entity_id=42,
            note_external_id="note-123",
            permalink="accepted",
            title="Accepted",
            operation=RuntimeProjectNoteOperation.updated,
            file_path="notes/accepted.md",
            accepted_at=datetime(2026, 8, 29, 12, tzinfo=UTC),
            source="api",
            db_version=3,
        )


def test_moved_project_change_requires_previous_path() -> None:
    with pytest.raises(ValueError, match="requires previous_file_path"):
        RuntimeAcceptedProjectNoteChange(
            project_id=7,
            project_external_id="project-123",
            partition_position=1,
            entity_id=42,
            note_external_id="note-123",
            permalink="accepted",
            title="Accepted",
            operation=RuntimeProjectNoteOperation.moved,
            file_path="notes/accepted.md",
            accepted_at=datetime(2026, 8, 29, 12, tzinfo=UTC),
            source="api",
        )


def test_project_change_survives_materialization_job_flattening() -> None:
    project_change = _project_change()
    request = plan_note_materialization_job_request(
        RuntimePendingNoteMaterialization(
            project_id=7,
            entity_id=42,
            db_version=3,
            db_checksum="db-checksum",
            project_change=project_change,
            source="api",
        )
    )

    assert request.project_change is project_change


def test_project_change_survives_file_delete_job_flattening() -> None:
    project_change = _project_change(RuntimeProjectNoteOperation.deleted)
    request = plan_note_file_delete_job_request(
        RuntimePendingNoteFileDelete(
            project_id=7,
            entity_id=42,
            file_path="notes/accepted.md",
            file_checksum="file-checksum",
            project_change=project_change,
        )
    )

    assert request.project_change is project_change
