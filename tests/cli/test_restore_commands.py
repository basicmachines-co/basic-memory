"""Tests for cloud restore CLI commands.

SPEC-29 Phase 3: Tests for restore command.
"""

from unittest.mock import Mock, patch

import httpx
from typer.testing import CliRunner

from basic_memory.cli.app import app
from basic_memory.cli.commands.cloud.api_client import (
    CloudAPIError,
    SubscriptionRequiredError,
)


class TestRestoreCommand:
    """Tests for 'bm cloud restore' command."""

    def test_restore_file_success_with_force(self):
        """Test successful file restoration with --force flag."""
        runner = CliRunner()

        mock_response = Mock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "restored": ["notes/project.md"],
            "snapshot_id": "snap_123",
        }

        async def mock_make_api_request(*args, **kwargs):
            return mock_response

        with patch(
            "basic_memory.cli.commands.cloud.restore.make_api_request",
            side_effect=mock_make_api_request,
        ):
            mock_config = Mock()
            mock_config.cloud_host = "https://cloud.example.com"
            mock_config_manager = Mock()
            mock_config_manager.config = mock_config

            with patch(
                "basic_memory.cli.commands.cloud.restore.ConfigManager",
                return_value=mock_config_manager,
            ):
                result = runner.invoke(
                    app,
                    [
                        "cloud",
                        "restore",
                        "notes/project.md",
                        "--snapshot",
                        "snap_123",
                        "--force",
                    ],
                )

                assert result.exit_code == 0
                assert "Successfully restored" in result.stdout
                assert "notes/project.md" in result.stdout

    def test_restore_folder_success(self):
        """Test successful folder restoration."""
        runner = CliRunner()

        mock_restore_response = Mock(spec=httpx.Response)
        mock_restore_response.status_code = 200
        mock_restore_response.json.return_value = {
            "restored": [
                "research/paper1.md",
                "research/paper2.md",
                "research/notes.md",
            ],
            "snapshot_id": "snap_123",
        }

        async def mock_make_api_request(*args, **kwargs):
            return mock_restore_response

        with patch(
            "basic_memory.cli.commands.cloud.restore.make_api_request",
            side_effect=mock_make_api_request,
        ):
            mock_config = Mock()
            mock_config.cloud_host = "https://cloud.example.com"
            mock_config_manager = Mock()
            mock_config_manager.config = mock_config

            with patch(
                "basic_memory.cli.commands.cloud.restore.ConfigManager",
                return_value=mock_config_manager,
            ):
                result = runner.invoke(
                    app,
                    ["cloud", "restore", "research/", "--snapshot", "snap_123", "--force"],
                )

                assert result.exit_code == 0
                assert "Successfully restored 3 file(s)" in result.stdout

    def test_restore_many_files_truncated_output(self):
        """Test restore output is truncated for many files."""
        runner = CliRunner()

        mock_response = Mock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "restored": [f"notes/file{i}.md" for i in range(20)],
            "snapshot_id": "snap_123",
        }

        async def mock_make_api_request(*args, **kwargs):
            return mock_response

        with patch(
            "basic_memory.cli.commands.cloud.restore.make_api_request",
            side_effect=mock_make_api_request,
        ):
            mock_config = Mock()
            mock_config.cloud_host = "https://cloud.example.com"
            mock_config_manager = Mock()
            mock_config_manager.config = mock_config

            with patch(
                "basic_memory.cli.commands.cloud.restore.ConfigManager",
                return_value=mock_config_manager,
            ):
                result = runner.invoke(
                    app,
                    ["cloud", "restore", "notes/", "--snapshot", "snap_123", "--force"],
                )

                assert result.exit_code == 0
                assert "Successfully restored 20 file(s)" in result.stdout
                assert "and 15 more" in result.stdout

    def test_restore_no_files_found(self):
        """Test restore when no files match the path."""
        runner = CliRunner()

        mock_response = Mock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "restored": [],
            "snapshot_id": "snap_123",
        }

        async def mock_make_api_request(*args, **kwargs):
            return mock_response

        with patch(
            "basic_memory.cli.commands.cloud.restore.make_api_request",
            side_effect=mock_make_api_request,
        ):
            mock_config = Mock()
            mock_config.cloud_host = "https://cloud.example.com"
            mock_config_manager = Mock()
            mock_config_manager.config = mock_config

            with patch(
                "basic_memory.cli.commands.cloud.restore.ConfigManager",
                return_value=mock_config_manager,
            ):
                result = runner.invoke(
                    app,
                    [
                        "cloud",
                        "restore",
                        "nonexistent/",
                        "--snapshot",
                        "snap_123",
                        "--force",
                    ],
                )

                assert result.exit_code == 0
                assert "No files were restored" in result.stdout

    def test_restore_snapshot_not_found(self):
        """Test restore from non-existent snapshot."""
        runner = CliRunner()

        async def mock_make_api_request(*args, **kwargs):
            raise CloudAPIError("Not found", status_code=404)

        with patch(
            "basic_memory.cli.commands.cloud.restore.make_api_request",
            side_effect=mock_make_api_request,
        ):
            mock_config = Mock()
            mock_config.cloud_host = "https://cloud.example.com"
            mock_config_manager = Mock()
            mock_config_manager.config = mock_config

            with patch(
                "basic_memory.cli.commands.cloud.restore.ConfigManager",
                return_value=mock_config_manager,
            ):
                result = runner.invoke(
                    app,
                    [
                        "cloud",
                        "restore",
                        "notes/project.md",
                        "--snapshot",
                        "snap_nonexistent",
                        "--force",
                    ],
                )

                assert result.exit_code == 1
                assert "Snapshot not found" in result.stdout

    def test_restore_subscription_required(self):
        """Test restore requires subscription."""
        runner = CliRunner()

        async def mock_make_api_request(*args, **kwargs):
            raise SubscriptionRequiredError(
                message="Active subscription required",
                subscribe_url="https://basicmemory.com/subscribe",
            )

        with patch(
            "basic_memory.cli.commands.cloud.restore.make_api_request",
            side_effect=mock_make_api_request,
        ):
            mock_config = Mock()
            mock_config.cloud_host = "https://cloud.example.com"
            mock_config_manager = Mock()
            mock_config_manager.config = mock_config

            with patch(
                "basic_memory.cli.commands.cloud.restore.ConfigManager",
                return_value=mock_config_manager,
            ):
                result = runner.invoke(
                    app,
                    [
                        "cloud",
                        "restore",
                        "notes/project.md",
                        "--snapshot",
                        "snap_123",
                        "--force",
                    ],
                )

                assert result.exit_code == 1
                assert "Subscription Required" in result.stdout
                assert "https://basicmemory.com/subscribe" in result.stdout

    def test_restore_cancelled_by_user(self):
        """Test restore cancelled by user confirmation."""
        runner = CliRunner()

        # First call is browse, second would be restore (should not happen)
        mock_browse_response = Mock(spec=httpx.Response)
        mock_browse_response.status_code = 200
        mock_browse_response.json.return_value = {
            "files": [
                {"key": "notes/project.md", "size": 1024, "last_modified": "2025-01-18T12:00:00Z"}
            ],
            "prefix": "notes/project.md",
            "snapshot_version": "12345",
        }

        call_count = 0

        async def mock_make_api_request(method, url, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            if "browse" in url:
                return mock_browse_response
            # Track unexpected calls - the test will verify later
            return mock_browse_response

        with patch(
            "basic_memory.cli.commands.cloud.restore.make_api_request",
            side_effect=mock_make_api_request,
        ):
            mock_config = Mock()
            mock_config.cloud_host = "https://cloud.example.com"
            mock_config_manager = Mock()
            mock_config_manager.config = mock_config

            with patch(
                "basic_memory.cli.commands.cloud.restore.ConfigManager",
                return_value=mock_config_manager,
            ):
                # Simulate user saying "n" to confirmation
                result = runner.invoke(
                    app,
                    ["cloud", "restore", "notes/project.md", "--snapshot", "snap_123"],
                    input="n\n",
                )

                assert result.exit_code == 0
                assert "cancelled" in result.stdout
                # Only one call should happen (browse), not the restore POST
                assert call_count == 1

    def test_restore_with_leading_slash_normalized(self):
        """Test that leading slashes are stripped from path."""
        runner = CliRunner()

        mock_response = Mock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "restored": ["notes/project.md"],
            "snapshot_id": "snap_123",
        }

        captured_json_data = []

        async def mock_make_api_request(*args, **kwargs):
            if "json_data" in kwargs:
                captured_json_data.append(kwargs["json_data"])
            return mock_response

        with patch(
            "basic_memory.cli.commands.cloud.restore.make_api_request",
            side_effect=mock_make_api_request,
        ):
            mock_config = Mock()
            mock_config.cloud_host = "https://cloud.example.com"
            mock_config_manager = Mock()
            mock_config_manager.config = mock_config

            with patch(
                "basic_memory.cli.commands.cloud.restore.ConfigManager",
                return_value=mock_config_manager,
            ):
                result = runner.invoke(
                    app,
                    [
                        "cloud",
                        "restore",
                        "/notes/project.md",  # Leading slash
                        "--snapshot",
                        "snap_123",
                        "--force",
                    ],
                )

                assert result.exit_code == 0
                # Verify the path was normalized (no leading slash)
                assert any(data.get("path") == "notes/project.md" for data in captured_json_data)

    def test_restore_api_error(self):
        """Test handling generic API errors during restore."""
        runner = CliRunner()

        async def mock_make_api_request(*args, **kwargs):
            raise CloudAPIError("Server error", status_code=500)

        with patch(
            "basic_memory.cli.commands.cloud.restore.make_api_request",
            side_effect=mock_make_api_request,
        ):
            mock_config = Mock()
            mock_config.cloud_host = "https://cloud.example.com"
            mock_config_manager = Mock()
            mock_config_manager.config = mock_config

            with patch(
                "basic_memory.cli.commands.cloud.restore.ConfigManager",
                return_value=mock_config_manager,
            ):
                result = runner.invoke(
                    app,
                    [
                        "cloud",
                        "restore",
                        "notes/project.md",
                        "--snapshot",
                        "snap_123",
                        "--force",
                    ],
                )

                assert result.exit_code == 1
                assert "Failed to restore" in result.stdout


class TestRestoreCommandHelp:
    """Tests for restore command help and usage."""

    def test_restore_requires_snapshot_option(self):
        """Test that --snapshot option is required."""
        runner = CliRunner()

        result = runner.invoke(app, ["cloud", "restore", "notes/project.md"])

        # Should fail due to missing required option (exit code 2 for usage error)
        assert result.exit_code == 2
        # Typer writes the error message to the output
        assert "Missing option" in result.output or "--snapshot" in result.output

    def test_restore_requires_path_argument(self):
        """Test that path argument is required."""
        runner = CliRunner()

        result = runner.invoke(app, ["cloud", "restore", "--snapshot", "snap_123"])

        # Should fail due to missing required argument
        assert result.exit_code != 0
