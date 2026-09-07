"""Agent provenance must survive the queue and object-storage boundaries."""

from basic_memory.runtime.job_payloads import RuntimeNoteMaterializationJobPayload
from basic_memory.runtime.note_content import RuntimeNoteMaterializationJobRequest
from basic_memory.runtime.note_object_metadata import (
    RuntimeNoteObjectMetadata,
    RuntimeNoteObjectProvenance,
)


def test_agent_source_survives_materialization_payload_and_storage_metadata() -> None:
    request = RuntimeNoteMaterializationJobRequest(
        project_id=1,
        entity_id=2,
        db_version=3,
        db_checksum="abc123",
        actor_kind="system",
        actor_name="Preview Agent",
        source="agent_runtime",
    )
    payload = RuntimeNoteMaterializationJobPayload.from_runtime_request(request)
    restored = RuntimeNoteMaterializationJobPayload.model_validate_json(
        payload.model_dump_json()
    ).to_runtime_request()
    assert restored == request
    metadata = RuntimeNoteObjectMetadata(
        entity_id=restored.entity_id,
        db_version=restored.db_version,
        db_checksum=restored.db_checksum,
        actor_kind=restored.actor_kind,
        actor_name=restored.actor_name,
        source=restored.source,
    ).to_storage_metadata()
    provenance = RuntimeNoteObjectProvenance.from_object_metadata(metadata)
    assert provenance.source == "agent_runtime"
    assert provenance.actor_kind == "system"
    assert provenance.actor_name == "Preview Agent"
