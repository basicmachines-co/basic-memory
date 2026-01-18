"""Tests for cloud snapshot CLI commands.

SPEC-29 Phase 3: Tests for snapshot create, list, delete, show, browse commands.
"""

from unittest.mock import Mock, patch

import httpx
from typer.testing import CliRunner

from basic_memory.cli.app import app
from basic_memory.cli.commands.cloud.api_client import (
    CloudAPIError,
    SubscriptionRequiredError,
)


class TestSnapshotCreateCommand:
    """Tests for 'bm cloud snapshot create' command."""

    def test_create_snapshot_success(self):
        """Test successful snapshot creation."""
        runner = CliRunner()

        mock_response = Mock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "snap_123",
            "bucket_name": "tenant-abc",
            "snapshot_version": "1703430000000",
            "name": "manual-snapshot",
            "description": "before major refactor",
            "auto": False,
            "created_at": "2024-12-24T12:00:00Z",
        }

        async def mock_make_api_request(*args, **kwargs):
            return mock_response

        with patch(
            "basic_memory.cli.commands.cloud.snapshot.make_api_request",
            side_effect=mock_make_api_request,
        ):
            mock_config = Mock()
            mock_config.cloud_host = "https://cloud.example.com"
            mock_config_manager = Mock()
            mock_config_manager.config = mock_config

            with patch(
                "basic_memory.cli.commands.cloud.snapshot.ConfigManager",
                return_value=mock_config_manager,
            ):
                result = runner.invoke(
                    app, ["cloud", "snapshot", "create", "before major refactor"]
                )

                assert result.exit_code == 0
                assert "Snapshot created successfully" in result.stdout
                assert "snap_123" in result.stdout
                assert "before major refactor" in result.stdout

    def test_create_snapshot_subscription_required(self):
        """Test snapshot creation requires subscription."""
        runner = CliRunner()

        async def mock_make_api_request(*args, **kwargs):
            raise SubscriptionRequiredError(
                message="Active subscription required",
                subscribe_url="https://basicmemory.com/subscribe",
            )

        with patch(
            "basic_memory.cli.commands.cloud.snapshot.make_api_request",
            side_effect=mock_make_api_request,
        ):
            mock_config = Mock()
            mock_config.cloud_host = "https://cloud.example.com"
            mock_config_manager = Mock()
            mock_config_manager.config = mock_config

            with patch(
                "basic_memory.cli.commands.cloud.snapshot.ConfigManager",
                return_value=mock_config_manager,
            ):
                result = runner.invoke(app, ["cloud", "snapshot", "create", "test snapshot"])

                assert result.exit_code == 1
                assert "Subscription Required" in result.stdout
                assert "https://basicmemory.com/subscribe" in result.stdout

    def test_create_snapshot_api_error(self):
        """Test handling API errors during snapshot creation."""
        runner = CliRunner()

        async def mock_make_api_request(*args, **kwargs):
            raise CloudAPIError("Server error", status_code=500)

        with patch(
            "basic_memory.cli.commands.cloud.snapshot.make_api_request",
            side_effect=mock_make_api_request,
        ):
            mock_config = Mock()
            mock_config.cloud_host = "https://cloud.example.com"
            mock_config_manager = Mock()
            mock_config_manager.config = mock_config

            with patch(
                "basic_memory.cli.commands.cloud.snapshot.ConfigManager",
                return_value=mock_config_manager,
            ):
                result = runner.invoke(app, ["cloud", "snapshot", "create", "test snapshot"])

                assert result.exit_code == 1
                assert "Failed to create snapshot" in result.stdout


