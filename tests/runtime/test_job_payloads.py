"""Tests for portable runtime worker payload boundaries."""

from uuid import UUID

from basic_memory.runtime import (
    RuntimeNoteFileDeleteJobPayload,
    RuntimeNoteFileDeleteJobRequest,
)


def test_runtime_note_file_delete_job_payload_round_trips_runtime_request() -> None:
    """The Pydantic worker payload preserves the queue-neutral delete request."""
    runtime_request = RuntimeNoteFileDeleteJobRequest(
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        project_id=101,
        entity_id=42,
        file_path="notes/a.md",
        file_checksum="file-sum",
    )

    payload = RuntimeNoteFileDeleteJobPayload.from_runtime_request(runtime_request)

    assert payload.to_runtime_request() == runtime_request
