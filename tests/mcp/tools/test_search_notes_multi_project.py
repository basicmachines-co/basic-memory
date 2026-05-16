"""Tests for optional multi-project search_notes behavior."""

from contextlib import asynccontextmanager
import importlib

import pytest

from basic_memory.schemas.search import SearchItemType, SearchResponse, SearchResult


def _stub_routing_mode(monkeypatch, *, cloud: bool) -> None:
    """Pin the three cloud-route signals search.py reads.

    `_search_all_projects` only forwards project_id (external UUID) when a
    cloud route is available. The composite mirrors get_project_client:
    factory mode OR explicit --cloud OR has_cloud_credentials. Tests stub
    all three so a dev box with OAuth tokens on disk can't bleed into the
    local-mode case.
    """
    search_mod = importlib.import_module("basic_memory.mcp.tools.search")
    monkeypatch.setattr(search_mod, "is_factory_mode", lambda: False)
    monkeypatch.setattr(search_mod, "_explicit_routing", lambda: cloud)
    monkeypatch.setattr(search_mod, "_force_local_mode", lambda: False)
    monkeypatch.setattr(search_mod, "has_cloud_credentials", lambda config: cloud)


@pytest.fixture
def cloud_routing(monkeypatch):
    """Force the cloud-routing path for multi-project search tests."""
    _stub_routing_mode(monkeypatch, cloud=True)


@pytest.fixture
def local_routing(monkeypatch):
    """Force the local-routing path for multi-project search tests."""
    _stub_routing_mode(monkeypatch, cloud=False)


@pytest.mark.asyncio
async def test_search_notes_search_all_projects_qualifies_result_permalinks(
    monkeypatch, cloud_routing
):
    """Multi-project search belongs to search_notes and keeps result ids routable."""
    clients_mod = importlib.import_module("basic_memory.mcp.clients")
    search_mod = importlib.import_module("basic_memory.mcp.tools.search")

    project_refs = [
        {
            "project": "personal/main",
            "project_id": "11111111-1111-1111-1111-111111111111",
        },
        {
            "project": "team-paul/main",
            "project_id": "22222222-2222-2222-2222-222222222222",
        },
    ]
    searched_projects: list[tuple[str | None, str | None]] = []

    async def fake_load_search_project_refs(context=None):
        return project_refs

    class StubProject:
        def __init__(self, name: str | None, external_id: str | None):
            self.name = name or "main"
            self.external_id = external_id or "local-main"

    @asynccontextmanager
    async def fake_get_project_client(project=None, context=None, project_id=None):
        searched_projects.append((project, project_id))
        yield object(), StubProject(project, project_id)

    async def fake_resolve_project_and_path(client, identifier, project=None, context=None):
        return StubProject(project, None), identifier, False

    class MockSearchClient:
        def __init__(self, client, project_id):
            self.project_id = project_id

        async def search(self, payload, page, page_size):
            if self.project_id == "11111111-1111-1111-1111-111111111111":
                title = "Personal MCP Test Note"
                score = 0.5
            else:
                title = "Team MCP Test Note"
                score = 0.9
            return SearchResponse(
                results=[
                    SearchResult(
                        title=title,
                        permalink="main/tests/mcp-test-note",
                        content="MCP content",
                        type=SearchItemType.ENTITY,
                        score=score,
                        file_path="/main/tests/mcp-test-note.md",
                    )
                ],
                current_page=page,
                page_size=page_size,
                total=1,
            )

    monkeypatch.setattr(search_mod, "_load_search_project_refs", fake_load_search_project_refs)
    monkeypatch.setattr(search_mod, "get_project_client", fake_get_project_client)
    monkeypatch.setattr(search_mod, "resolve_project_and_path", fake_resolve_project_and_path)
    monkeypatch.setattr(clients_mod, "SearchClient", MockSearchClient)

    result = await search_mod.search_notes(
        query="MCP Test Note",
        search_all_projects=True,
        output_format="json",
    )

    assert isinstance(result, dict)
    assert searched_projects == [
        ("personal/main", "11111111-1111-1111-1111-111111111111"),
        ("team-paul/main", "22222222-2222-2222-2222-222222222222"),
    ]
    assert [item["permalink"] for item in result["results"]] == [
        "team-paul/main/tests/mcp-test-note",
        "personal/main/tests/mcp-test-note",
    ]


