"""Malformed memory-JSON must be rejected before the first file mutation."""

import json
from unittest.mock import AsyncMock, MagicMock

from httpx import AsyncClient
import pytest

from basic_memory.services.file_service import FileService
import logfire


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid",
    [
        [],
        {"name": "private-upload-value"},
        {"type": "unknown", "name": "private-upload-value"},
        {"type": "entity", "name": "private-upload-value", "observations": [7]},
        {"type": "entity", "name": []},
        {"type": "relation", "from": "private-upload-value", "relationType": "uses"},
    ],
)
async def test_malformed_record_rejects_entire_upload_before_writing(
    client: AsyncClient, file_service: FileService, v2_project_url: str, invalid: object
) -> None:
    valid = {"type": "entity", "name": "must-not-be-written", "entityType": "test"}
    upload = "\n".join(json.dumps(record) for record in [valid, invalid])

    response = await client.post(
        f"{v2_project_url}/import/memory-json",
        files={"file": ("memory.json", upload.encode(), "application/json")},
        data={"directory": "rejected-import"},
    )

    assert response.status_code == 400
    assert "record 2" in response.json()["detail"]
    assert "private-upload-value" not in response.text
    assert not await file_service.exists("rejected-import/test/must-not-be-written.md")


@pytest.mark.asyncio
async def test_memory_json_storage_failure_remains_a_server_failure(
    client: AsyncClient, v2_project_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    write = AsyncMock(side_effect=OSError("storage unavailable"))
    monkeypatch.setattr(FileService, "write_file", write)

    response = await client.post(
        f"{v2_project_url}/import/memory-json",
        files={"file": ("memory.json", b'{"type":"entity","name":"Valid"}', "application/json")},
    )

    assert response.status_code == 500
    write.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("upload", "status", "outcome"),
    [
        (b'{"type":"entity","name":"Valid"}', 200, "imported"),
        (b"[]", 400, "input_rejected"),
        (b'{"type":"entity","name":"Valid"}', 500, "failed"),
    ],
)
async def test_import_span_distinguishes_input_rejection_from_operational_failure(
    client: AsyncClient,
    v2_project_url: str,
    monkeypatch: pytest.MonkeyPatch,
    upload: bytes,
    status: int,
    outcome: str,
) -> None:
    captured: list[MagicMock] = []

    def capture_span(name: str, **_attributes: object) -> MagicMock:
        context = MagicMock()
        if name == "import.memory_json":
            captured.append(context.__enter__.return_value)
        return context

    monkeypatch.setattr(logfire, "span", capture_span)
    if status == 500:
        monkeypatch.setattr(
            FileService, "write_file", AsyncMock(side_effect=OSError("storage unavailable"))
        )

    response = await client.post(
        f"{v2_project_url}/import/memory-json",
        files={"file": ("memory.json", upload, "application/json")},
    )

    assert response.status_code == status
    assert len(captured) == 1
    recorded = dict(call.args for call in captured[0].set_attribute.call_args_list)
    for call in captured[0].set_attributes.call_args_list:
        recorded.update(call.args[0])
    assert recorded["outcome"] == outcome
