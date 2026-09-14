"""Account search carries query guidance only for a complete, empty first page."""

import importlib
from typing import Literal
from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["empty", "hit", "failed", "later"])
@pytest.mark.parametrize("output_format", ["text", "json"])
async def test_fanout_query_hint(monkeypatch, case: str, output_format: Literal["text", "json"]):
    search = importlib.import_module("basic_memory.mcp.tools.search")
    monkeypatch.setattr(
        search,
        "_load_search_project_refs",
        AsyncMock(
            return_value=[
                {"project": "one", "project_id": None},
                {"project": "two", "project_id": None},
            ]
        ),
    )
    empty = {
        "results": [],
        "total": 0,
        "total_is_exact": True,
        "query_hint": "Try a shorter word from your query.",
    }
    second: dict[str, object] | str = empty
    if case == "hit":
        second = {
            "results": [
                {
                    "title": "Match",
                    "type": "entity",
                    "score": 1.0,
                    "file_path": "note.md",
                    "permalink": "note",
                }
            ],
            "total": 1,
            "total_is_exact": True,
        }
    elif case == "failed":
        second = "# Search Failed - Access Error"
    monkeypatch.setattr(search, "search_notes", AsyncMock(side_effect=[empty, second]))
    result = await search._search_all_projects(
        query="雾凇拼音",
        page=2 if case == "later" else 1,
        page_size=10,
        search_type="text",
        output_format=output_format,
        note_types=[],
        entity_types=[],
        categories=[],
        after_date=None,
        metadata_filters=None,
        tags=None,
        status=None,
        min_similarity=None,
        valid_at=None,
        valid_overlaps=None,
        time_kind=None,
        context=None,
    )
    if isinstance(result, str):
        assert ("shorter word" in result) == (case == "empty")
    else:
        assert (result.get("query_hint") is not None) == (case == "empty")
