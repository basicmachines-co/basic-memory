"""bm_search across projects: one scoped query per database, merged by score.

The local projects share one database; every cloud workspace is its own. Most tests
here need several databases, which only Cloud can provide, so they replace the
per-database boundary -- project discovery, the per-workspace HTTP client, the scoped
search client and the index-readiness probe -- with a stub that records each call.
The merge, paging, attribution and failure handling between them run for real.
`test_hits_from_two_local_projects_name_their_project` runs the whole path.
"""

import importlib
from contextlib import asynccontextmanager
from typing import Any, Literal
from unittest.mock import AsyncMock

import pytest
from fastmcp.exceptions import ToolError
from httpx import HTTPStatusError, Request, Response
from loguru import logger

from basic_memory.mcp.tools import bm_search, bm_write
from basic_memory.mcp.tools.search import SearchProjectRef
from basic_memory.schemas.search import SearchItemType, SearchResponse, SearchResult

# The package exports the ChatGPT `search` adapter under the module's name.
search_module = importlib.import_module("basic_memory.mcp.tools.search")
clients_module = importlib.import_module("basic_memory.mcp.clients")

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
    file_path: str = "notes/example.md",
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
    """Route each database's scoped search into a stub that records the call.

    ``answer(workspace, project_ids, page, page_size)`` returns the SearchResponse for
    one database, or raises to model a failed database.
    """
    workspaces: list[str | None] = []
    calls: list[dict[str, Any]] = []

    async def load_search_project_refs(context=None):
        return refs

    @asynccontextmanager
    async def get_client(project_name=None, workspace=None):
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

    monkeypatch.setattr(search_module, "_load_search_project_refs", load_search_project_refs)
    monkeypatch.setattr(search_module, "get_client", get_client)
    monkeypatch.setattr(clients_module, "ScopedSearchClient", StubScopedSearchClient)
    monkeypatch.setattr(search_module, "project_index_required", AsyncMock(return_value=None))
    return workspaces, calls


def _page(results: list[SearchResult], page: int, page_size: int, **fields: Any) -> SearchResponse:
    return SearchResponse(
        results=results,
        current_page=page,
        page_size=page_size,
        total=fields.pop("total", len(results)),
        **fields,
    )


async def search_json(**arguments: Any) -> dict[str, Any]:
    result = await bm_search(output_format="json", **arguments)
    assert isinstance(result, dict)
    return result


# --- One query per database ---


