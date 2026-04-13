"""Tests for basic_memory.schema.resolver -- schema resolution order."""

from unittest.mock import AsyncMock

import pytest

from basic_memory.schema.parser import SchemaDefinition
from basic_memory.schema.resolver import resolve_schema


@pytest.fixture
def mock_search_fn():
    """A mock search function that returns empty by default."""
    return AsyncMock(return_value=[])


# --- Inline schema (priority 1) ---


class TestInlineSchema:
    @pytest.mark.asyncio
    async def test_inline_schema_parsed_directly(self, mock_search_fn):
        frontmatter = {
            "type": "Meeting",
            "schema": {
                "date": "string",
                "attendees?(array)": "string",
            },
        }
        result = await resolve_schema(frontmatter, mock_search_fn)

        assert result is not None
        assert isinstance(result, SchemaDefinition)
        assert result.entity == "Meeting"
        assert len(result.fields) == 2
        # Inline schema should NOT call search at all
        mock_search_fn.assert_not_called()

    @pytest.mark.asyncio
    async def test_inline_schema_uses_type_for_entity(self, mock_search_fn):
        frontmatter = {
            "type": "CustomType",
            "schema": {"field": "string"},
        }
        result = await resolve_schema(frontmatter, mock_search_fn)
        assert result.entity == "CustomType"

    @pytest.mark.asyncio
    async def test_inline_schema_defaults_entity_to_unknown(self, mock_search_fn):
        frontmatter = {
            "schema": {"field": "string"},
        }
        result = await resolve_schema(frontmatter, mock_search_fn)
        assert result.entity == "unknown"

    @pytest.mark.asyncio
    async def test_inline_schema_respects_validation_mode(self, mock_search_fn):
        frontmatter = {
            "type": "Test",
            "schema": {"name": "string"},
            "settings": {"validation": "strict"},
        }
        result = await resolve_schema(frontmatter, mock_search_fn)
        assert result.validation_mode == "strict"


# --- Explicit reference (priority 2) ---


class TestExplicitReference:
    @pytest.mark.asyncio
    async def test_explicit_ref_calls_search(self, mock_search_fn):
        schema_note_frontmatter = {
            "entity": "Person",
            "schema": {"name": "string"},
        }
        mock_search_fn.return_value = [schema_note_frontmatter]

        frontmatter = {"schema": "Person"}
        result = await resolve_schema(frontmatter, mock_search_fn)

        assert result is not None
        assert result.entity == "Person"
        mock_search_fn.assert_called_once_with("Person")

    @pytest.mark.asyncio
    async def test_explicit_ref_not_found_falls_through(self, mock_search_fn):
        """If explicit ref search returns nothing, fall through to implicit by type."""
        mock_search_fn.return_value = []

        frontmatter = {"schema": "NonExistent", "type": "Person"}
        await resolve_schema(frontmatter, mock_search_fn)

        # Search called twice: once for explicit ref "NonExistent", once for type "Person"
        assert mock_search_fn.call_count == 2

    @pytest.mark.asyncio
    async def test_explicit_ref_not_found_no_type_returns_none(self, mock_search_fn):
        frontmatter = {"schema": "NonExistent"}
        result = await resolve_schema(frontmatter, mock_search_fn)
        assert result is None


# --- Implicit by type (priority 3) ---


class TestImplicitByType:
    @pytest.mark.asyncio
    async def test_implicit_type_lookup(self, mock_search_fn):
        schema_note_frontmatter = {
            "entity": "Book",
            "schema": {"title": "string"},
        }
        mock_search_fn.return_value = [schema_note_frontmatter]

        frontmatter = {"type": "Book"}
        result = await resolve_schema(frontmatter, mock_search_fn)

        assert result is not None
        assert result.entity == "Book"
        mock_search_fn.assert_called_once_with("Book")

    @pytest.mark.asyncio
    async def test_implicit_type_not_found(self, mock_search_fn):
        frontmatter = {"type": "UnknownType"}
        result = await resolve_schema(frontmatter, mock_search_fn)
        assert result is None


# --- No schema (priority 4) ---


class TestNoSchema:
    @pytest.mark.asyncio
    async def test_no_schema_no_type(self, mock_search_fn):
        result = await resolve_schema({}, mock_search_fn)
        assert result is None
        mock_search_fn.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_frontmatter(self, mock_search_fn):
        result = await resolve_schema({}, mock_search_fn)
        assert result is None


# --- Priority order ---


class TestResolutionPriority:
    @pytest.mark.asyncio
    async def test_inline_beats_type(self, mock_search_fn):
        """Inline schema should win over implicit type lookup."""
        frontmatter = {
            "type": "Person",
            "schema": {"custom_field": "string"},
        }
        result = await resolve_schema(frontmatter, mock_search_fn)

        # Inline schema parsed, search never called
        assert result is not None
        assert result.fields[0].name == "custom_field"
        mock_search_fn.assert_not_called()