@pytest.mark.asyncio
async def test_search_notes_multi_project_search_is_opt_in(monkeypatch):
    """Default search_notes calls stay scoped to the resolved project."""
    clients_mod = importlib.import_module("basic_memory.mcp.clients")
    search_mod = importlib.import_module("basic_memory.mcp.tools.search")

    searched_projects: list[tuple[str | None, str | None]] = []

    async def fake_load_search_project_refs(context=None):
        raise AssertionError("project discovery should only run when search_all_projects=True")

    class StubProject:
        name = "main"
        external_id = "local-main"

    @asynccontextmanager
    async def fake_get_project_client(project=None, context=None, project_id=None):
        searched_projects.append((project, project_id))
        yield object(), StubProject()

    async def fake_resolve_project_and_path(client, identifier, project=None, context=None):
        return StubProject(), identifier, False

    class MockSearchClient:
        def __init__(self, *args, **kwargs):
            pass

        async def search(self, payload, page, page_size):
            return SearchResponse(results=[], current_page=page, page_size=page_size)

    monkeypatch.setattr(search_mod, "_load_search_project_refs", fake_load_search_project_refs)
    monkeypatch.setattr(search_mod, "get_project_client", fake_get_project_client)
    monkeypatch.setattr(search_mod, "resolve_project_and_path", fake_resolve_project_and_path)
    monkeypatch.setattr(clients_mod, "SearchClient", MockSearchClient)

    result = await search_mod.search_notes(query="MCP Test Note", output_format="json")

    assert isinstance(result, dict)
    assert result["results"] == []
    assert searched_projects == [(None, None)]


@pytest.mark.asyncio
async def test_search_notes_search_all_projects_with_no_refs_returns_empty_all_projects(
    monkeypatch,
):
    """Explicit all-project search must not silently fall back to one project."""
    search_mod = importlib.import_module("basic_memory.mcp.tools.search")

    async def fake_load_search_project_refs(context=None):
        return []

    @asynccontextmanager
    async def fail_get_project_client(*args, **kwargs):
        raise AssertionError("search_all_projects=True should not fall back to scoped search")
        yield

    monkeypatch.setattr(search_mod, "_load_search_project_refs", fake_load_search_project_refs)
    monkeypatch.setattr(search_mod, "get_project_client", fail_get_project_client)

    result = await search_mod.search_notes(
        query="MCP Test Note",
        search_all_projects=True,
        output_format="json",
    )

    assert isinstance(result, dict)
    assert result == {
        "results": [],
        "current_page": 1,
        "page_size": 10,
        "total": 0,
        "has_more": False,
    }