@pytest.mark.asyncio
@pytest.mark.parametrize("compact", [False, True])
@pytest.mark.parametrize("observations", [False, True])
async def test_each_workspace_is_one_query_and_results_stay_routable(
    monkeypatch, compact, observations
):
    """Two cloud workspaces are two databases: one scoped call each, merged by score."""

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
                    file_path="tests/Exact Note.md",
                )
            ],
            page,
            page_size,
        )

    workspaces, calls = _install_scoped_search(monkeypatch, [PERSONAL, TEAM], answer)

    result = await search_json(query="MCP Test Note", all_projects=True, compact=compact)

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
    # Each hit's path names the project it lives in, so bm_read can follow it.
    assert [item["file_path"] for item in result["results"]] == [
        "team-paul/main/tests/Exact Note.md",
        "personal/main/tests/Exact Note.md",
    ]
    if compact:
        assert all("content" not in item for item in result["results"])
    assert result["total"] == 2
    assert result["total_is_exact"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("compact_observations", [False, True])
async def test_local_projects_share_one_scoped_query(monkeypatch, compact_observations):
    """Projects in the local database are one query, and every hit names its project."""

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

    result = await search_json(query="anything", all_projects=True, compact=compact_observations)

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
    assert [item["project"] for item in result["results"]] == ["alpha", "beta"]


@pytest.mark.asyncio
async def test_hits_from_two_local_projects_name_their_project(
    client, test_project, second_project
):
    """The whole path, no stubs: two local projects, one database, routable hits."""
    for project in (test_project, second_project):
        await bm_write(
            project=project.name,
            title=f"Fan Out {project.name}",
            directory="notes",
            content=f"# Fan Out {project.name}\nFanOutToken in {project.name}",
        )

    as_json = await search_json(query="FanOutToken", mode="text", all_projects=True)
    as_text = await bm_search(
        query="FanOutToken", mode="text", projects=[test_project.name, second_project.name]
    )

    assert {(item["title"], item["project"]) for item in as_json["results"]} == {
        (f"Fan Out {test_project.name}", test_project.name),
        (f"Fan Out {second_project.name}", second_project.name),
    }
    assert isinstance(as_text, str)
    assert as_text.startswith(
        f"Results for 'FanOutToken' in projects {test_project.name}, {second_project.name}:"
    )
    assert f"- {second_project.permalink}/notes/Fan Out {second_project.name}.md" in as_text


# --- Paging the merge ---


@pytest.mark.asyncio
async def test_each_database_answers_the_whole_prefix_the_page_needs(monkeypatch):
    """Page two of five asks every database for its top ten, then slices the merge."""

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

    result = await search_json(query="notes", all_projects=True, page=2, page_size=5)

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
async def test_full_text_hits_keep_the_server_order_and_page_from_the_strongest(monkeypatch):
    """bm25 scores are negative with lower meaning better; the merge ranks by strength.

    Sorting the raw values put the weakest hit first and made page two repeat page
    one, because every page was cut from the wrong end of the same ranking.
    """

    def answer(workspace, project_ids, page, page_size):
        # The server's own order: strongest (most negative) first.
        ranked = [("strongest", -4.8), ("middle", -4.5), ("weakest", -4.0)]
        return _page(
            [_result(ALPHA, title=title, score=score) for title, score in ranked[:page_size]],
            page,
            page_size,
            total=3,
        )

    _install_scoped_search(monkeypatch, [ALPHA, BETA], answer)

    first = await search_json(query="notes", all_projects=True, page=1, page_size=2)
    second = await search_json(query="notes", all_projects=True, page=2, page_size=2)

    assert [item["title"] for item in first["results"]] == ["strongest", "middle"]
    assert [item["title"] for item in second["results"]] == ["weakest"]


@pytest.mark.asyncio
async def test_no_accessible_projects_is_an_empty_all_projects_answer(monkeypatch):
    """Explicit all-project search must not silently fall back to one project."""
    _workspaces, calls = _install_scoped_search(
        monkeypatch, [], lambda *args: pytest.fail("must not query without projects")
    )

    result = await search_json(query="MCP Test Note", all_projects=True)

    assert result == {
        "results": [],
        "current_page": 1,
        "page_size": 10,
        "total": 0,
        "total_is_exact": True,
        "has_more": False,
    }
    assert calls == []


# --- Failures ---


@pytest.mark.asyncio
async def test_a_failing_database_is_skipped_and_the_total_becomes_inexact(monkeypatch):
    """One failing workspace should not discard the other's results."""

    def answer(workspace, project_ids, page, page_size):
        if workspace == TEAM.workspace_tenant_id:
            raise RuntimeError("team index unavailable")
        return _page(
            [_result(PERSONAL, title="Personal MCP Test Note", score=0.5)], page, page_size
        )

    _install_scoped_search(monkeypatch, [PERSONAL, TEAM], answer)
    warnings: list[str] = []
    sink = logger.add(lambda message: warnings.append(str(message)), level="WARNING")
    try:
        result = await search_json(query="MCP Test Note", all_projects=True)
    finally:
        logger.remove(sink)

    assert [item["permalink"] for item in result["results"]] == ["personal/main/notes/example"]
    assert result["total"] == 1
    assert result["total_is_exact"] is False
    assert any(
        "workspace team-paul" in warning and "team index unavailable" in warning
        for warning in warnings
    )


@pytest.mark.asyncio
async def test_every_database_failing_is_a_failed_search_not_an_empty_one(monkeypatch):
    """With one database, its failure is the whole answer; do not dress it as no matches."""

    def answer(workspace, project_ids, page, page_size):
        raise RuntimeError("local index unavailable")

    _install_scoped_search(monkeypatch, [ALPHA, BETA], answer)

    with pytest.raises(ToolError, match="^UNAVAILABLE: .*local index unavailable"):
        await bm_search(query="MCP Test Note", all_projects=True)


@pytest.mark.asyncio
async def test_a_retryable_service_outage_fails_the_merged_page(monkeypatch):
    """A retryable outage must fail the merged page instead of returning a partial one."""

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

    with pytest.raises(ToolError, match='"error": "UNAVAILABLE"'):
        await bm_search(query="MCP Test Note", all_projects=True, output_format="json")


@pytest.mark.asyncio
@pytest.mark.parametrize("output_format", ["text", "json"])
async def test_an_empty_answer_checks_every_project_in_the_database_for_an_index(
    monkeypatch, output_format: Literal["text", "json"]
):
    """An empty scoped page is only a miss once each project has been indexed."""

    def answer(workspace, project_ids, page, page_size):
        return _page([], page, page_size, total=0)

    _install_scoped_search(monkeypatch, [ALPHA, BETA], answer)
    checked: list[str] = []

    async def readiness(client, project):
        checked.append(project.name)
        return "# Project Index Required\n\nProject 'beta'" if project.name == "beta" else None

    monkeypatch.setattr(search_module, "project_index_required", readiness)

    result = await bm_search(query="missing", all_projects=True, output_format=output_format)

    guidance = result if isinstance(result, str) else result["guidance"]
    assert guidance.startswith("# Project Index Required")
    if isinstance(result, dict):
        assert result["results"] == []
    assert checked == ["alpha", "beta"]


@pytest.mark.asyncio
async def test_a_hit_the_server_does_not_attribute_is_a_version_skew_error(monkeypatch):
    """A result without project identity cannot be qualified; say so instead of guessing."""

    def answer(workspace, project_ids, page, page_size):
        stray = _result(ALPHA, title="stray", score=0.5).model_copy(
            update={"project_external_id": None}
        )
        return _page([stray], page, page_size)

    _install_scoped_search(monkeypatch, [ALPHA, BETA], answer)

    # A version mismatch, not a search miss: an error rather than an empty or partial page.
    with pytest.raises(ValueError, match="did not attribute"):
        await bm_search(query="notes", all_projects=True)


# --- Choosing projects ---


@pytest.mark.asyncio
async def test_projects_selects_a_subset_across_databases(monkeypatch):
    """Naming projects searches only those, grouped by the database each lives in."""

    def answer(workspace, project_ids, page, page_size):
        ref = TEAM if workspace == TEAM.workspace_tenant_id else BETA
        return _page([_result(ref, title=ref.name, score=0.5)], page, page_size)

    _workspaces, calls = _install_scoped_search(monkeypatch, [PERSONAL, TEAM, ALPHA, BETA], answer)

    result = await search_json(query="notes", projects=["beta", TEAM.external_id])

    assert [(call["workspace"], call["project_ids"]) for call in calls] == [
        (None, [BETA.id]),
        (TEAM.workspace_tenant_id, [TEAM.id]),
    ]
    assert {item["title"] for item in result["results"]} == {"beta", "team-paul/main"}


@pytest.mark.asyncio
async def test_an_unknown_project_name_is_refused_before_any_query(monkeypatch):
    _workspaces, calls = _install_scoped_search(
        monkeypatch, [ALPHA, BETA], lambda *args: pytest.fail("must not query")
    )

    with pytest.raises(ValueError, match="Unknown project\\(s\\): gamma"):
        await bm_search(query="notes", projects=["gamma"])

    assert calls == []


def test_project_refs_need_an_id_an_external_id_and_a_name():
    """List rows a scoped search cannot address or attribute are left out."""
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

    refs = search_module._search_project_refs(payload)

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
    assert search_module._search_project_refs({"projects": "nope"}) == []
    assert search_module._search_project_refs(None) == []


def test_project_names_match_by_permalink_like_routing():
    """`ALPHA` names project `alpha` in search scoping, as it does in routing (#1630)."""
    payload = {
        "projects": [
            {"name": "alpha", "external_id": ALPHA.external_id, "id": 3, "path": "/alpha"},
            {"name": "beta", "external_id": BETA.external_id, "id": 4, "path": "/beta"},
        ],
        "constrained_project": "ALPHA",
    }

    refs = search_module._search_project_refs(payload)

    assert [ref.name for ref in refs] == ["alpha"]
    assert search_module._select_project_refs([ALPHA, BETA], ["BETA"]) == [BETA]
    with pytest.raises(ValueError, match="Unknown project\\(s\\): gamma"):
        search_module._select_project_refs([ALPHA, BETA], ["gamma"])


# --- Valid-time filters reach every database (SPEC-82) ---


def _temporal_answer(workspace, project_ids, page, page_size):
    """Each database confirms it applied the valid-time filter."""
    ref = PERSONAL if workspace == PERSONAL.workspace_tenant_id else TEAM
    return _page(
        [
            _result(
                ref,
                title="Cache Layer",
                score=0.5,
                observation=True,
                permalink="main/decisions/cache-layer",
                file_path="main/decisions/cache-layer.md",
            )
        ],
        page,
        page_size,
        temporal_applied=True,
    )


@pytest.mark.asyncio
async def test_every_database_is_asked_the_same_valid_time_question(monkeypatch):
    """A filter dropped on the way to one database would mix filtered and unfiltered rows."""
    _workspaces, calls = _install_scoped_search(monkeypatch, [PERSONAL, TEAM], _temporal_answer)

    result = await search_json(
        query="cache layer", all_projects=True, time_kind="effective", valid_at="2026-07-28"
    )

    payloads = [call["payload"] for call in calls]
    assert len(payloads) == 2
    for payload in payloads:
        assert payload["valid_at"] == "2026-07-28"
        assert payload["time_kind"] == "effective"
        assert payload["valid_overlaps"] is None
    # Every database confirmed it ran the filter, so the merged answer confirms it too.
    assert result["temporal_applied"] is True


@pytest.mark.asyncio
async def test_every_database_is_asked_the_same_overlap_question(monkeypatch):
    _workspaces, calls = _install_scoped_search(monkeypatch, [PERSONAL, TEAM], _temporal_answer)

    await search_json(
        query="cache layer", all_projects=True, valid_overlaps="[2026-06-01,2026-08-01)"
    )

    assert [call["payload"]["valid_overlaps"] for call in calls] == [
        "[2026-06-01,2026-08-01)",
        "[2026-06-01,2026-08-01)",
    ]


@pytest.mark.asyncio
async def test_a_search_without_a_valid_time_filter_claims_none(monkeypatch):
    _install_scoped_search(monkeypatch, [PERSONAL, TEAM], _temporal_answer)

    result = await search_json(query="cache layer", all_projects=True)

    assert "temporal_applied" not in result


@pytest.mark.parametrize(
    ("valid_at", "valid_overlaps", "time_kind", "bad_value"),
    [
        ("2026-13-01", None, None, "2026-13-01"),
        (None, "2026-06-10..2026-07-27", None, "2026-06-10..2026-07-27"),
        (None, None, "asserted", "asserted"),
    ],
)
@pytest.mark.asyncio
async def test_a_malformed_filter_is_refused_before_any_database_is_searched(
    monkeypatch,
    valid_at: str | None,
    valid_overlaps: str | None,
    time_kind: str | None,
    bad_value: str,
):
    """A typo must read as an error, never as an all-projects search with no matches.

    A database that refuses the filter is logged and skipped, so with every database
    skipped the merged answer would be an empty success that still reports
    `temporal_applied`. Client-side validation is the only layer that can tell a typo
    from an unavailable database, so it runs once, before any query.
    """
    _workspaces, calls = _install_scoped_search(monkeypatch, [PERSONAL, TEAM], _temporal_answer)

    with pytest.raises(ToolError, match=f"^INVALID_ARGUMENTS: .*{bad_value}"):
        await bm_search(
            query="cache layer",
            all_projects=True,
            valid_at=valid_at,
            valid_overlaps=valid_overlaps,
            time_kind=time_kind,
        )

    assert calls == []


@pytest.mark.asyncio
async def test_no_projects_is_still_an_answer_to_the_valid_time_question(monkeypatch):
    """Zero projects is an empty answer to the valid-time question, not an unfiltered one."""
    _install_scoped_search(monkeypatch, [], _temporal_answer)

    result = await search_json(query="cache layer", all_projects=True, valid_at="2026-07-28")

    assert result["results"] == []
    assert result["temporal_applied"] is True


# --- Query guidance (#1533) ---


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["empty", "hit", "failed", "later"])
@pytest.mark.parametrize("output_format", ["text", "json"])
async def test_query_hint_only_for_a_complete_empty_first_page(
    monkeypatch, case: str, output_format: Literal["text", "json"]
):
    """The local database is always empty here; the cloud workspace varies by case."""
    one = SearchProjectRef(
        name="one",
        external_id="55555555-5555-5555-5555-555555555555",
        id=1,
        workspace_tenant_id=None,
        path="/one",
    )
    two = SearchProjectRef(
        name="two",
        external_id="66666666-6666-6666-6666-666666666666",
        id=2,
        workspace_tenant_id="tenant-two",
        path="/two",
    )

    def empty(page: int, page_size: int) -> SearchResponse:
        return _page(
            [],
            page,
            page_size,
            total=0,
            total_is_exact=True,
            query_hint="Try a shorter word from your query.",
        )

    def answer(workspace, project_ids, page, page_size):
        if workspace is None or case in ("empty", "later"):
            return empty(page, page_size)
        if case == "failed":
            raise RuntimeError("access denied")
        return _page([_result(two, title="Match", score=1.0)], page, page_size)

    _install_scoped_search(monkeypatch, [one, two], answer)

    result = await bm_search(
        query="雾凇拼音",
        mode="text",
        all_projects=True,
        page=2 if case == "later" else 1,
        output_format=output_format,
    )

    if isinstance(result, str):
        assert ("shorter word" in result) == (case == "empty")
    else:
        assert (result.get("query_hint") is not None) == (case == "empty")
