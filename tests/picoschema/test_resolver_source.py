"""Resolution retains the selected source without another lookup."""

from typing import Any

import pytest

from basic_memory.picoschema.resolver import SchemaCandidate, resolve_schema_with_source


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("frontmatter", "queries", "expected_source"),
    [
        ({"schema": {"local": "string"}, "type": "person"}, [], "inline-note"),
        ({"schema": "explicit", "type": "person"}, ["explicit"], "selected-schema"),
        ({"schema": "missing", "type": "person"}, ["missing", "person"], "selected-schema"),
        ({"type": "person"}, ["person"], "selected-schema"),
        ({"schema": "missing"}, ["missing"], None),
        ({"type": "missing"}, ["missing"], None),
        ({}, [], None),
    ],
)
async def test_source_follows_resolution(
    frontmatter: dict[str, Any], queries: list[str], expected_source: str | None
) -> None:
    searched: list[str] = []

    async def search(query: str) -> list[SchemaCandidate[str]]:
        searched.append(query)
        if query == "missing":
            return []
        return [
            SchemaCandidate({"entity": "person", "schema": {"name": "string"}}, "selected-schema"),
            SchemaCandidate({"entity": "person", "schema": {"role": "string"}}, "other-schema"),
        ]

    result = await resolve_schema_with_source(frontmatter, search, inline_source="inline-note")
    assert searched == queries
    if expected_source is None:
        assert result is None
    else:
        assert result is not None
        assert result.source == expected_source
        assert result.definition.fields[0].name == (
            "local" if expected_source == "inline-note" else "name"
        )


@pytest.mark.asyncio
async def test_invalid_selected_schema_does_not_skip_to_another_source() -> None:
    async def search(query: str) -> list[SchemaCandidate[str]]:
        return [
            SchemaCandidate({"entity": "person"}, "invalid-first"),
            SchemaCandidate({"entity": "person", "schema": {"name": "string"}}, "valid-second"),
        ]

    with pytest.raises(ValueError):
        await resolve_schema_with_source({"type": "person"}, search, inline_source="inline-note")
