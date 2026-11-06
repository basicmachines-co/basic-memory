"""Literal and canonical permalink searches through MCP, HTTP and the real index."""

from pathlib import Path

import pytest
import pytest_asyncio
from fastmcp import Client
from httpx import ASGITransport, AsyncClient

from basic_memory.mcp.clients.project import ProjectClient
from basic_memory.workspace_context import workspace_permalink_context


@pytest_asyncio.fixture
async def permalink_projects(client, test_project, tmp_path_factory):
    """Index coexisting identities and identical decoys in another project."""
    projects = ProjectClient(client)
    await projects.create_project(
        {"name": "neighbor", "path": str(tmp_path_factory.mktemp("neighbor")), "set_default": False}
    )
    neighbor = await projects.resolve_project("neighbor")
    permalinks = ["notes/shared", f"{test_project.permalink}/notes/shared", "other/notes/foreign"]
    for project in (test_project, neighbor):
        directory = Path(project.path) / "notes"
        directory.mkdir(exist_ok=True)
        for index, permalink in enumerate(permalinks):
            (directory / f"note-{index}.md").write_text(
                f"---\ntitle: Shared {index}\npermalink: {permalink}\n"
                "type: note\nstatus: active\ntags: [retained]\nembed: false\n---\n\nSearchable content.\n",
                encoding="utf-8",
            )
        await projects.index(project.external_id, run_in_background=False)
    return permalinks


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("query", "matches"),
    [
        ("notes/shared", [0, 1]),
        ("memory://notes/shared", [0, 1]),
        (" notes/shared ", [0, 1]),
        ("notes/shared tag:retained", [0, 1]),
        ("notes/*", [0, 1]),
        ("*shared", [0, 1]),
        ("*", [0, 1, 2]),
        ("other/notes/foreign", [2]),
        ("other/notes/*", [2]),
    ],
)
async def test_mcp_permalink_union_pages(
    mcp_server, client, test_project, permalink_projects, query, matches
):
    """Union before pagination/count, even when the two patterns overlap."""
    expected = {permalink_projects[index] for index in matches}
    async with Client(mcp_server) as mcp:
        pages = []
        for page in range(1, len(expected) + 2):
            result = await mcp.call_tool(
                "bm_search",
                {
                    "project": test_project.name,
                    "query": query,
                    "mode": "permalink",
                    "page": page,
                    "page_size": 1,
                    "output_format": "json",
                },
            )
            payload = result.data
            assert payload["total"] == len(expected)
            assert payload["total_is_exact"] is True
            assert payload["has_more"] is (page < len(expected))
            assert len(payload["results"]) == (1 if page <= len(expected) else 0)
            assert all(row["project_id"] == test_project.id for row in payload["results"])
            pages.extend(row["permalink"] for row in payload["results"])
        repeated = await mcp.call_tool(
            "bm_search",
            {
                "project": test_project.name,
                "query": query,
                "mode": "permalink",
                "page_size": 1,
                "output_format": "json",
            },
        )
    assert set(pages) == expected
    assert len(pages) == len(set(pages))
    assert repeated.data["results"][0]["permalink"] == pages[0]


@pytest.mark.asyncio
async def test_single_segment_permalink_is_exact(
    mcp_server, client, test_project, permalink_projects
):
    """A bare name matches its two identities, not other paths with the same suffix."""
    expected = {"shared", f"{test_project.permalink}/shared"}
    for index, permalink in enumerate(sorted(expected)):
        (Path(test_project.path) / f"single-{index}.md").write_text(
            f"---\ntitle: Single {index}\npermalink: {permalink}\nembed: false\n---\nBody\n",
            encoding="utf-8",
        )
    await ProjectClient(client).index(test_project.external_id, run_in_background=False)
    async with Client(mcp_server) as mcp:
        response = await mcp.call_tool(
            "bm_search",
            {
                "project": test_project.name,
                "query": "shared",
                "mode": "permalink",
                "output_format": "json",
            },
        )
    payload = response.data
    assert {row["permalink"] for row in payload["results"]} == expected
    assert len(payload["results"]) == payload["total"] == 2
    assert payload["total_is_exact"] is True
    assert payload["has_more"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["permalink", "permalink_match"])
async def test_api_permalink_alternatives_and_scalar_controls(
    client, test_project, permalink_projects, field
):
    """HTTP alternatives include both rows, while scalars retain exact path matching."""
    values = permalink_projects[:2]
    if field == "permalink_match":
        values = ["notes/*", f"{test_project.permalink}/notes/*", "*shared"]
    url = f"/v2/projects/{test_project.external_id}/search/"
    for value, expected in [
        (values, set(permalink_projects[:2])),
        ("notes/shared", {"notes/shared"}),
    ]:
        rows = []
        for page in range(1, len(expected) + 2):
            response = await client.request(
                "QUERY",
                url,
                json={field: value, "entity_types": ["entity"]},
                params={"page": page, "page_size": 1},
            )
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["total"] == len(expected)
            assert payload["total_is_exact"] is True
            assert payload["has_more"] is (page < len(expected))
            assert len(payload["results"]) == (1 if page <= len(expected) else 0)
            assert all(row["project_id"] == test_project.id for row in payload["results"])
            rows.extend(row["permalink"] for row in payload["results"])
        assert set(rows) == expected
        assert len(rows) == len(expected)


