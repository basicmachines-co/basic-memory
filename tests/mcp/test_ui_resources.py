"""Tests for MCP UI resource endpoints (resources/ui.py).

Each resource function is wrapped by @mcp.resource() into a FunctionResource.
We call the underlying .fn() to exercise the template loading logic.
"""

from basic_memory.mcp.resources.ui import (
    search_results_ui,
    note_preview_ui,
    search_results_ui_vanilla,
    search_results_ui_tool_ui,
    search_results_ui_mcp_ui,
    note_preview_ui_vanilla,
    note_preview_ui_tool_ui,
    note_preview_ui_mcp_ui,
)


class TestVariantResources:
    """Tests for variant-agnostic resource endpoints."""

    def test_search_results_ui(self, monkeypatch):
        """search_results_ui loads the variant-specific template."""
        monkeypatch.setenv("BASIC_MEMORY_MCP_UI_VARIANT", "vanilla")
        html = search_results_ui.fn()
        assert isinstance(html, str)
        assert len(html) > 0

    def test_note_preview_ui(self, monkeypatch):
        """note_preview_ui loads the variant-specific template."""
        monkeypatch.setenv("BASIC_MEMORY_MCP_UI_VARIANT", "vanilla")
        html = note_preview_ui.fn()
        assert isinstance(html, str)
        assert len(html) > 0


class TestExplicitVariantResources:
    """Tests for variant-specific resource endpoints."""

    def test_search_results_vanilla(self):
        html = search_results_ui_vanilla.fn()
        assert isinstance(html, str)
        assert len(html) > 0

    def test_search_results_tool_ui(self):
        html = search_results_ui_tool_ui.fn()
        assert isinstance(html, str)
        assert len(html) > 0

    def test_search_results_mcp_ui(self):
        html = search_results_ui_mcp_ui.fn()
        assert isinstance(html, str)
        assert len(html) > 0

    def test_note_preview_vanilla(self):
        html = note_preview_ui_vanilla.fn()
        assert isinstance(html, str)
        assert len(html) > 0

    def test_note_preview_tool_ui(self):
        html = note_preview_ui_tool_ui.fn()
        assert isinstance(html, str)
        assert len(html) > 0

    def test_note_preview_mcp_ui(self):
        html = note_preview_ui_mcp_ui.fn()
        assert isinstance(html, str)
        assert len(html) > 0
