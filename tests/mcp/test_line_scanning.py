"""Exercise line reads and literal grep through real project-scoped API clients."""

import json
from typing import Any

import pytest
from fastmcp.exceptions import ToolError
from httpx import HTTPStatusError, Request, Response

from basic_memory.mcp.clients import KnowledgeClient, SearchClient
from basic_memory.mcp.tools import cat, grep, read_note, write_note
from basic_memory.schemas.search import SearchResponse
from basic_memory.schemas.v2 import EntityResponseV2


@pytest.mark.asyncio
async def test_scan_read_round_trip_and_token_savings(client, test_project) -> None:
    body = "\n".join(
        "retry the operation" if n in (70, 72, 180) else f"Background line {n}: " + "detail " * 15
        for n in range(200)
    )
    await write_note(title="Scan Note", directory="test", content=body, project=test_project.name)
    full = await cat("Scan Note", project=test_project.name)
    lines = full["content"].splitlines()
    expected_matches = [n for n, line in enumerate(lines, 1) if "retry" in line]

    result = await grep(
        "retry", literal=True, context_lines=1, max_matches=2, project=test_project.name
    )
    ordinary = await grep("retry", literal=True, project=test_project.name)
    assert len(json.dumps(result)) < len(json.dumps(ordinary)) / 2
    assert result["pagination_scope"] == "search_candidates"
    row = result["results"][0]
    assert "content" not in row and "matched_chunk" not in row and "frontmatter" not in row
    assert row["match_count"] == 3
    assert row["next_match_line"] == expected_matches[2]
    assert len(row["windows"]) == 1
    window = row["windows"][0]
    assert window["match_lines"] == expected_matches[:2]

    sliced = await read_note(
        row["external_id"],
        start_line=window["start_line"],
        end_line=window["end_line"],
        output_format="json",
        project=test_project.name,
    )
    assert isinstance(sliced, dict)
    assert sliced["content"] == window["content"]
    assert sliced["total_lines"] == len(lines)
    assert sliced["has_more"] is True
    assert sliced["next_start_line"] == window["end_line"] + 1
    assert sliced["next_end_line"] == window["end_line"] + len(window["content"].splitlines())
    assert sliced["frontmatter"] is None
    cat_slice = await cat(
        row["file_path"],
        start_line=window["start_line"],
        end_line=window["end_line"],
        project=test_project.name,
    )
    assert cat_slice["content"] == sliced["content"]
    assert len(json.dumps(sliced)) < len(json.dumps(full)) / 10

    numbered = await read_note(
        "Scan Note",
        start_line=expected_matches[0],
        end_line=expected_matches[0],
        project=test_project.name,
    )
    assert isinstance(numbered, str)
    assert f"{expected_matches[0]}: retry the operation" in numbered
    assert "next: start_line=" in numbered


@pytest.mark.asyncio
async def test_ranges_include_frontmatter_and_have_eof_metadata(client, test_project) -> None:
    await write_note(
        title="Bounds", directory="test", content="alpha\nbeta", project=test_project.name
    )
    full = await cat("Bounds", project=test_project.name)
    lines = full["content"].splitlines()
    opening = await read_note("Bounds", end_line=2, output_format="json", project=test_project.name)
    assert isinstance(opening, dict)
    assert opening["content"] == "\n".join(lines[:2])
    assert opening["start_line"] == 1
    for end in (None, len(lines) + 100):
        last = await read_note(
            "Bounds",
            start_line=len(lines),
            end_line=end,
            output_format="json",
            project=test_project.name,
        )
        assert isinstance(last, dict)
        assert last["content"] == lines[-1]
        assert last["has_more"] is False
        assert last["next_start_line"] is None and last["next_end_line"] is None
    empty = await read_note(
        "Bounds", start_line=1000, output_format="json", project=test_project.name
    )
    assert isinstance(empty, dict)
    assert empty["content"] == "" and empty["has_more"] is False


