"""Request-count contracts for semantic ``read_note`` JSON paths."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from httpx import Response

from basic_memory.mcp.note_reads import read_note_json_by_external_id
from basic_memory.schemas.v2 import EntityResponseV2

PROJECT_ID = "11111111-1111-4111-8111-111111111111"
ENTITY_ID = "22222222-2222-4222-8222-222222222222"


def _entity(
    *, content: str | None = "---\ntitle: Request Count\nstatus: ready\n---\nBody\n"
) -> EntityResponseV2:
    now = datetime(2026, 8, 13, tzinfo=UTC)
    return EntityResponseV2(
        external_id=ENTITY_ID,
        id=1,
        title="Request Count",
        note_type="note",
        permalink="notes/request-count",
        file_path="notes/Request Count.md",
        content=content,
        entity_metadata={"title": "Indexed Title", "status": "indexed"},
        created_at=now,
        updated_at=now,
    )


def _patch_project_routing(monkeypatch: pytest.MonkeyPatch, read_note_module: object) -> None:
    @asynccontextmanager
    async def fake_get_project_client(
        project: str | None,
        *,
        context: object | None,
        project_id: str | None,
    ) -> AsyncIterator[tuple[object, SimpleNamespace]]:
        del project, context, project_id
        yield object(), SimpleNamespace(name="main", external_id=PROJECT_ID, home="/tmp")

    async def fake_resolve_project_and_path(
        client: object,
        identifier: str,
        project: str,
        context: object | None,
    ) -> tuple[None, str, None]:
        del client, project, context
        return None, identifier, None

    monkeypatch.setattr(read_note_module, "get_project_client", fake_get_project_client)
    monkeypatch.setattr(read_note_module, "resolve_project_and_path", fake_resolve_project_and_path)
    monkeypatch.setattr(read_note_module, "validate_project_path", lambda *_args: True)


@pytest.mark.asyncio
async def test_exact_uuid_json_reads_entity_once_without_resolve_or_resource(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib

    read_note_module = importlib.import_module("basic_memory.mcp.tools.read_note")
    clients_module = importlib.import_module("basic_memory.mcp.clients")
    _patch_project_routing(monkeypatch, read_note_module)
    calls = {"resolve": 0, "entity": 0, "resource": 0}

    class RecordingKnowledgeClient:
        def __init__(self, client: object, project_id: str) -> None:
            del client
            assert project_id == PROJECT_ID

        async def resolve_entity(self, identifier: str, *, strict: bool = False) -> str:
            del identifier, strict
            calls["resolve"] += 1
            raise AssertionError("an exact external ID must not be resolved")

        async def get_entity(self, entity_id: str) -> EntityResponseV2:
            calls["entity"] += 1
            assert entity_id == ENTITY_ID
            return _entity()

    class RecordingResourceClient:
        def __init__(self, client: object, project_id: str) -> None:
            del client
            assert project_id == PROJECT_ID

        async def read(self, entity_id: str) -> Response:
            del entity_id
            calls["resource"] += 1
            raise AssertionError("accepted entity content must avoid the resource route")

    monkeypatch.setattr(clients_module, "KnowledgeClient", RecordingKnowledgeClient)
    monkeypatch.setattr(clients_module, "ResourceClient", RecordingResourceClient)

    result = await read_note_module.read_note(ENTITY_ID, project="main", output_format="json")

    assert isinstance(result, dict)
    assert result["content"].strip() == "Body"
    assert result["frontmatter"] == {"title": "Request Count", "status": "ready"}
    assert calls == {"resolve": 0, "entity": 1, "resource": 0}


@pytest.mark.asyncio
async def test_permalink_json_resolves_once_then_reads_entity_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib

    read_note_module = importlib.import_module("basic_memory.mcp.tools.read_note")
    clients_module = importlib.import_module("basic_memory.mcp.clients")
    _patch_project_routing(monkeypatch, read_note_module)
    calls = {"resolve": 0, "entity": 0, "resource": 0}

    class RecordingKnowledgeClient:
        def __init__(self, client: object, project_id: str) -> None:
            del client, project_id

        async def resolve_entity(self, identifier: str, *, strict: bool = False) -> str:
            calls["resolve"] += 1
            assert identifier == "notes/request-count"
            assert strict is True
            return ENTITY_ID

        async def get_entity(self, entity_id: str) -> EntityResponseV2:
            calls["entity"] += 1
            assert entity_id == ENTITY_ID
            return _entity()

    class RecordingResourceClient:
        def __init__(self, client: object, project_id: str) -> None:
            del client, project_id

        async def read(self, entity_id: str) -> Response:
            del entity_id
            calls["resource"] += 1
            raise AssertionError("accepted entity content must avoid the resource route")

    monkeypatch.setattr(clients_module, "KnowledgeClient", RecordingKnowledgeClient)
    monkeypatch.setattr(clients_module, "ResourceClient", RecordingResourceClient)

    result = await read_note_module.read_note(
        "notes/request-count",
        project="main",
        output_format="json",
    )

    assert isinstance(result, dict)
    assert result["content"].strip() == "Body"
    assert calls == {"resolve": 1, "entity": 1, "resource": 0}


@pytest.mark.asyncio
async def test_exact_title_json_uses_search_result_external_id_without_second_resolve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib

    read_note_module = importlib.import_module("basic_memory.mcp.tools.read_note")
    clients_module = importlib.import_module("basic_memory.mcp.clients")
    _patch_project_routing(monkeypatch, read_note_module)
    calls = {"resolve": 0, "entity": 0, "resource": 0}

    class RecordingKnowledgeClient:
        def __init__(self, client: object, project_id: str) -> None:
            del client, project_id

        async def resolve_entity(self, identifier: str, *, strict: bool = False) -> str:
            calls["resolve"] += 1
            assert identifier == "Request Count"
            assert strict is True
            raise RuntimeError("force exact-title fallback")

        async def get_entity(self, entity_id: str) -> EntityResponseV2:
            calls["entity"] += 1
            assert entity_id == ENTITY_ID
            return _entity()

    class RecordingResourceClient:
        def __init__(self, client: object, project_id: str) -> None:
            del client, project_id

        async def read(self, entity_id: str) -> Response:
            del entity_id
            calls["resource"] += 1
            raise AssertionError("accepted entity content must avoid the resource route")

    async def fake_search_notes(*, search_type: str, **_kwargs: object) -> dict[str, object]:
        assert search_type == "title"
        return {
            "results": [
                {
                    "title": "Request Count",
                    "external_id": ENTITY_ID,
                    "permalink": "notes/request-count",
                    "file_path": "notes/Request Count.md",
                }
            ],
            "has_more": False,
        }

    monkeypatch.setattr(clients_module, "KnowledgeClient", RecordingKnowledgeClient)
    monkeypatch.setattr(clients_module, "ResourceClient", RecordingResourceClient)
    monkeypatch.setattr(read_note_module, "search_notes", fake_search_notes)

    result = await read_note_module.read_note(
        "Request Count",
        project="main",
        output_format="json",
    )

    assert isinstance(result, dict)
    assert result["content"].strip() == "Body"
    assert calls == {"resolve": 1, "entity": 1, "resource": 0}


@pytest.mark.asyncio
async def test_text_mode_keeps_resolve_then_resource_behavior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib

    read_note_module = importlib.import_module("basic_memory.mcp.tools.read_note")
    clients_module = importlib.import_module("basic_memory.mcp.clients")
    _patch_project_routing(monkeypatch, read_note_module)
    calls = {"resolve": 0, "entity": 0, "resource": 0}

    class RecordingKnowledgeClient:
        def __init__(self, client: object, project_id: str) -> None:
            del client, project_id

        async def resolve_entity(self, identifier: str, *, strict: bool = False) -> str:
            calls["resolve"] += 1
            assert identifier == "notes/request-count"
            assert strict is True
            return ENTITY_ID

        async def get_entity(self, entity_id: str) -> EntityResponseV2:
            del entity_id
            calls["entity"] += 1
            raise AssertionError("text mode must not load the entity response")

    class RecordingResourceClient:
        def __init__(self, client: object, project_id: str) -> None:
            del client, project_id

        async def read(self, entity_id: str) -> Response:
            calls["resource"] += 1
            assert entity_id == ENTITY_ID
            return Response(200, text="raw text-mode Markdown")

    monkeypatch.setattr(clients_module, "KnowledgeClient", RecordingKnowledgeClient)
    monkeypatch.setattr(clients_module, "ResourceClient", RecordingResourceClient)

    result = await read_note_module.read_note("notes/request-count", project="main")

    assert result == "raw text-mode Markdown"
    assert calls == {"resolve": 1, "entity": 0, "resource": 1}


@pytest.mark.asyncio
@pytest.mark.parametrize("accepted_content", ["accepted content", ""])
async def test_exact_id_helper_does_not_read_resource_for_present_content(
    accepted_content: str,
) -> None:
    class EntityReader:
        calls = 0

        async def get_entity(
            self,
            entity_id: str,
            *,
            section: str | None = None,
            lines: str | None = None,
            max_tokens: int | None = None,
        ) -> EntityResponseV2:
            assert section is None and lines is None and max_tokens is None
            self.calls += 1
            assert entity_id == ENTITY_ID
            return _entity(content=accepted_content)

    class ResourceReader:
        calls = 0

        async def read(self, entity_id: str) -> Response:
            del entity_id
            self.calls += 1
            raise AssertionError("present content must not fall back to resource")

    entity_reader = EntityReader()
    resource_reader = ResourceReader()
    result = await read_note_json_by_external_id(
        knowledge_client=entity_reader,
        resource_client=resource_reader,
        entity_external_id=ENTITY_ID,
    )

    assert result["content"] == accepted_content
    assert entity_reader.calls == 1
    assert resource_reader.calls == 0


@pytest.mark.asyncio
async def test_exact_id_helper_reads_resource_once_only_when_content_is_absent() -> None:
    class EntityReader:
        calls = 0

        async def get_entity(
            self,
            entity_id: str,
            *,
            section: str | None = None,
            lines: str | None = None,
            max_tokens: int | None = None,
        ) -> EntityResponseV2:
            assert section is None and lines is None and max_tokens is None
            self.calls += 1
            assert entity_id == ENTITY_ID
            return _entity(content=None)

    class ResourceReader:
        calls = 0

        async def read(self, entity_id: str) -> Response:
            self.calls += 1
            assert entity_id == ENTITY_ID
            return Response(200, text="---\nlegacy: true\n---\nlegacy body\n")

    entity_reader = EntityReader()
    resource_reader = ResourceReader()
    result = await read_note_json_by_external_id(
        knowledge_client=entity_reader,
        resource_client=resource_reader,
        entity_external_id=ENTITY_ID,
    )

    assert result["content"].strip() == "legacy body"
    assert result["frontmatter"] == {"legacy": True}
    assert entity_reader.calls == 1
    assert resource_reader.calls == 1


@pytest.mark.asyncio
async def test_exact_id_helper_does_not_fabricate_frontmatter_from_entity_metadata() -> None:
    class EntityReader:
        async def get_entity(
            self,
            entity_id: str,
            *,
            section: str | None = None,
            lines: str | None = None,
            max_tokens: int | None = None,
        ) -> EntityResponseV2:
            assert section is None and lines is None and max_tokens is None
            assert entity_id == ENTITY_ID
            return _entity(content="plain body\n")

    class ResourceReader:
        async def read(self, entity_id: str) -> Response:
            del entity_id
            raise AssertionError("present content must not fall back to resource")

    result = await read_note_json_by_external_id(
        knowledge_client=EntityReader(),
        resource_client=ResourceReader(),
        entity_external_id=ENTITY_ID,
    )

    assert result["content"] == "plain body\n"
    assert result["frontmatter"] is None
