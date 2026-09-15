"""Optional multi-project search_notes behavior: one scoped query per database."""

import importlib
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastmcp.exceptions import ToolError
from httpx import HTTPStatusError, Request, Response

from basic_memory.mcp.tools.search import SearchProjectRef
from basic_memory.schemas.search import SearchItemType, SearchResponse, SearchResult

PERSONAL = SearchProjectRef(
    name="personal/main",
    external_id="11111111-1111-1111-1111-111111111111",
    id=11,
    workspace_tenant_id="tenant-personal",
    path="/personal/main",
)
TEAM = SearchProjectRef(
    name="team-paul/main",
    external_id="22222222-2222-2222-2222-222222222222",
    id=22,
    workspace_tenant_id="tenant-team",
    path="/team/main",
)
ALPHA = SearchProjectRef(
    name="alpha",
    external_id="33333333-3333-3333-3333-333333333333",
    id=3,
    workspace_tenant_id=None,
    path="/alpha",
)
BETA = SearchProjectRef(
    name="beta",
    external_id="44444444-4444-4444-4444-444444444444",
    id=4,
    workspace_tenant_id=None,
    path="/beta",
)


def _result(
    ref: SearchProjectRef,
    *,
    title: str,
    score: float,
    observation: bool = False,
    permalink: str = "notes/example",
    file_path: str = "/notes/example.md",
) -> SearchResult:
    return SearchResult(
        title=title,
        permalink=permalink,
        content="MCP content",
        type=SearchItemType.OBSERVATION if observation else SearchItemType.ENTITY,
        category="fact" if observation else None,
        score=score,
        file_path=file_path,
        project_id=ref.id,
        project_external_id=ref.external_id,
    )


def _install_scoped_search(
    monkeypatch: pytest.MonkeyPatch,
    refs: list[SearchProjectRef],
    answer,
) -> tuple[list[str | None], list[dict[str, Any]]]:
    """Route the all-projects search into a stub that records each database's call.

    ``answer(workspace, project_ids, page, page_size)`` returns the SearchResponse for
    one database, or raises to model a failed database.
    """
    clients_mod = importlib.import_module("basic_memory.mcp.clients")
    search_mod = importlib.import_module("basic_memory.mcp.tools.search")
    workspaces: list[str | None] = []
    calls: list[dict[str, Any]] = []

    async def fake_load_search_project_refs(context=None):
        return refs

    @asynccontextmanager
    async def fake_get_client(project_name=None, workspace=None):
        workspaces.append(workspace)
        yield {"workspace": workspace}

    class StubScopedSearchClient:
        def __init__(self, client):
            self.workspace = client["workspace"]

        async def search(self, payload, *, project_ids, page, page_size):
            calls.append(
                {
                    "workspace": self.workspace,
                    "project_ids": list(project_ids),
                    "page": page,
                    "page_size": page_size,
                    "payload": payload,
                }
            )
            return answer(self.workspace, list(project_ids), page, page_size)

    monkeypatch.setattr(search_mod, "_load_search_project_refs", fake_load_search_project_refs)
    monkeypatch.setattr(search_mod, "get_client", fake_get_client)
    monkeypatch.setattr(clients_mod, "ScopedSearchClient", StubScopedSearchClient)
    monkeypatch.setattr(search_mod, "project_index_required", AsyncMock(return_value=None))
    return workspaces, calls