class TestSnapshotListCommand:
    """Tests for 'bm cloud snapshot list' command."""

    def test_list_snapshots_success(self):
        """Test successful snapshot listing."""
        runner = CliRunner()

        mock_response = Mock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "snapshots": [
                {
                    "id": "snap_123",
                    "name": "snapshot-1",
                    "description": "first snapshot",
                    "auto": False,
                    "created_at": "2024-12-24T12:00:00Z",
                },
                {
                    "id": "snap_456",
                    "name": "daily-auto",
                    "description": "daily backup",
                    "auto": True,
                    "created_at": "2024-12-23T03:00:00Z",
                },
            ],
            "total": 2,
        }

        async def mock_make_api_request(*args, **kwargs):
            return mock_response

        with patch(
            "basic_memory.cli.commands.cloud.snapshot.make_api_request",
            side_effect=mock_make_api_request,
        ):
            mock_config = Mock()
            mock_config.cloud_host = "https://cloud.example.com"
            mock_config_manager = Mock()
            mock_config_manager.config = mock_config

            with patch(
                "basic_memory.cli.commands.cloud.snapshot.ConfigManager",
                return_value=mock_config_manager,
            ):
                result = runner.invoke(app, ["cloud", "snapshot", "list"])

                assert result.exit_code == 0
                assert "snap_123" in result.stdout
                assert "snap_456" in result.stdout
                assert "first snapshot" in result.stdout
                assert "daily backup" in result.stdout

    def test_list_snapshots_empty(self):
        """Test listing when no snapshots exist."""
        runner = CliRunner()

        mock_response = Mock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {"snapshots": [], "total": 0}

        async def mock_make_api_request(*args, **kwargs):
            return mock_response

        with patch(
            "basic_memory.cli.commands.cloud.snapshot.make_api_request",
            side_effect=mock_make_api_request,
        ):
            mock_config = Mock()
            mock_config.cloud_host = "https://cloud.example.com"
            mock_config_manager = Mock()
            mock_config_manager.config = mock_config

            with patch(
                "basic_memory.cli.commands.cloud.snapshot.ConfigManager",
                return_value=mock_config_manager,
            ):
                result = runner.invoke(app, ["cloud", "snapshot", "list"])

                assert result.exit_code == 0
                assert "No snapshots found" in result.stdout

    def test_list_snapshots_with_limit(self):
        """Test listing snapshots with custom limit."""
        runner = CliRunner()

        mock_response = Mock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "snapshots": [
                {
                    "id": f"snap_{i}",
                    "name": f"snap-{i}",
                    "auto": False,
                    "created_at": "2024-12-24T12:00:00Z",
                }
                for i in range(20)
            ],
            "total": 20,
        }

        async def mock_make_api_request(*args, **kwargs):
            return mock_response

        with patch(
            "basic_memory.cli.commands.cloud.snapshot.make_api_request",
            side_effect=mock_make_api_request,
        ):
            mock_config = Mock()
            mock_config.cloud_host = "https://cloud.example.com"
            mock_config_manager = Mock()
            mock_config_manager.config = mock_config

            with patch(
                "basic_memory.cli.commands.cloud.snapshot.ConfigManager",
                return_value=mock_config_manager,
            ):
                result = runner.invoke(app, ["cloud", "snapshot", "list", "--limit", "5"])

                assert result.exit_code == 0
                # Should show message about more snapshots available
                assert "Showing 5 of 20" in result.stdout


