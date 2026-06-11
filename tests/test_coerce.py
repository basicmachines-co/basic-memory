"""Tests for coerce_list, coerce_dict, strict_search_tags, and parse_str_list utility functions.

These must fail until the helpers are implemented in utils.py.
"""

from basic_memory.utils import coerce_dict, coerce_list, parse_str_list, strict_search_tags


class TestCoerceList:
    """Tests for coerce_list."""

    def test_none_passthrough(self):
        assert coerce_list(None) is None

    def test_native_list_passthrough(self):
        assert coerce_list(["a", "b"]) == ["a", "b"]

    def test_json_array_string(self):
        assert coerce_list('["entity", "observation"]') == ["entity", "observation"]

    def test_single_string_wrapped(self):
        assert coerce_list("entity") == ["entity"]

    def test_non_json_string_wrapped(self):
        assert coerce_list("not-json") == ["not-json"]

    def test_json_object_string_wrapped(self):
        """A JSON object string is not a list, so wrap it."""
        assert coerce_list('{"key": "val"}') == ['{"key": "val"}']

    def test_int_passthrough(self):
        """Non-string, non-None values pass through unchanged."""
        assert coerce_list(42) == 42


class TestStrictSearchTags:
    """Tests for strict_search_tags (the search_notes tags boundary coercer)."""

    def test_none_parses_to_empty_list(self):
        assert strict_search_tags(None) == []

    def test_comma_string_splits(self):
        assert strict_search_tags("a,b") == ["a", "b"]

    def test_list_with_comma_element_splits(self):
        assert strict_search_tags(["alpha,beta"]) == ["alpha", "beta"]

    def test_plain_list_passthrough(self):
        assert strict_search_tags(["a", "b"]) == ["a", "b"]

    def test_json_array_string(self):
        assert strict_search_tags('["a", "b"]') == ["a", "b"]

    def test_int_passthrough_for_pydantic_rejection(self):
        """Unsupported types pass through unchanged so Pydantic rejects them."""
        assert strict_search_tags(42) == 42

    def test_dict_passthrough_for_pydantic_rejection(self):
        value = {"a": 1}
        assert strict_search_tags(value) is value

    def test_int_list_passthrough_for_pydantic_rejection(self):
        """Lists with non-string elements pass through unchanged so Pydantic rejects them."""
        value = [42]
        assert strict_search_tags(value) is value

    def test_dict_list_passthrough_for_pydantic_rejection(self):
        value = [{"a": 1}]
        assert strict_search_tags(value) is value

    def test_mixed_list_passthrough_for_pydantic_rejection(self):
        """One bad element poisons the whole list — no partial stringification."""
        value = ["ok", 42]
        assert strict_search_tags(value) is value

    def test_json_array_string_with_int_passthrough_for_pydantic_rejection(self):
        """A JSON-array string with non-string elements must not be stringified."""
        value = "[42]"
        assert strict_search_tags(value) is value

    def test_json_array_string_with_dict_passthrough_for_pydantic_rejection(self):
        value = '[{"a": 1}]'
        assert strict_search_tags(value) is value

    def test_json_array_string_mixed_passthrough_for_pydantic_rejection(self):
        """One bad element poisons the whole JSON-array string — no partial parse."""
        value = '["ok", 42]'
        assert strict_search_tags(value) is value

    def test_json_array_string_all_strings_still_parses(self):
        assert strict_search_tags('["a","b"]') == ["a", "b"]


class TestCoerceDict:
    """Tests for coerce_dict."""

    def test_none_passthrough(self):
        assert coerce_dict(None) is None

    def test_native_dict_passthrough(self):
        assert coerce_dict({"k": "v"}) == {"k": "v"}

    def test_json_object_string(self):
        assert coerce_dict('{"status": "draft"}') == {"status": "draft"}

    def test_non_json_string_passthrough(self):
        """Non-parseable strings pass through (Pydantic will reject them)."""
        assert coerce_dict("not-json") == "not-json"

    def test_json_array_string_passthrough(self):
        """A JSON array string is not a dict, so pass through."""
        assert coerce_dict('["a", "b"]') == '["a", "b"]'

    def test_int_passthrough(self):
        assert coerce_dict(42) == 42


class TestParseStrList:
    """Tests for parse_str_list — the comma-split coercer for note_types/entity_types/categories."""

    # --- None input ---

    def test_none_returns_empty_list(self):
        assert parse_str_list(None) == []

    # --- Single string inputs ---

    def test_single_string_wraps_as_one_element(self):
        assert parse_str_list("note") == ["note"]

    def test_comma_string_splits(self):
        """The primary motivation for this function: "note,task" → ["note", "task"]."""
        assert parse_str_list("note,task") == ["note", "task"]

    def test_comma_string_with_spaces_strips(self):
        assert parse_str_list("note, task, person") == ["note", "task", "person"]

    # --- JSON array string inputs (MCP clients sometimes serialize lists as strings) ---

    def test_json_array_string(self):
        assert parse_str_list('["note", "task"]') == ["note", "task"]

    def test_json_array_string_single_element(self):
        assert parse_str_list('["note"]') == ["note"]

    def test_json_array_string_with_comma_elements(self):
        """JSON array where an element is itself a comma-string — flatten it."""
        assert parse_str_list('["note,task"]') == ["note", "task"]

    # --- List inputs ---

    def test_plain_list_passthrough(self):
        assert parse_str_list(["note", "task"]) == ["note", "task"]

    def test_list_with_comma_element_splits(self):
        """A list containing a comma-string is flattened."""
        assert parse_str_list(["note,task"]) == ["note", "task"]

    def test_list_with_multiple_comma_elements(self):
        assert parse_str_list(["note,task", "person"]) == ["note", "task", "person"]

    # --- No '#' stripping (unlike parse_tags) ---

    def test_hash_prefix_preserved(self):
        """parse_str_list must NOT strip '#' — these are type identifiers, not hashtags."""
        assert parse_str_list("#type") == ["#type"]

    def test_hash_prefix_in_comma_string_preserved(self):
        assert parse_str_list("#type,#other") == ["#type", "#other"]

    # --- Non-str/list/None pass through for Pydantic rejection ---

    def test_int_passthrough_for_pydantic_rejection(self):
        assert parse_str_list(42) == 42  # type: ignore[arg-type]

    def test_dict_passthrough_for_pydantic_rejection(self):
        value = {"a": 1}
        assert parse_str_list(value) is value  # type: ignore[arg-type]

    # --- Non-string list elements pass through unchanged (Codex review fix) ---

    def test_int_list_passthrough_for_pydantic_rejection(self):
        """Lists with non-string elements must not be stringified ([42] → ['42'])."""
        value = [42]
        assert parse_str_list(value) is value  # type: ignore[arg-type]

    def test_mixed_list_passthrough_for_pydantic_rejection(self):
        """One non-string element poisons the whole list — no partial coercion."""
        value = ["note", 42]
        assert parse_str_list(value) is value  # type: ignore[arg-type]

    def test_dict_list_passthrough_for_pydantic_rejection(self):
        value = [{"a": 1}]
        assert parse_str_list(value) is value  # type: ignore[arg-type]