@pytest.mark.asyncio
@pytest.mark.parametrize("ranking", [{}, {"text": "searchable"}, {"title": "Shared"}])
@pytest.mark.parametrize("exact", [None, "notes/shared", ["notes/shared", "missing"]])
async def test_api_mixed_pattern_alternatives_with_filters(
    client, test_project, permalink_projects, ranking, exact
):
    """A bare FTS term can join path/glob alternatives without invalid MATCH context."""
    query = {
        "permalink_match": ["shared", "other/notes/foreign", "notes/*"],
        "metadata_filters": {"status": "active"},
        "file_path_prefix": "notes",
        "entity_types": ["entity"],
        **ranking,
    }
    if exact is not None:
        query["permalink"] = exact
    # PostgreSQL bare patterns use equality; SQLite bare patterns use FTS.
    # Include the canonical path explicitly so this checks the union on both.
    query["permalink_match"].append(permalink_projects[1])
    expected = {"notes/shared"} if exact is not None else set(permalink_projects)
    response = await client.request(
        "QUERY", f"/v2/projects/{test_project.external_id}/search/", json=query
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert {row["permalink"] for row in payload["results"]} == expected
    assert payload["total"] == len(expected)
    assert payload["total_is_exact"] is True
    assert payload["has_more"] is False
    assert all(row["project_id"] == test_project.id for row in payload["results"])
    if ranking:
        assert all(row["score"] != 0 for row in payload["results"])


@pytest.mark.asyncio
@pytest.mark.parametrize("workspace", [False, True])
@pytest.mark.parametrize("pattern", [False, True])
async def test_qualified_memory_url_keeps_stored_identity(
    mcp_server, client, test_project, permalink_projects, workspace, pattern
):
    """A qualified URL must not widen into the bare row or rewrite a legacy prefix."""
    prefix = "personal/" if workspace else ""
    permalink = f"{prefix}{test_project.permalink}/notes/shared"
    if workspace:
        note = Path(test_project.path) / "notes/workspace.md"
        note.write_text(f"---\ntitle: Workspace\npermalink: {permalink}\nembed: false\n---\nBody\n")
        await ProjectClient(client).index(test_project.external_id, run_in_background=False)
    query = permalink.replace("/shared", "/*") if pattern else permalink
    with workspace_permalink_context(workspace_slug="personal", workspace_type="personal"):
        async with Client(mcp_server) as mcp:
            response = await mcp.call_tool(
                "bm_search",
                {
                    "project": test_project.name,
                    "query": f"memory://{query}",
                    "output_format": "json",
                },
            )
    assert [row["permalink"] for row in response.data["results"]] == [permalink]
    assert response.data["total"] == 1


@pytest.mark.asyncio
async def test_prefix_disabled_keeps_literal_search(
    mcp_server, client, test_project, permalink_projects, app_config, config_manager
):
    """Disabling prefix generation keeps the existing literal-only search contract."""
    app_config.permalinks_include_project = False
    config_manager.save_config(app_config)
    async with Client(mcp_server) as mcp:
        response = await mcp.call_tool(
            "bm_search",
            {
                "project": test_project.name,
                "query": "notes/shared",
                "mode": "permalink",
                "output_format": "json",
            },
        )
    assert [row["permalink"] for row in response.data["results"]] == ["notes/shared"]
    assert response.data["total"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["permalink", "permalink_match"])
@pytest.mark.parametrize("value", [[], [42], [None]])
async def test_api_rejects_invalid_permalink_alternatives(client, test_project, field, value):
    """An alternative list must contain at least one string."""
    response = await client.request(
        "QUERY", f"/v2/projects/{test_project.external_id}/search/", json={field: value}
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_bare_pattern_keeps_backend_matching_and_scalar_ranking(
    client, test_project, permalink_projects, db_backend
):
    """SQLite bare names keep FTS ranking; PostgreSQL keeps equality semantics."""
    url = f"/v2/projects/{test_project.external_id}/search/"
    scalar = await client.request("QUERY", url, json={"permalink_match": "shared"})
    mixed = await client.request(
        "QUERY", url, json={"permalink_match": ["shared", "other/notes/foreign"]}
    )
    assert scalar.status_code == mixed.status_code == 200
    scalar_payload, mixed_payload = scalar.json(), mixed.json()
    if db_backend == "sqlite":
        assert {row["permalink"] for row in scalar_payload["results"]} == set(
            permalink_projects[:2]
        )
        scores = [row["score"] for row in scalar_payload["results"]]
        assert len(scores) == 2
        assert scores[0] < scores[1] < 0
        assert scalar_payload["results"][0]["permalink"] == "notes/shared"
        expected = set(permalink_projects)
    else:
        assert scalar_payload["results"] == []
        expected = {"other/notes/foreign"}
    assert scalar_payload["total"] == len(scalar_payload["results"])
    assert {row["permalink"] for row in mixed_payload["results"]} == expected
    assert mixed_payload["total"] == len(expected)
    assert all(row["score"] == 0 for row in mixed_payload["results"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "filters",
    [
        {"metadata_filters": {"status": "draft"}},
        {"file_path_prefix": "archive"},
        {"entity_types": ["observation"]},
        {"permalink_match": "other/notes/*"},
        {"permalink_match": ["other/notes/*", "absent"]},
    ],
)
async def test_exact_alternatives_intersect_other_filters(
    client, test_project, permalink_projects, filters
):
    """Neither an OR branch nor a shared bind can bypass an independent filter."""
    response = await client.request(
        "QUERY",
        f"/v2/projects/{test_project.external_id}/search/",
        json={"permalink": permalink_projects[:2], **filters},
    )
    assert response.status_code == 200, response.text
    assert response.json()["results"] == []
    assert response.json()["total"] == 0
    assert response.json()["has_more"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("scoped", [False, True])
@pytest.mark.parametrize("field", ["permalink", "permalink_match"])
@pytest.mark.parametrize(
    "alternatives",
    [
        ["notes/shared", ""],
        ["notes/shared", " \t\n"],
        [],
        [f"notes/{index}" for index in range(101)],
        [f"notes/{index}" for index in range(1001)],
    ],
    ids=["empty-element", "whitespace-element", "empty-list", "above-limit", "depth-overflow"],
)
async def test_api_rejects_unsafe_permalink_alternatives(
    app, test_project, permalink_projects, scoped, field, alternatives
):
    """Both public routes reject unsafe lists instead of losing matches or returning 500."""
    url = "/v2/search/" if scoped else f"/v2/projects/{test_project.external_id}/search/"
    scope = {"project_ids": [test_project.id]} if scoped else {}
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    ) as client:
        control = await client.request("QUERY", url, json={field: ["notes/shared"], **scope})
        assert control.status_code == 200, control.text
        assert [row["permalink"] for row in control.json()["results"]] == ["notes/shared"]
        response = await client.request("QUERY", url, json={field: alternatives, **scope})
    assert response.status_code == 422, response.text
    assert any(field in error["loc"] for error in response.json()["detail"])


@pytest.mark.asyncio
@pytest.mark.parametrize("scoped", [False, True])
@pytest.mark.parametrize(
    "fields", [("permalink",), ("permalink_match",), ("permalink", "permalink_match")]
)
@pytest.mark.parametrize("size", [1, 100])
async def test_api_permalink_alternatives_at_accepted_boundaries(
    client, test_project, permalink_projects, scoped, fields, size
):
    """Accepted list limits execute together with ranking and independent predicates."""
    query: dict[str, object] = {
        field: [f"missing{index}" for index in range(size - 1)] + ["notes/shared"]
        for field in fields
    }
    query.update(
        {
            "text": "searchable",
            "title": "Shared",
            "entity_types": ["entity"],
            "note_types": ["note"],
            "file_path_prefix": "notes",
            "after_date": "2000-01-01",
            "tags": ["retained"],
            "status": "active",
            "metadata_filters": {"title": {"$in": ["Shared 0", "Absent"]}},
        }
    )
    url = "/v2/search/" if scoped else f"/v2/projects/{test_project.external_id}/search/"
    if scoped:
        query["project_ids"] = [test_project.id]
    response = await client.request("QUERY", url, json=query)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert [row["permalink"] for row in payload["results"]] == ["notes/shared"]
    assert payload["results"][0]["project_id"] == test_project.id
    assert payload["has_more"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field, expected", [("permalink", []), ("permalink_match", ["notes/shared"])]
)
async def test_api_permalink_alternatives_keep_text_normalization(
    client, test_project, permalink_projects, field, expected
):
    """Exact values stay literal while pattern values retain lowercase/strip behavior."""
    url = f"/v2/projects/{test_project.external_id}/search/"
    for value in [" Notes/Shared ", [" Notes/Shared "]]:
        response = await client.request("QUERY", url, json={field: value})
        assert response.status_code == 200, response.text
        assert [row["permalink"] for row in response.json()["results"]] == expected
