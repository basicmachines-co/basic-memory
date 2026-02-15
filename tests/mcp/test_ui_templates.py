"""Tests for MCP UI template helpers (templates.py).

Covers get_ui_variant(), load_html(), and load_variant_html().
"""

import pytest

from basic_memory.mcp.ui.templates import (
    get_ui_variant,
    load_html,
    load_variant_html,
    DEFAULT_VARIANT,
)


class TestGetUIVariant:
    """Tests for get_ui_variant()."""

    def test_default_variant(self, monkeypatch):
        """Returns 'vanilla' when env var is not set."""
        monkeypatch.delenv("BASIC_MEMORY_MCP_UI_VARIANT", raising=False)
        assert get_ui_variant() == "vanilla"

    def test_vanilla_variant(self, monkeypatch):
        monkeypatch.setenv("BASIC_MEMORY_MCP_UI_VARIANT", "vanilla")
        assert get_ui_variant() == "vanilla"

    def test_tool_ui_variant(self, monkeypatch):
        monkeypatch.setenv("BASIC_MEMORY_MCP_UI_VARIANT", "tool-ui")
        assert get_ui_variant() == "tool-ui"

    def test_mcp_ui_variant(self, monkeypatch):
        monkeypatch.setenv("BASIC_MEMORY_MCP_UI_VARIANT", "mcp-ui")
        assert get_ui_variant() == "mcp-ui"

    def test_unsupported_variant_falls_back(self, monkeypatch):
        """Unsupported values fall back to DEFAULT_VARIANT."""
        monkeypatch.setenv("BASIC_MEMORY_MCP_UI_VARIANT", "nonexistent")
        assert get_ui_variant() == DEFAULT_VARIANT

    def test_whitespace_trimmed(self, monkeypatch):
        monkeypatch.setenv("BASIC_MEMORY_MCP_UI_VARIANT", "  tool-ui  ")
        assert get_ui_variant() == "tool-ui"

    def test_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("BASIC_MEMORY_MCP_UI_VARIANT", "VANILLA")
        assert get_ui_variant() == "vanilla"


class TestLoadHtml:
    """Tests for load_html()."""

    def test_load_search_results_vanilla(self):
        html = load_html("search-results-vanilla.html")
        assert isinstance(html, str)
        assert len(html) > 0
        # HTML files should contain standard HTML markers
        assert "<" in html

    def test_load_note_preview_vanilla(self):
        html = load_html("note-preview-vanilla.html")
        assert isinstance(html, str)
        assert len(html) > 0

    def test_load_nonexistent_raises(self):
        with pytest.raises(FileNotFoundError):
            load_html("does-not-exist.html")


class TestLoadVariantHtml:
    """Tests for load_variant_html()."""

    def test_loads_vanilla_variant(self, monkeypatch):
        monkeypatch.setenv("BASIC_MEMORY_MCP_UI_VARIANT", "vanilla")
        html = load_variant_html("search-results")
        assert isinstance(html, str)
        assert len(html) > 0

    def test_loads_tool_ui_variant(self, monkeypatch):
        monkeypatch.setenv("BASIC_MEMORY_MCP_UI_VARIANT", "tool-ui")
        html = load_variant_html("search-results")
        assert isinstance(html, str)
        assert len(html) > 0

    def test_loads_mcp_ui_variant(self, monkeypatch):
        monkeypatch.setenv("BASIC_MEMORY_MCP_UI_VARIANT", "mcp-ui")
        html = load_variant_html("note-preview")
        assert isinstance(html, str)
        assert len(html) > 0
