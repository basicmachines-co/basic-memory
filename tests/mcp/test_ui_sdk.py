"""Tests for MCP UI SDK helpers and tools.

Covers:
- basic_memory.mcp.ui.sdk (build_embedded_ui_resource, _ensure_sdk, MissingMCPUIServerError)
- basic_memory.mcp.tools.ui_sdk (_text_block, search_notes_ui, read_note_ui)
"""

from unittest.mock import MagicMock

import pytest

from basic_memory.mcp.ui.sdk import (
    MissingMCPUIServerError,
    _ensure_sdk,
    build_embedded_ui_resource,
)
from basic_memory.mcp.tools.ui_sdk import _text_block


class TestMissingMCPUIServerError:
    """MissingMCPUIServerError is a RuntimeError."""

    def test_is_runtime_error(self):
        assert issubclass(MissingMCPUIServerError, RuntimeError)

    def test_message(self):
        err = MissingMCPUIServerError("not installed")
        assert str(err) == "not installed"


class TestEnsureSdk:
    """Tests for _ensure_sdk() guard function."""

    def test_raises_when_sdk_not_installed(self, monkeypatch):
        """When mcp_ui_server is not importable, _ensure_sdk raises."""
        import basic_memory.mcp.ui.sdk as sdk_mod

        monkeypatch.setattr(sdk_mod, "create_ui_resource", None)
        monkeypatch.setattr(sdk_mod, "UIMetadataKey", None)

        with pytest.raises(MissingMCPUIServerError, match="mcp-ui-server is not installed"):
            _ensure_sdk()

    def test_returns_tuple_when_available(self, monkeypatch):
        """When SDK is available, returns (create_ui_resource, UIMetadataKey)."""
        import basic_memory.mcp.ui.sdk as sdk_mod

        mock_create = MagicMock()
        mock_keys = MagicMock()
        monkeypatch.setattr(sdk_mod, "create_ui_resource", mock_create)
        monkeypatch.setattr(sdk_mod, "UIMetadataKey", mock_keys)

        create_fn, keys = _ensure_sdk()
        assert create_fn is mock_create
        assert keys is mock_keys


class TestBuildEmbeddedUIResource:
    """Tests for build_embedded_ui_resource()."""

    def test_calls_sdk_correctly(self, monkeypatch):
        """Builds a resource dict and passes it to create_ui_resource."""
        import basic_memory.mcp.ui.sdk as sdk_mod

        mock_keys = MagicMock()
        mock_keys.PREFERRED_FRAME_SIZE = "preferredFrameSize"
        mock_keys.INITIAL_RENDER_DATA = "initialRenderData"

        mock_create = MagicMock(return_value={"type": "resource"})
        monkeypatch.setattr(sdk_mod, "create_ui_resource", mock_create)
        monkeypatch.setattr(sdk_mod, "UIMetadataKey", mock_keys)

        # Mock load_html to avoid filesystem dependency
        monkeypatch.setattr(sdk_mod, "load_html", lambda f: "<html>test</html>")

        result = build_embedded_ui_resource(
            uri="ui://test/resource",
            html_filename="test.html",
            render_data={"key": "value"},
            preferred_frame_size=["100%", "400px"],
            metadata={"custom": "meta"},
        )

        assert result == {"type": "resource"}
        mock_create.assert_called_once()
        call_arg = mock_create.call_args[0][0]
        assert call_arg["uri"] == "ui://test/resource"
        assert call_arg["content"]["htmlString"] == "<html>test</html>"
        assert call_arg["metadata"] == {"custom": "meta"}

    def test_metadata_defaults_to_empty_dict(self, monkeypatch):
        """When metadata is None, passes empty dict."""
        import basic_memory.mcp.ui.sdk as sdk_mod

        mock_keys = MagicMock()
        mock_keys.PREFERRED_FRAME_SIZE = "preferredFrameSize"
        mock_keys.INITIAL_RENDER_DATA = "initialRenderData"

        mock_create = MagicMock(return_value={"type": "resource"})
        monkeypatch.setattr(sdk_mod, "create_ui_resource", mock_create)
        monkeypatch.setattr(sdk_mod, "UIMetadataKey", mock_keys)
        monkeypatch.setattr(sdk_mod, "load_html", lambda f: "<html></html>")

        build_embedded_ui_resource(
            uri="ui://test",
            html_filename="t.html",
            render_data={},
            preferred_frame_size=["100%", "300px"],
        )

        call_arg = mock_create.call_args[0][0]
        assert call_arg["metadata"] == {}

    def test_raises_when_sdk_missing(self, monkeypatch):
        """Raises MissingMCPUIServerError when SDK is not installed."""
        import basic_memory.mcp.ui.sdk as sdk_mod

        monkeypatch.setattr(sdk_mod, "create_ui_resource", None)
        monkeypatch.setattr(sdk_mod, "UIMetadataKey", None)

        with pytest.raises(MissingMCPUIServerError):
            build_embedded_ui_resource(
                uri="ui://test",
                html_filename="t.html",
                render_data={},
                preferred_frame_size=["100%", "300px"],
            )


class TestTextBlock:
    """Tests for the _text_block helper."""

    def test_returns_single_text_content(self):
        blocks = _text_block("hello world")
        assert len(blocks) == 1
        assert blocks[0].type == "text"
        assert blocks[0].text == "hello world"
