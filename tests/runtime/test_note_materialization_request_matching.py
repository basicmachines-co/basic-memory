from dataclasses import dataclass
from uuid import UUID

from basic_memory.runtime import (
    RuntimeNoteMaterializationJobRequest,
    note_content_matches_materialization_request,
)


@dataclass(frozen=True, slots=True)
class _NoteContentVersion:
    db_version: object
    db_checksum: object


def _request(*, db_version: int = 4, db_checksum: str = "12345"):
    return RuntimeNoteMaterializationJobRequest(
        tenant_id=UUID("12345678-1234-5678-1234-567812345678"),
        project_id=7,
        entity_id=42,
        db_version=db_version,
        db_checksum=db_checksum,
    )


def test_note_content_matches_materialization_request_with_coerced_runtime_values():
    note_content = _NoteContentVersion(db_version="4", db_checksum=12345)

    assert note_content_matches_materialization_request(note_content, _request())


def test_note_content_matches_materialization_request_rejects_stale_version_or_checksum():
    assert not note_content_matches_materialization_request(
        _NoteContentVersion(db_version=5, db_checksum="db-checksum"),
        _request(db_checksum="db-checksum"),
    )
    assert not note_content_matches_materialization_request(
        _NoteContentVersion(db_version=4, db_checksum="other-checksum"),
        _request(),
    )