class TestSnapshotDeleteCommand:
    """Tests for 'bm cloud snapshot delete' command."""

    def test_delete_snapshot_success_with_force(self):
        """Test successful snapshot deletion with --force flag."""
        runner = CliRunner()

        mock_response = Mock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {}

        async def mock_make_api_request(*args, **kwargs):
            return mock_response

        with patch(
            "basic_memory.cli.commands.cloud.snapshot.make_api_request",
            side_effect=mock_make_api_request,
        ):
            mock_config = Mock()
            mock_config.cloud_host = "https://cloud.example.com"
            mock_config_manager = Mock()
            mock_config_manager.config = mock_config

            with patch(
                "basic_memory.cli.commands.cloud.snapshot.ConfigManager",
                return_value=mock_config_manager,
            ):
                result = runner.invoke(app, ["cloud", "snapshot", "delete", "snap_123", "--force"])

                assert result.exit_code == 0
                assert "deleted successfully" in result.stdout

    def test_delete_snapshot_not_found(self):
        """Test deletion of non-existent snapshot."""
        runner = CliRunner()

        async def mock_make_api_request(*args, **kwargs):
            raise CloudAPIError("Not found", status_code=404)

        with patch(
            "basic_memory.cli.commands.cloud.snapshot.make_api_request",
            side_effect=mock_make_api_request,
        ):
            mock_config = Mock()
            mock_config.cloud_host = "https://cloud.example.com"
            mock_config_manager = Mock()
            mock_config_manager.config = mock_config

            with patch(
                "basic_memory.cli.commands.cloud.snapshot.ConfigManager",
                return_value=mock_config_manager,
            ):
                result = runner.invoke(
                    app, ["cloud", "snapshot", "delete", "snap_nonexistent", "--force"]
                )

                assert result.exit_code == 1
                assert "Snapshot not found" in result.stdout

    def test_delete_snapshot_cancelled(self):
        """Test snapshot deletion cancelled by user."""
        runner = CliRunner()

        # Mock successful GET for snapshot details
        mock_get_response = Mock(spec=httpx.Response)
        mock_get_response.status_code = 200
        mock_get_response.json.return_value = {
            "id": "snap_123",
            "description": "test snapshot",
            "created_at": "2024-12-24T12:00:00Z",
        }

        call_count = 0

        async def mock_make_api_request(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            method = kwargs.get("method", args[0] if args else None)
            if method == "GET":
                return mock_get_response
            # Track unexpected calls
            return mock_get_response

        with patch(
            "basic_memory.cli.commands.cloud.snapshot.make_api_request",
            side_effect=mock_make_api_request,
        ):
            mock_config = Mock()
            mock_config.cloud_host = "https://cloud.example.com"
            mock_config_manager = Mock()
            mock_config_manager.config = mock_config

            with patch(
                "basic_memory.cli.commands.cloud.snapshot.ConfigManager",
                return_value=mock_config_manager,
            ):
                # Simulate user saying "n" to confirmation
                result = runner.invoke(
                    app, ["cloud", "snapshot", "delete", "snap_123"], input="n\n"
                )

                assert result.exit_code == 0
                assert "cancelled" in result.stdout
                # Only one call should happen (GET for details), not the DELETE
                assert call_count == 1


class TestSnapshotShowCommand:
    """Tests for 'bm cloud snapshot show' command."""

    def test_show_snapshot_success(self):
        """Test showing snapshot details."""
        runner = CliRunner()

        mock_response = Mock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "snap_123",
            "bucket_name": "tenant-abc",
            "snapshot_version": "1703430000000",
            "name": "test-snapshot",
            "description": "test description",
            "auto": False,
            "created_at": "2024-12-24T12:00:00Z",
        }

        async def mock_make_api_request(*args, **kwargs):
            return mock_response

        with patch(
            "basic_memory.cli.commands.cloud.snapshot.make_api_request",
            side_effect=mock_make_api_request,
        ):
            mock_config = Mock()
            mock_config.cloud_host = "https://cloud.example.com"
            mock_config_manager = Mock()
            mock_config_manager.config = mock_config

            with patch(
                "basic_memory.cli.commands.cloud.snapshot.ConfigManager",
                return_value=mock_config_manager,
            ):
                result = runner.invoke(app, ["cloud", "snapshot", "show", "snap_123"])

                assert result.exit_code == 0
                assert "snap_123" in result.stdout
                assert "tenant-abc" in result.stdout
                assert "test description" in result.stdout

    def test_show_snapshot_not_found(self):
        """Test showing non-existent snapshot."""
        runner = CliRunner()

        async def mock_make_api_request(*args, **kwargs):
            raise CloudAPIError("Not found", status_code=404)

        with patch(
            "basic_memory.cli.commands.cloud.snapshot.make_api_request",
            side_effect=mock_make_api_request,
        ):
            mock_config = Mock()
            mock_config.cloud_host = "https://cloud.example.com"
            mock_config_manager = Mock()
            mock_config_manager.config = mock_config

            with patch(
                "basic_memory.cli.commands.cloud.snapshot.ConfigManager",
                return_value=mock_config_manager,
            ):
                result = runner.invoke(app, ["cloud", "snapshot", "show", "snap_nonexistent"])

                assert result.exit_code == 1
                assert "Snapshot not found" in result.stdout


