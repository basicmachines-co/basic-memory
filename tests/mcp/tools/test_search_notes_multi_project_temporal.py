"""All-projects search must carry the valid-time filter into every project (SPEC-82).

`_search_all_projects` re-declares the whole filter surface in its own signature and then
calls `search_notes` once per project. A filter that is not repeated there is dropped for
every project at once, and the merged answer would quietly mix filtered and unfiltered
rows -- the worst shape this failure can take, because the result still looks like an
answer.
"""

import importlib
from contextlib import asynccontextmanager
from typing import Any

import pytest

from basic_memory.schemas.search import SearchItemType, SearchResponse, SearchResult

PROJECT_REFS = [
    {"project": "personal/main", "project_id": "11111111-1111-1111-1111-111111111111"},
    {"project": "team-paul/main", "project_id": "22222222-2222-2222-2222-222222222222"},
]


@pytest.fixture
def cloud_routing(monkeypatch):
    """Pin the routing signals so project ids are forwarded deterministically."""
    search_mod = importlib.import_module("basic_memory.mcp.tools.search")
    monkeypatch.setattr(search_mod, "is_factory_mode", lambda: False)
    monkeypatch.setattr(search_mod, "_explicit_routing", lambda: True)
    monkeypatch.setattr(search_mod, "_force_local_mode", lambda: False)
    monkeypatch.setattr(search_mod, "has_cloud_credentials", lambda config: True)


def _install_stub_client(monkeypatch, payloads: list[dict[str, Any]], refs) -> None:
    """Route every per-project search into a stub that records its query payload."""
    clients_mod = importlib.import_module("basic_memory.mcp.clients")
    search_mod = importlib.import_module("basic_memory.mcp.tools.search")

    class StubProject:
        def __init__(self, name: str | None, external_id: str | None):
            self.name = name or "main"
            self.external_id = external_id or "local-main"

    @asynccontextmanager
    async def fake_get_project_client(project=None, context=None, project_id=None):
        yield object(), StubProject(project, project_id)

    async def fake_resolve_project_and_path(client, identifier, project=None, context=None):
        return StubProject(project, None), identifier, False

    async def fake_load_search_project_refs(context=None):
        return refs

    class MockSearchClient:
        def __init__(self, client, project_id):
            self.project_id = project_id

        async def search(self, payload, page, page_size):
            payloads.append(payload)
            return SearchResponse(
                results=[
                    SearchResult(
                        title="Cache Layer",
                        permalink="main/decisions/cache-layer",
                        content="The cache layer will use Memcached.",
                        type=SearchItemType.OBSERVATION,
                        score=0.5,
                        file_path="/main/decisions/cache-layer.md",
                    )
                ],
                current_page=page,
                page_size=page_size,
                total=1,
                temporal_applied=True,
            )

    monkeypatch.setattr(search_mod, "_load_search_project_refs", fake_load_search_project_refs)
    monkeypatch.setattr(search_mod, "get_project_client", fake_get_project_client)
    monkeypatch.setattr(search_mod, "resolve_project_and_path", fake_resolve_project_and_path)
    monkeypatch.setattr(clients_mod, "SearchClient", MockSearchClient)


@pytest.mark.asyncio
async def test_all_projects_search_forwards_the_valid_time_filter(monkeypatch, cloud_routing):
    """Every project is asked the same valid-time question, not just the first."""
    search_mod = importlib.import_module("basic_memory.mcp.tools.search")
    payloads: list[dict[str, Any]] = []
    _install_stub_client(monkeypatch, payloads, PROJECT_REFS)

    result = await search_mod.search_notes(
        query="cache layer",
        search_all_projects=True,
        time_role="effective",
        valid_at="2026-07-28",
        output_format="json",
    )

    assert isinstance(result, dict)
    assert len(payloads) == len(PROJECT_REFS)
    for payload in payloads:
        assert payload["valid_at"] == "2026-07-28"
        assert payload["time_role"] == "effective"
        assert payload["valid_overlaps"] is None
    # Every leg confirmed it ran the filter, so the merged answer confirms it too.
    assert result["temporal_applied"] is True


@pytest.mark.asyncio
async def test_all_projects_search_forwards_an_overlap_filter(monkeypatch, cloud_routing):
    payloads: list[dict[str, Any]] = []
    _install_stub_client(monkeypatch, payloads, PROJECT_REFS)
    search_mod = importlib.import_module("basic_memory.mcp.tools.search")

    await search_mod.search_notes(
        query="cache layer",
        search_all_projects=True,
        valid_overlaps="[2026-06-01,2026-08-01)",
        output_format="json",
    )

    assert [payload["valid_overlaps"] for payload in payloads] == [
        "[2026-06-01,2026-08-01)",
        "[2026-06-01,2026-08-01)",
    ]


@pytest.mark.asyncio
async def test_all_projects_search_without_a_filter_claims_nothing(monkeypatch, cloud_routing):
    """An ordinary all-projects search stays exactly the payload it always was."""
    payloads: list[dict[str, Any]] = []
    _install_stub_client(monkeypatch, payloads, PROJECT_REFS)
    search_mod = importlib.import_module("basic_memory.mcp.tools.search")

    result = await search_mod.search_notes(
        query="cache layer",
        search_all_projects=True,
        output_format="json",
    )

    assert isinstance(result, dict)
    assert "temporal_applied" not in result


@pytest.mark.asyncio
async def test_all_projects_search_with_no_projects_still_confirms_the_filter(
    monkeypatch, cloud_routing
):
    """Zero projects is an empty answer to the valid-time question, not an unfiltered one."""
    payloads: list[dict[str, Any]] = []
    _install_stub_client(monkeypatch, payloads, [])
    search_mod = importlib.import_module("basic_memory.mcp.tools.search")

    result = await search_mod.search_notes(
        query="cache layer",
        search_all_projects=True,
        valid_at="2026-07-28",
        output_format="json",
    )

    assert isinstance(result, dict)
    assert result["results"] == []
    assert result["temporal_applied"] is True