@pytest.mark.asyncio
async def test_search_notes_search_all_projects_continues_after_project_failure(
    monkeypatch, cloud_routing
):
    """One failing project should not discard successful all-project search results."""
    clients_mod = importlib.import_module("basic_memory.mcp.clients")
    search_mod = importlib.import_module("basic_memory.mcp.tools.search")

    project_refs = [
        {
            "project": "personal/main",
            "project_id": "11111111-1111-1111-1111-111111111111",
        },
        {
            "project": "team-paul/main",
            "project_id": "22222222-2222-2222-2222-222222222222",
        },
    ]
    warnings: list[str] = []

    async def fake_load_search_project_refs(context=None):
        return project_refs

    class StubProject:
        def __init__(self, name: str | None, external_id: str | None):
            self.name = name or "main"
            self.external_id = external_id or "local-main"

    @asynccontextmanager
    async def fake_get_project_client(project=None, context=None, project_id=None):
        yield object(), StubProject(project, project_id)

    async def fake_resolve_project_and_path(client, identifier, project=None, context=None):
        return StubProject(project, None), identifier, False

    class FakeLogger:
        def debug(self, *args, **kwargs):
            pass

        def error(self, *args, **kwargs):
            pass

        def warning(self, message, *args, **kwargs):
            warnings.append(str(message))

    class MockSearchClient:
        def __init__(self, client, project_id):
            self.project_id = project_id

        async def search(self, payload, page, page_size):
            if self.project_id == "22222222-2222-2222-2222-222222222222":
                raise RuntimeError("team index unavailable")
            return SearchResponse(
                results=[
                    SearchResult(
                        title="Personal MCP Test Note",
                        permalink="main/tests/mcp-test-note",
                        content="MCP content",
                        type=SearchItemType.ENTITY,
                        score=0.5,
                        file_path="/main/tests/mcp-test-note.md",
                    )
                ],
                current_page=page,
                page_size=page_size,
                total=1,
            )

    monkeypatch.setattr(search_mod, "_load_search_project_refs", fake_load_search_project_refs)
    monkeypatch.setattr(search_mod, "get_project_client", fake_get_project_client)
    monkeypatch.setattr(search_mod, "resolve_project_and_path", fake_resolve_project_and_path)
    monkeypatch.setattr(search_mod, "logger", FakeLogger())
    monkeypatch.setattr(clients_mod, "SearchClient", MockSearchClient)

    result = await search_mod.search_notes(
        query="MCP Test Note",
        search_all_projects=True,
        output_format="json",
    )

    assert isinstance(result, dict)
    assert [item["permalink"] for item in result["results"]] == [
        "personal/main/tests/mcp-test-note",
    ]
    assert result["total"] == 1
    assert any("team-paul/main" in warning for warning in warnings)
    assert any("team index unavailable" in warning for warning in warnings)


@pytest.mark.asyncio
async def test_search_notes_search_all_projects_local_omits_project_id(
    monkeypatch, local_routing
):
    """Without a cloud route, fan-out must address each project by name only.

    project_id (external UUID) routes through the cloud v2 API path, which
    returns 401 on local installs because there's no JWT to present. Local
    fan-out has to fall back to the name-routed path so each per-project
    search actually returns results instead of silently failing.
    """
    clients_mod = importlib.import_module("basic_memory.mcp.clients")
    search_mod = importlib.import_module("basic_memory.mcp.tools.search")

    project_refs = [
        {
            "project": "alpha",
            "project_id": "11111111-1111-1111-1111-111111111111",
        },
        {
            "project": "beta",
            "project_id": "22222222-2222-2222-2222-222222222222",
        },
    ]
    searched_projects: list[tuple[str | None, str | None]] = []

    async def fake_load_search_project_refs(context=None):
        return project_refs

    class StubProject:
        def __init__(self, name: str | None, external_id: str | None):
            self.name = name or "main"
            self.external_id = external_id or "local-main"

    @asynccontextmanager
    async def fake_get_project_client(project=None, context=None, project_id=None):
        searched_projects.append((project, project_id))
        yield object(), StubProject(project, project_id)

    async def fake_resolve_project_and_path(client, identifier, project=None, context=None):
        return StubProject(project, None), identifier, False

    class MockSearchClient:
        def __init__(self, client, project_id):
            self.project_id = project_id

        async def search(self, payload, page, page_size):
            return SearchResponse(
                results=[
                    SearchResult(
                        title=f"Note in {self.project_id or 'local'}",
                        permalink="notes/example",
                        content="",
                        type=SearchItemType.ENTITY,
                        score=0.5,
                        file_path="/notes/example.md",
                    )
                ],
                current_page=page,
                page_size=page_size,
                total=1,
            )

    monkeypatch.setattr(search_mod, "_load_search_project_refs", fake_load_search_project_refs)
    monkeypatch.setattr(search_mod, "get_project_client", fake_get_project_client)
    monkeypatch.setattr(search_mod, "resolve_project_and_path", fake_resolve_project_and_path)
    monkeypatch.setattr(clients_mod, "SearchClient", MockSearchClient)

    result = await search_mod.search_notes(
        query="anything",
        search_all_projects=True,
        output_format="json",
    )

    assert isinstance(result, dict)
    assert searched_projects == [("alpha", None), ("beta", None)], (
        "Local fan-out must omit project_id so the recursive search_notes calls "
        "take the name-routed path."
    )
    assert result["total"] == 2