@pytest.mark.asyncio
async def test_exact_uuid_line_read_is_one_sliced_get(client, test_project, monkeypatch) -> None:
    await write_note(title="One Get", directory="test", content="alpha", project=test_project.name)
    found = await grep("alpha", literal=True, project=test_project.name)
    identifier = found["results"][0]["external_id"]
    original = KnowledgeClient.get_entity
    calls: list[tuple[str, str | None]] = []

    async def record(
        self: KnowledgeClient,
        entity_id: str,
        *,
        section: str | None = None,
        lines: str | None = None,
        max_tokens: int | None = None,
    ) -> EntityResponseV2:
        calls.append((entity_id, lines))
        return await original(self, entity_id, section=section, lines=lines, max_tokens=max_tokens)

    async def refuse_resolve(*args: object, **kwargs: object) -> str:
        raise AssertionError("UUID must not resolve")

    monkeypatch.setattr(KnowledgeClient, "get_entity", record)
    monkeypatch.setattr(KnowledgeClient, "resolve_entity", refuse_resolve)
    await read_note(identifier, end_line=2, project=test_project.name)
    assert calls == [(identifier, "1-2")]


@pytest.mark.asyncio
async def test_exact_title_fallback_keeps_line_scan(client, test_project, monkeypatch) -> None:
    await write_note(
        title="Exact Fallback", directory="test", content="alpha", project=test_project.name
    )

    async def refuse_resolve(*args: object, **kwargs: object) -> str:
        request = Request("POST", "http://test/knowledge/resolve")
        raise ToolError("force title search") from HTTPStatusError(
            "Not found", request=request, response=Response(404, request=request)
        )

    monkeypatch.setattr(KnowledgeClient, "resolve_entity", refuse_resolve)
    result = await read_note("Exact Fallback", end_line=1, project=test_project.name)
    assert isinstance(result, str)
    assert "1: ---" in result


@pytest.mark.asyncio
async def test_grep_candidate_pagination_survives_no_current_match(
    client, test_project, monkeypatch
) -> None:
    for title in ("First", "Second"):
        await write_note(title=title, directory="test", content="retry", project=test_project.name)
    original = KnowledgeClient.get_entity

    async def changed_content(self: KnowledgeClient, entity_id: str) -> EntityResponseV2:
        entity = await original(self, entity_id)
        # Simulate a newer accepted document while the index still matches retry.
        return entity.model_copy(update={"content": "already fixed\n"})

    monkeypatch.setattr(KnowledgeClient, "get_entity", changed_content)
    first = await grep(
        "retry", literal=True, context_lines=0, page_size=1, project=test_project.name
    )
    assert first["has_more"] is True
    row = first["results"][0]
    assert row["match_count"] == 0 and row["windows"] == []
    assert row["total_lines"] == 1
    assert row["next_match_line"] is None
    second = await grep(
        "retry", literal=True, context_lines=0, page=2, page_size=1, project=test_project.name
    )
    assert second["results"][0]["external_id"] != row["external_id"]


@pytest.mark.asyncio
async def test_grep_refuses_missing_candidate_identity(client, test_project, monkeypatch) -> None:
    await write_note(title="Legacy", directory="test", content="retry", project=test_project.name)
    original = SearchClient.search

    async def old_server(
        self: SearchClient, query: dict[str, Any], *, page: int, page_size: int
    ) -> SearchResponse:
        response = await original(self, query, page=page, page_size=page_size)
        return response.model_copy(
            update={
                "results": [
                    row.model_copy(update={"external_id": None}) for row in response.results
                ]
            }
        )

    monkeypatch.setattr(SearchClient, "search", old_server)
    with pytest.raises(ToolError, match="external_id"):
        await grep("retry", literal=True, context_lines=0, project=test_project.name)


@pytest.mark.parametrize(
    "bounds", [{"start_line": 0}, {"end_line": 0}, {"start_line": 5, "end_line": 3}]
)
@pytest.mark.asyncio
async def test_invalid_read_bounds_fail_before_routing(bounds: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        await read_note("any", start_line=bounds.get("start_line"), end_line=bounds.get("end_line"))


@pytest.mark.parametrize(
    ("literal", "context_lines", "max_matches", "page_size", "pattern"),
    [
        (False, 1, 10, 10, "retry"),
        (True, -1, 10, 10, "retry"),
        (True, 11, 10, 10, "retry"),
        (True, 0, 0, 10, "retry"),
        (True, 0, 101, 10, "retry"),
        (True, 0, 10, 101, "retry"),
        (True, None, 3, 10, "retry"),
        (True, 0, 10, 10, "a\nb"),
    ],
)
@pytest.mark.asyncio
async def test_invalid_grep_options_fail_before_routing(
    literal: bool,
    context_lines: int | None,
    max_matches: int,
    page_size: int,
    pattern: str,
) -> None:
    with pytest.raises(ValueError):
        await grep(
            pattern,
            literal=literal,
            context_lines=context_lines,
            max_matches=max_matches,
            page_size=page_size,
        )
