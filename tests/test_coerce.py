"""Tests for coerce_list, coerce_dict, and strict_search_tags utility functions.

These must fail until the helpers are implemented in utils.py.
"""

from basic_memory.utils import coerce_list, coerce_dict, strict_search_tags


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
