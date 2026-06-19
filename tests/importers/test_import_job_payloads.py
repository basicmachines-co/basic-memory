from dataclasses import dataclass
from uuid import uuid4

import pytest
from pydantic import ValidationError

from basic_memory.importers import ImportDataPayload, ImportKind


@dataclass(frozen=True, slots=True)
class ProjectSource:
    id: int
    external_id: str | None
    path: str | None
    name: str | None = None
    permalink: str | None = None


def test_import_data_payload_from_project_preserves_workflow_metadata() -> None:
    tenant_id = uuid4()
    workflow_id = uuid4()

    payload = ImportDataPayload.from_project(
        tenant_id=tenant_id,
        import_type="project-zip",
        s3_input_key="tenants/test/jobs/import/input.zip",
        destination_folder="docs",
        project=ProjectSource(
            id=101,
            external_id="project-ext",
            path="main",
            name="Main",
            permalink="main",
        ),
        workflow_id=workflow_id,
    )

    assert payload.project_reference().workflow_metadata() == {
        "project_id": 101,
        "project_external_id": "project-ext",
        "project_name": "Main",
        "project_permalink": "main",
        "project_path": "main",
    }
    assert payload.workflow_payload_metadata() == {
        "tenant_id": str(tenant_id),
        "import_type": "project-zip",
        "s3_input_key": "tenants/test/jobs/import/input.zip",
        "destination_folder": "docs",
        "project_id": 101,
        "project_name": "Main",
        "project_permalink": "main",
        "project_external_id": "project-ext",
        "project_path": "main",
    }


def test_import_data_payload_routing_headers_preserve_existing_queue_shape() -> None:
    workflow_id = uuid4()
    payload = ImportDataPayload(
        tenant_id=uuid4(),
        import_type="chatgpt",
        s3_input_key="tenants/test/jobs/import/conversations.json",
        destination_folder="imports",
        project_id=101,
        project_external_id="project-ext",
        project_path="main",
        workflow_id=workflow_id,
    )

    assert payload.routing_headers({"source": "test"}) == {
        "source": "test",
        "tenant_id": str(payload.tenant_id),
        "project_id": "101",
        "project_path": "main",
        "workflow_id": str(workflow_id),
    }


def test_import_data_payload_rejects_unknown_import_kind() -> None:
    with pytest.raises(ValidationError):
        ImportDataPayload.model_validate(
            {
                "tenant_id": uuid4(),
                "import_type": "unknown",
                "s3_input_key": "tenants/test/jobs/import/input.zip",
                "destination_folder": "",
                "project_id": 101,
                "project_external_id": "project-ext",
                "project_path": "main",
                "workflow_id": uuid4(),
            }
        )


def test_import_kind_type_exports_current_values() -> None:
    import_kind: ImportKind = "memory-json"

    assert import_kind == "memory-json"
