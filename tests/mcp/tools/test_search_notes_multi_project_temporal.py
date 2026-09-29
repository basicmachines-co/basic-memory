"""All-projects search must carry the valid-time filter into every database (SPEC-82).

`_search_all_projects` re-declares the whole filter surface in its own signature and then
runs one scoped query per database. A filter that is not repeated there is dropped for
every project at once, and the merged answer would quietly mix filtered and unfiltered
rows -- the worst shape this failure can take, because the result still looks like an
answer.
"""

import importlib
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock

import pytest

from basic_memory.mcp.tools.search import SearchProjectRef
from basic_memory.schemas.search import SearchItemType, SearchResponse, SearchResult

PROJECT_REFS = [
    SearchProjectRef(
        name="personal/main",
        external_id="11111111-1111-1111-1111-111111111111",
        id=11,
        workspace_tenant_id="tenant-personal",
        path="/personal/main",
    ),
    SearchProjectRef(
        name="team-paul/main",
        external_id="22222222-2222-2222-2222-222222222222",
        id=22,
        workspace_tenant_id="tenant-team",
        path="/team/main",
    ),
]


def _install_stub_client(monkeypatch, payloads: list[dict[str, Any]], refs) -> None:
    """Route every database's scoped search into a stub that records its query payload."""
    clients_mod = importlib.import_module("basic_memory.mcp.clients")
    search_mod = importlib.import_module("basic_memory.mcp.tools.search")
    refs_by_tenant = {ref.workspace_tenant_id: ref for ref in refs}

    @asynccontextmanager
    async def fake_get_client(project_name=None, workspace=None):
        yield {"workspace": workspace}

    async def fake_load_search_project_refs(context=None):
        return refs

    class StubScopedSearchClient:
        def __init__(self, client):
            self.ref = refs_by_tenant[client["workspace"]]

        async def search(self, payload, *, project_ids, page, page_size):
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
                        project_id=self.ref.id,
                        project_external_id=self.ref.external_id,
                    )
                ],
                current_page=page,
                page_size=page_size,
                total=1,
                temporal_applied=True,
            )

    monkeypatch.setattr(search_mod, "_load_search_project_refs", fake_load_search_project_refs)
    monkeypatch.setattr(search_mod, "get_client", fake_get_client)
    monkeypatch.setattr(clients_mod, "ScopedSearchClient", StubScopedSearchClient)
    monkeypatch.setattr(search_mod, "project_index_required", AsyncMock(return_value=None))


@pytest.mark.asyncio
async def test_all_projects_search_forwards_the_valid_time_filter(monkeypatch):
    """Every database is asked the same valid-time question, not just the first."""
    search_mod = importlib.import_module("basic_memory.mcp.tools.search")
    payloads: list[dict[str, Any]] = []
    _install_stub_client(monkeypatch, payloads, PROJECT_REFS)

    result = await search_mod.search_notes(
        query="cache layer",
        search_all_projects=True,
        time_kind="effective",
        valid_at="2026-07-28",
        output_format="json",
    )

    assert isinstance(result, dict)
    assert len(payloads) == len(PROJECT_REFS)
    for payload in payloads:
        assert payload["valid_at"] == "2026-07-28"
        assert payload["time_kind"] == "effective"
        assert payload["valid_overlaps"] is None
    # Every database confirmed it ran the filter, so the merged answer confirms it too.
    assert result["temporal_applied"] is True


@pytest.mark.asyncio
async def test_all_projects_search_forwards_an_overlap_filter(monkeypatch):
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
async def test_all_projects_search_without_a_filter_claims_nothing(monkeypatch):
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
    payloads: list[dict[str, Any]] = []
    _install_stub_client(monkeypatch, payloads, PROJECT_REFS)
    search_mod = importlib.import_module("basic_memory.mcp.tools.search")

    with pytest.raises(ValueError, match=bad_value):
        await search_mod.search_notes(
            query="cache layer",
            search_all_projects=True,
            output_format="json",
            valid_at=valid_at,
            valid_overlaps=valid_overlaps,
            time_kind=time_kind,
        )

    assert payloads == []


@pytest.mark.asyncio
async def test_all_projects_search_with_no_projects_still_confirms_the_filter(monkeypatch):
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