def _page(results: list[SearchResult], page: int, page_size: int, **fields: Any) -> SearchResponse:
    return SearchResponse(
        results=results,
        current_page=page,
        page_size=page_size,
        total=fields.pop("total", len(results)),
        **fields,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("compact", [False, True])
@pytest.mark.parametrize("observations", [False, True])
async def test_each_workspace_is_one_query_and_results_stay_routable(
    monkeypatch, compact, observations
):
    """Two cloud workspaces are two databases: one scoped call each, merged by score."""
    search_mod = importlib.import_module("basic_memory.mcp.tools.search")

    def answer(workspace, project_ids, page, page_size):
        ref, title, score = (
            (PERSONAL, "Personal MCP Test Note", 0.5)
            if workspace == PERSONAL.workspace_tenant_id
            else (TEAM, "Team MCP Test Note", 0.9)
        )
        return _page(
            [
                _result(
                    ref,
                    title=title,
                    score=score,
                    observation=observations,
                    permalink="main/tests/mcp-test-note",
                    file_path="tests/Exact Note.md"
                    if observations
                    else "/main/tests/mcp-test-note.md",
                )
            ],
            page,
            page_size,
        )

    workspaces, calls = _install_scoped_search(monkeypatch, [PERSONAL, TEAM], answer)

    result = await search_mod.search_notes(
        query="MCP Test Note",
        search_all_projects=True,
        output_format="json",
        compact=compact,
    )

    assert isinstance(result, dict)
    assert workspaces == [PERSONAL.workspace_tenant_id, TEAM.workspace_tenant_id]
    assert [(call["workspace"], call["project_ids"]) for call in calls] == [
        (PERSONAL.workspace_tenant_id, [PERSONAL.id]),
        (TEAM.workspace_tenant_id, [TEAM.id]),
    ]
    path = "tests/Exact Note.md" if compact and observations else "tests/mcp-test-note"
    assert [item["permalink"] for item in result["results"]] == [
        f"team-paul/main/{path}",
        f"personal/main/{path}",
    ]
    if compact:
        assert all("content" not in item for item in result["results"])
    assert result["total"] == 2
    assert result["total_is_exact"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("compact_observations", [False, True])
async def test_local_projects_share_one_scoped_query(monkeypatch, compact_observations):
    """Projects in the local database are one query, and every hit names its project."""
    search_mod = importlib.import_module("basic_memory.mcp.tools.search")

    def answer(workspace, project_ids, page, page_size):
        return _page(
            [
                _result(
                    ref, title=f"Note in {ref.name}", score=0.5, observation=compact_observations
                )
                for ref in (ALPHA, BETA)
            ],
            page,
            page_size,
        )

    workspaces, calls = _install_scoped_search(monkeypatch, [ALPHA, BETA], answer)

    result = await search_mod.search_notes(
        query="anything",
        search_all_projects=True,
        output_format="json",
        compact=compact_observations,
    )

    assert isinstance(result, dict)
    assert workspaces == [None]
    assert [(call["workspace"], call["project_ids"]) for call in calls] == [
        (None, [ALPHA.id, BETA.id])
    ]
    assert result["total"] == 2
    assert result["total_is_exact"] is True
    if compact_observations:
        assert [item["permalink"] for item in result["results"]] == [
            "alpha/notes/example.md",
            "beta/notes/example.md",
        ]
    else:
        assert [item["permalink"] for item in result["results"]] == [
            "notes/example",
            "notes/example",
        ]
    assert [item["project_external_id"] for item in result["results"]] == [
        ALPHA.external_id,
        BETA.external_id,
    ]


@pytest.mark.asyncio
async def test_each_database_answers_the_whole_prefix_the_page_needs(monkeypatch):
    """Page two of five asks every database for its top ten, then slices the merge."""
    search_mod = importlib.import_module("basic_memory.mcp.tools.search")

    def answer(workspace, project_ids, page, page_size):
        ref = PERSONAL if workspace == PERSONAL.workspace_tenant_id else TEAM
        # Distinct scores across databases so the merge order is unambiguous.
        base = 0.9 if ref is TEAM else 0.85
        return _page(
            [
                _result(ref, title=f"{ref.name} {index}", score=base - index * 0.1)
                for index in range(6)
            ],
            page,
            page_size,
            total=6,
        )

    _workspaces, calls = _install_scoped_search(monkeypatch, [PERSONAL, TEAM], answer)

    result = await search_mod.search_notes(
        query="notes", search_all_projects=True, output_format="json", page=2, page_size=5
    )

    assert isinstance(result, dict)
    assert [(call["page"], call["page_size"]) for call in calls] == [(1, 10), (1, 10)]
    assert [item["title"] for item in result["results"]] == [
        "personal/main 2",
        "team-paul/main 3",
        "personal/main 3",
        "team-paul/main 4",
        "personal/main 4",
    ]
    assert result["current_page"] == 2
    assert result["total"] == 12
    assert result["has_more"] is True


@pytest.mark.asyncio
async def test_multi_project_search_is_opt_in(monkeypatch):
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
    monkeypatch.setattr(search_mod, "project_index_required", AsyncMock(return_value=None))

    result = await search_mod.search_notes(query="MCP Test Note", output_format="json")

    assert isinstance(result, dict)
    assert result["results"] == []
    assert searched_projects == [(None, None)]


@pytest.mark.asyncio
async def test_no_accessible_projects_is_an_empty_all_projects_answer(monkeypatch):
    """Explicit all-project search must not silently fall back to one project."""
    search_mod = importlib.import_module("basic_memory.mcp.tools.search")

    async def fake_load_search_project_refs(context=None):
        return []

    @asynccontextmanager
    async def fail_get_client(*args, **kwargs):
        raise AssertionError("search_all_projects=True should not query without projects")
        yield

    monkeypatch.setattr(search_mod, "_load_search_project_refs", fake_load_search_project_refs)
    monkeypatch.setattr(search_mod, "get_client", fail_get_client)

    result = await search_mod.search_notes(
        query="MCP Test Note",
        search_all_projects=True,
        output_format="json",
    )

    assert result == {
        "results": [],
        "current_page": 1,
        "page_size": 10,
        "total": 0,
        "total_is_exact": True,
        "has_more": False,
    }


@pytest.mark.asyncio
async def test_a_failing_database_is_skipped_and_the_total_becomes_inexact(monkeypatch):
    """One failing workspace should not discard the other's results."""
    search_mod = importlib.import_module("basic_memory.mcp.tools.search")
    warnings: list[str] = []

    class FakeLogger:
        def debug(self, *args, **kwargs):
            pass

        def error(self, *args, **kwargs):
            pass

        def warning(self, message, *args, **kwargs):
            warnings.append(str(message))

    def answer(workspace, project_ids, page, page_size):
        if workspace == TEAM.workspace_tenant_id:
            raise RuntimeError("team index unavailable")
        return _page(
            [_result(PERSONAL, title="Personal MCP Test Note", score=0.5)], page, page_size
        )

    _install_scoped_search(monkeypatch, [PERSONAL, TEAM], answer)
    monkeypatch.setattr(search_mod, "logger", FakeLogger())

    result = await search_mod.search_notes(
        query="MCP Test Note",
        search_all_projects=True,
        output_format="json",
    )

    assert isinstance(result, dict)
    assert [item["permalink"] for item in result["results"]] == ["personal/main/notes/example"]
    assert result["total"] == 1
    assert result["total_is_exact"] is False
    assert any("workspace team-paul" in warning for warning in warnings)
    assert any("team index unavailable" in warning for warning in warnings)


@pytest.mark.asyncio
async def test_every_database_failing_is_a_failed_search_not_an_empty_one(monkeypatch):
    """With one database, its failure is the whole answer; do not dress it as no matches."""
    search_mod = importlib.import_module("basic_memory.mcp.tools.search")

    def answer(workspace, project_ids, page, page_size):
        raise RuntimeError("local index unavailable")

    _install_scoped_search(monkeypatch, [ALPHA, BETA], answer)

    result = await search_mod.search_notes(
        query="MCP Test Note", search_all_projects=True, output_format="json"
    )

    assert isinstance(result, str)
    assert result.startswith("# Search Failed")
    assert "local index unavailable" in result


@pytest.mark.asyncio
async def test_a_retryable_service_outage_fails_the_merged_page(monkeypatch):
    """A retryable outage must fail the merged page instead of returning a partial one."""
    search_mod = importlib.import_module("basic_memory.mcp.tools.search")

    def answer(workspace, project_ids, page, page_size):
        if workspace == TEAM.workspace_tenant_id:
            request = Request("QUERY", "https://api.example/v2/search/")
            response = Response(
                503, request=request, json={"detail": "Reranker temporarily unavailable"}
            )
            try:
                response.raise_for_status()
            except HTTPStatusError as exc:
                raise ToolError("Reranker temporarily unavailable") from exc
        return _page([_result(PERSONAL, title="Personal result", score=0.5)], page, page_size)

    _install_scoped_search(monkeypatch, [PERSONAL, TEAM], answer)

    result = await search_mod.search_notes(
        query="MCP Test Note",
        search_all_projects=True,
        output_format="json",
    )

    assert isinstance(result, str)
    assert result.startswith("# Search Failed - Service Temporarily Unavailable")


@pytest.mark.asyncio
async def test_an_empty_answer_checks_every_project_in_the_database_for_an_index(monkeypatch):
    """An empty scoped page is only a miss once each project has been indexed."""
    search_mod = importlib.import_module("basic_memory.mcp.tools.search")

    def answer(workspace, project_ids, page, page_size):
        return _page([], page, page_size, total=0)

    _install_scoped_search(monkeypatch, [ALPHA, BETA], answer)
    checked: list[str] = []

    async def readiness(client, project):
        checked.append(project.name)
        return "# Project Index Required\n\nProject 'beta'" if project.name == "beta" else None

    monkeypatch.setattr(search_mod, "project_index_required", readiness)

    result = await search_mod.search_notes(
        query="missing", search_all_projects=True, output_format="text"
    )

    assert isinstance(result, str)
    assert result.startswith("# Project Index Required")
    assert checked == ["alpha", "beta"]


@pytest.mark.asyncio
async def test_projects_selects_a_subset_across_databases(monkeypatch):
    """Naming projects searches only those, grouped by the database each lives in."""
    search_mod = importlib.import_module("basic_memory.mcp.tools.search")

    def answer(workspace, project_ids, page, page_size):
        ref = TEAM if workspace == TEAM.workspace_tenant_id else BETA
        return _page([_result(ref, title=ref.name, score=0.5)], page, page_size)

    _workspaces, calls = _install_scoped_search(monkeypatch, [PERSONAL, TEAM, ALPHA, BETA], answer)

    result = await search_mod.search_notes(
        query="notes", projects=["beta", TEAM.external_id], output_format="json"
    )

    assert isinstance(result, dict)
    assert [(call["workspace"], call["project_ids"]) for call in calls] == [
        (None, [BETA.id]),
        (TEAM.workspace_tenant_id, [TEAM.id]),
    ]
    assert {item["title"] for item in result["results"]} == {"beta", "team-paul/main"}


@pytest.mark.asyncio
async def test_an_unknown_project_name_is_refused_before_any_query(monkeypatch):
    search_mod = importlib.import_module("basic_memory.mcp.tools.search")
    _workspaces, calls = _install_scoped_search(
        monkeypatch, [ALPHA, BETA], lambda *args: pytest.fail("must not query")
    )

    with pytest.raises(ValueError, match="Unknown project\\(s\\): gamma"):
        await search_mod.search_notes(query="notes", projects=["gamma"], output_format="json")

    assert calls == []


@pytest.mark.asyncio
async def test_a_hit_the_server_does_not_attribute_is_a_version_skew_error(monkeypatch):
    """A result without project identity cannot be qualified; say so instead of guessing."""
    search_mod = importlib.import_module("basic_memory.mcp.tools.search")

    def answer(workspace, project_ids, page, page_size):
        stray = _result(ALPHA, title="stray", score=0.5).model_copy(
            update={"project_external_id": None}
        )
        return _page([stray], page, page_size)

    _install_scoped_search(monkeypatch, [ALPHA, BETA], answer)

    # Like the valid-time skew above, this is a version mismatch, not a search miss:
    # it is raised as an error rather than returned as an empty or partial page.
    with pytest.raises(ValueError, match="did not attribute"):
        await search_mod.search_notes(query="notes", search_all_projects=True, output_format="json")


def test_project_refs_need_an_id_an_external_id_and_a_name():
    """List rows a scoped search cannot address or attribute are left out."""
    search_mod = importlib.import_module("basic_memory.mcp.tools.search")
    payload = {
        "projects": [
            {"name": "alpha", "external_id": ALPHA.external_id, "id": 3, "path": "/alpha"},
            {"name": "no-id", "external_id": BETA.external_id},
            {"name": "no-external-id", "id": 5},
            {"name": "bool-id", "external_id": PERSONAL.external_id, "id": True},
            {
                "name": "main",
                "qualified_name": "team-paul/main",
                "external_id": TEAM.external_id,
                "id": 22,
                "workspace_tenant_id": "tenant-team",
                "path": "/team/main",
            },
        ],
        "constrained_project": None,
    }

    refs = search_mod._search_project_refs(payload)

    assert refs == [
        SearchProjectRef(
            name="alpha",
            external_id=ALPHA.external_id,
            id=3,
            workspace_tenant_id=None,
            path="/alpha",
        ),
        TEAM,
    ]
    assert search_mod._search_project_refs({"projects": "nope"}) == []
    assert search_mod._search_project_refs(None) == []
