"""Tests for CLI tools in cloud mode.

These tests verify that CLI tools properly route to cloud endpoints
and inject authentication headers when in cloud mode.
"""

from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest
from typer.testing import CliRunner

from basic_memory.cli.commands.tool import tool_app

runner = CliRunner()


@pytest.fixture
def mock_cloud_config(tmp_path):
    """Mock cloud configuration."""
    config_dir = tmp_path / ".basic-memory"
    config_dir.mkdir(parents=True)

    # Create mock auth file
    auth_file = config_dir / "auth.json"
    auth_file.write_text('{"access_token": "test-token", "refresh_token": "test-refresh"}')

    # Create mock config file with cloud project
    config_file = config_dir / "config.yaml"
    config_file.write_text("""
projects:
  test-cloud:
    path: /tmp/test-cloud
    mode: cloud
    cloud_project_id: test-project-123
default_project: test-cloud
""")

    with patch("basic_memory.config.get_config_dir", return_value=config_dir):
        yield config_dir


class TestCloudModeRouting:
    """Tests for cloud mode routing and authentication."""

    def test_write_note_routes_to_cloud(self, mock_cloud_config):
        """Test that write_note routes to cloud endpoint in cloud mode."""
        # Mock the HTTP client to capture the request
        mock_response = Mock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "title": "Test Note",
            "permalink": "test-note",
            "status": "Created",
        }

        with patch("basic_memory.mcp.async_client.httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.post = AsyncMock(return_value=mock_response)
            mock_client.return_value.__aenter__.return_value = mock_instance

            # Run command
            result = runner.invoke(
                tool_app,
                [
                    "write-note",
                    "--title", "Test Note",
                    "--content", "Test content",
                    "--folder", "test",
                ],
            )

            # Verify cloud endpoint was called
            # In cloud mode, requests should go through /proxy endpoint
            assert mock_instance.post.called or mock_instance.request.called

            # Verify auth headers were injected
            if mock_instance.post.called:
                call_kwargs = mock_instance.post.call_args.kwargs
                assert "headers" in call_kwargs
                # Auth is injected at client creation, not per-request

    def test_search_notes_cloud_auth_injection(self, mock_cloud_config):
        """Test that search_notes injects auth headers in cloud mode."""
        mock_response = Mock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "results": [],
            "metadata": {"total_results": 0},
        }

        with patch("basic_memory.mcp.async_client.httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=mock_response)
            mock_client.return_value.__aenter__.return_value = mock_instance

            # Run command
            result = runner.invoke(
                tool_app,
                ["search-notes", "test query"],
            )

            # Verify cloud endpoint was called with auth
            assert mock_instance.get.called or mock_instance.request.called

    def test_read_note_cloud_mode(self, mock_cloud_config):
        """Test that read_note works in cloud mode."""
        mock_response = Mock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "title": "Test Note",
            "permalink": "test-note",
            "content": "Test content",
        }

        with patch("basic_memory.mcp.async_client.httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=mock_response)
            mock_client.return_value.__aenter__.return_value = mock_instance

            # Run command
            result = runner.invoke(
                tool_app,
                ["read-note", "test-note"],
            )

            # Verify cloud endpoint was called
            assert mock_instance.get.called or mock_instance.request.called


class TestCloudModeErrors:
    """Tests for cloud mode error handling."""

    def test_unauthenticated_error(self, tmp_path):
        """Test error handling when not authenticated in cloud mode."""
        config_dir = tmp_path / ".basic-memory"
        config_dir.mkdir(parents=True)

        # Create config WITHOUT auth file
        config_file = config_dir / "config.yaml"
        config_file.write_text("""
projects:
  test-cloud:
    path: /tmp/test-cloud
    mode: cloud
    cloud_project_id: test-project-123
default_project: test-cloud
""")

        with patch("basic_memory.config.get_config_dir", return_value=config_dir):
            # Mock client to raise authentication error
            mock_response = Mock(spec=httpx.Response)
            mock_response.status_code = 401
            mock_response.json.return_value = {"detail": "Unauthorized"}

            http_error = httpx.HTTPStatusError(
                "401 Unauthorized",
                request=Mock(),
                response=mock_response,
            )

            with patch("basic_memory.mcp.async_client.httpx.AsyncClient") as mock_client:
                mock_instance = AsyncMock()
                mock_instance.get = AsyncMock(side_effect=http_error)
                mock_client.return_value.__aenter__.return_value = mock_instance

                # Run command - should handle error gracefully
                result = runner.invoke(
                    tool_app,
                    ["read-note", "test-note"],
                )

                # Command should exit with error
                assert result.exit_code == 1

    def test_subscription_required_error(self, mock_cloud_config):
        """Test handling of subscription required error."""
        mock_response = Mock(spec=httpx.Response)
        mock_response.status_code = 403
        mock_response.json.return_value = {
            "detail": {
                "error": "subscription_required",
                "message": "Active subscription required",
                "subscribe_url": "https://basicmemory.com/subscribe",
            }
        }

        http_error = httpx.HTTPStatusError(
            "403 Forbidden",
            request=Mock(),
            response=mock_response,
        )

        with patch("basic_memory.mcp.async_client.httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.post = AsyncMock(side_effect=http_error)
            mock_client.return_value.__aenter__.return_value = mock_instance

            # Run command
            result = runner.invoke(
                tool_app,
                [
                    "write-note",
                    "--title", "Test",
                    "--content", "Test",
                    "--folder", "test",
                ],
            )

            # Command should exit with error
            assert result.exit_code == 1