class TestSnapshotBrowseCommand:
    """Tests for 'bm cloud snapshot browse' command."""

    def test_browse_snapshot_success(self):
        """Test browsing snapshot contents."""
        runner = CliRunner()

        mock_response = Mock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "files": [
                {"key": "notes/project.md", "size": 1024, "last_modified": "2025-01-18T12:00:00Z"},
                {"key": "notes/ideas.md", "size": 2048, "last_modified": "2025-01-18T12:00:00Z"},
                {"key": "research/paper.md", "size": 4096, "last_modified": "2025-01-18T12:00:00Z"},
            ],
            "prefix": "",
            "snapshot_version": "12345",
        }

        async def mock_make_api_request(*args, **kwargs):
            return mock_response

        with patch(
            "basic_memory.cli.commands.cloud.snapshot.make_api_request",
            side_effect=mock_make_api_request,
        ):
            mock_config = Mock()
            mock_config.cloud_host = "https://cloud.example.com"
            mock_config_manager = Mock()
            mock_config_manager.config = mock_config

            with patch(
                "basic_memory.cli.commands.cloud.snapshot.ConfigManager",
                return_value=mock_config_manager,
            ):
                result = runner.invoke(app, ["cloud", "snapshot", "browse", "snap_123"])

                assert result.exit_code == 0
                assert "notes/project.md" in result.stdout
                assert "notes/ideas.md" in result.stdout
                assert "research/paper.md" in result.stdout

    def test_browse_snapshot_with_prefix(self):
        """Test browsing snapshot with prefix filter."""
        runner = CliRunner()

        mock_response = Mock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "files": [
                {"key": "notes/project.md", "size": 1024, "last_modified": "2025-01-18T12:00:00Z"},
                {"key": "notes/ideas.md", "size": 2048, "last_modified": "2025-01-18T12:00:00Z"},
            ],
            "prefix": "notes/",
            "snapshot_version": "12345",
        }

        async def mock_make_api_request(*args, **kwargs):
            # Verify prefix is in the URL
            url = args[1] if len(args) > 1 else kwargs.get("url", "")
            assert "prefix=notes/" in url
            return mock_response

        with patch(
            "basic_memory.cli.commands.cloud.snapshot.make_api_request",
            side_effect=mock_make_api_request,
        ):
            mock_config = Mock()
            mock_config.cloud_host = "https://cloud.example.com"
            mock_config_manager = Mock()
            mock_config_manager.config = mock_config

            with patch(
                "basic_memory.cli.commands.cloud.snapshot.ConfigManager",
                return_value=mock_config_manager,
            ):
                result = runner.invoke(
                    app, ["cloud", "snapshot", "browse", "snap_123", "--prefix", "notes/"]
                )

                assert result.exit_code == 0

    def test_browse_snapshot_empty(self):
        """Test browsing snapshot with no files."""
        runner = CliRunner()

        mock_response = Mock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "files": [],
            "prefix": "",
            "snapshot_version": "12345",
        }

        async def mock_make_api_request(*args, **kwargs):
            return mock_response

        with patch(
            "basic_memory.cli.commands.cloud.snapshot.make_api_request",
            side_effect=mock_make_api_request,
        ):
            mock_config = Mock()
            mock_config.cloud_host = "https://cloud.example.com"
            mock_config_manager = Mock()
            mock_config_manager.config = mock_config

            with patch(
                "basic_memory.cli.commands.cloud.snapshot.ConfigManager",
                return_value=mock_config_manager,
            ):
                result = runner.invoke(app, ["cloud", "snapshot", "browse", "snap_123"])

                assert result.exit_code == 0
                assert "No files found" in result.stdout
