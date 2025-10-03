"""Tests for bisync_commands module."""

from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from basic_memory.cli.commands.cloud.bisync_commands import (
    BisyncError,
    convert_bmignore_to_rclone_filters,
    scan_local_directories,
    validate_bisync_directory,
    build_bisync_command,
    get_bisync_directory,
    get_bisync_state_path,
    bisync_state_exists,
    BISYNC_PROFILES,
)


class TestConvertBmignoreToRcloneFilters:
    """Tests for convert_bmignore_to_rclone_filters()."""

    def test_converts_basic_patterns(self, tmp_path):
        """Test conversion of basic gitignore patterns to rclone format."""
        bmignore_dir = tmp_path / ".basic-memory"
        bmignore_dir.mkdir(exist_ok=True)
        bmignore_file = bmignore_dir / ".bmignore"

        # Write test patterns
        bmignore_file.write_text("# Comment line\nnode_modules\n*.pyc\n.git\n**/*.log\n")

        with patch(
            "basic_memory.cli.commands.cloud.bisync_commands.get_bmignore_path",
            return_value=bmignore_file,
        ):
            convert_bmignore_to_rclone_filters()

        # Read the generated rclone filter file
        rclone_filter = bmignore_dir / ".bmignore.rclone"
        assert rclone_filter.exists()

        content = rclone_filter.read_text()
        lines = content.strip().split("\n")

        # Check comment preserved
        assert "# Comment line" in lines

        # Check patterns converted correctly
        assert "- node_modules/**" in lines  # Directory without wildcard
        assert "- *.pyc" in lines  # Wildcard pattern unchanged
        assert "- .git/**" in lines  # Directory pattern
        assert "- **/*.log" in lines  # Wildcard pattern unchanged

    def test_handles_empty_bmignore(self, tmp_path):
        """Test handling of empty .bmignore file."""
        bmignore_dir = tmp_path / ".basic-memory"
        bmignore_dir.mkdir(exist_ok=True)
        bmignore_file = bmignore_dir / ".bmignore"
        bmignore_file.write_text("")

        with patch(
            "basic_memory.cli.commands.cloud.bisync_commands.get_bmignore_path",
            return_value=bmignore_file,
        ):
            convert_bmignore_to_rclone_filters()

        rclone_filter = bmignore_dir / ".bmignore.rclone"
        assert rclone_filter.exists()

    def test_handles_missing_bmignore(self, tmp_path):
        """Test handling when .bmignore doesn't exist."""
        bmignore_dir = tmp_path / ".basic-memory"
        bmignore_dir.mkdir(exist_ok=True)
        bmignore_file = bmignore_dir / ".bmignore"

        # Ensure file doesn't exist
        if bmignore_file.exists():
            bmignore_file.unlink()

        with patch(
            "basic_memory.cli.commands.cloud.bisync_commands.get_bmignore_path",
            return_value=bmignore_file,
        ):
            with patch("basic_memory.cli.commands.cloud.bisync_commands.create_default_bmignore"):
                convert_bmignore_to_rclone_filters()

        # Should create minimal filter with .git
        rclone_filter = bmignore_dir / ".bmignore.rclone"
        assert rclone_filter.exists()
        content = rclone_filter.read_text()
        assert "- .git/**" in content


class TestScanLocalDirectories:
    """Tests for scan_local_directories()."""

    def test_scans_existing_directories(self, tmp_path):
        """Test scanning existing project directories."""
        # Use a subdirectory to avoid interference from test fixtures
        scan_dir = tmp_path / "scan_test"
        scan_dir.mkdir()

        # Create test directories
        (scan_dir / "project1").mkdir()
        (scan_dir / "project2").mkdir()
        (scan_dir / "project3").mkdir()

        # Create a hidden directory (should be ignored)
        (scan_dir / ".hidden").mkdir()

        # Create a file (should be ignored)
        (scan_dir / "file.txt").write_text("test")

        result = scan_local_directories(scan_dir)

        assert len(result) == 3
        assert "project1" in result
        assert "project2" in result
        assert "project3" in result
        assert ".hidden" not in result

    def test_handles_empty_directory(self, tmp_path):
        """Test scanning empty directory."""
        scan_dir = tmp_path / "empty_test"
        scan_dir.mkdir()
        result = scan_local_directories(scan_dir)
        assert result == []

    def test_handles_nonexistent_directory(self, tmp_path):
        """Test scanning nonexistent directory."""
        nonexistent = tmp_path / "does-not-exist"
        result = scan_local_directories(nonexistent)
        assert result == []

    def test_ignores_hidden_directories(self, tmp_path):
        """Test that hidden directories are ignored."""
        scan_dir = tmp_path / "hidden_test"
        scan_dir.mkdir()

        (scan_dir / ".git").mkdir()
        (scan_dir / ".cache").mkdir()
        (scan_dir / "visible").mkdir()

        result = scan_local_directories(scan_dir)

        assert len(result) == 1
        assert "visible" in result
        assert ".git" not in result
        assert ".cache" not in result


class TestValidateBisyncDirectory:
    """Tests for validate_bisync_directory()."""

    def test_allows_valid_directory(self, tmp_path):
        """Test that valid directory passes validation."""
        bisync_dir = tmp_path / "sync"
        bisync_dir.mkdir()

        # Should not raise
        validate_bisync_directory(bisync_dir)

    def test_rejects_mount_directory(self, tmp_path):
        """Test that mount directory is rejected."""
        mount_dir = Path.home() / "basic-memory-cloud"

        with pytest.raises(BisyncError) as exc_info:
            validate_bisync_directory(mount_dir)

        assert "mount directory" in str(exc_info.value).lower()

    @patch("subprocess.run")
    def test_rejects_mounted_directory(self, mock_run, tmp_path):
        """Test that currently mounted directory is rejected."""
        bisync_dir = tmp_path / "sync"
        bisync_dir.mkdir()

        # Mock mount command showing this directory is mounted
        mock_run.return_value = Mock(
            stdout=f"rclone on {bisync_dir} type fuse.rclone",
            stderr="",
            returncode=0,
        )

        with pytest.raises(BisyncError) as exc_info:
            validate_bisync_directory(bisync_dir)

        assert "currently mounted" in str(exc_info.value).lower()


class TestBuildBisyncCommand:
    """Tests for build_bisync_command()."""

    def test_builds_basic_command(self, tmp_path):
        """Test building basic bisync command."""
        tenant_id = "test-tenant"
        bucket_name = "test-bucket"
        local_path = tmp_path / "sync"
        local_path.mkdir()
        profile = BISYNC_PROFILES["balanced"]

        with patch(
            "basic_memory.cli.commands.cloud.bisync_commands.get_bisync_filter_path"
        ) as mock_filter:
            mock_filter.return_value = Path("/test/filter")

            cmd = build_bisync_command(
                tenant_id=tenant_id,
                bucket_name=bucket_name,
                local_path=local_path,
                profile=profile,
            )

        assert cmd[0] == "rclone"
        assert cmd[1] == "bisync"
        assert str(local_path) in cmd
        assert f"basic-memory-{tenant_id}:{bucket_name}" in cmd
        assert "--create-empty-src-dirs" in cmd
        assert "--resilient" in cmd
        assert f"--conflict-resolve={profile.conflict_resolve}" in cmd
        assert f"--max-delete={profile.max_delete}" in cmd
        assert "--progress" in cmd

    def test_adds_dry_run_flag(self, tmp_path):
        """Test that dry-run flag is added when requested."""
        tenant_id = "test-tenant"
        bucket_name = "test-bucket"
        local_path = tmp_path / "sync"
        local_path.mkdir()
        profile = BISYNC_PROFILES["safe"]

        with patch(
            "basic_memory.cli.commands.cloud.bisync_commands.get_bisync_filter_path"
        ) as mock_filter:
            mock_filter.return_value = Path("/test/filter")

            cmd = build_bisync_command(
                tenant_id=tenant_id,
                bucket_name=bucket_name,
                local_path=local_path,
                profile=profile,
                dry_run=True,
            )

        assert "--dry-run" in cmd

    def test_adds_resync_flag(self, tmp_path):
        """Test that resync flag is added when requested."""
        tenant_id = "test-tenant"
        bucket_name = "test-bucket"
        local_path = tmp_path / "sync"
        local_path.mkdir()
        profile = BISYNC_PROFILES["balanced"]

        with patch(
            "basic_memory.cli.commands.cloud.bisync_commands.get_bisync_filter_path"
        ) as mock_filter:
            mock_filter.return_value = Path("/test/filter")

            cmd = build_bisync_command(
                tenant_id=tenant_id,
                bucket_name=bucket_name,
                local_path=local_path,
                profile=profile,
                resync=True,
            )

        assert "--resync" in cmd

    def test_adds_verbose_flag(self, tmp_path):
        """Test that verbose flag is added when requested."""
        tenant_id = "test-tenant"
        bucket_name = "test-bucket"
        local_path = tmp_path / "sync"
        local_path.mkdir()
        profile = BISYNC_PROFILES["fast"]

        with patch(
            "basic_memory.cli.commands.cloud.bisync_commands.get_bisync_filter_path"
        ) as mock_filter:
            mock_filter.return_value = Path("/test/filter")

            cmd = build_bisync_command(
                tenant_id=tenant_id,
                bucket_name=bucket_name,
                local_path=local_path,
                profile=profile,
                verbose=True,
            )

        assert "--verbose" in cmd
        assert "--progress" not in cmd  # Progress replaced by verbose

    def test_creates_state_directory(self, tmp_path):
        """Test that state directory is created."""
        tenant_id = "test-tenant"
        bucket_name = "test-bucket"
        local_path = tmp_path / "sync"
        local_path.mkdir()
        profile = BISYNC_PROFILES["balanced"]

        with patch(
            "basic_memory.cli.commands.cloud.bisync_commands.get_bisync_filter_path"
        ) as mock_filter:
            with patch(
                "basic_memory.cli.commands.cloud.bisync_commands.get_bisync_state_path"
            ) as mock_state:
                state_path = tmp_path / "state"
                mock_filter.return_value = Path("/test/filter")
                mock_state.return_value = state_path

                build_bisync_command(
                    tenant_id=tenant_id,
                    bucket_name=bucket_name,
                    local_path=local_path,
                    profile=profile,
                )

                # State directory should be created
                assert state_path.exists()
                assert state_path.is_dir()


class TestBisyncStateManagement:
    """Tests for bisync state functions."""

    def test_get_bisync_state_path(self):
        """Test state path generation."""
        tenant_id = "test-tenant-123"
        result = get_bisync_state_path(tenant_id)

        expected = Path.home() / ".basic-memory" / "bisync-state" / tenant_id
        assert result == expected

    def test_bisync_state_exists_true(self, tmp_path):
        """Test checking for existing state."""
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        (state_dir / "test.lst").write_text("test")

        with patch(
            "basic_memory.cli.commands.cloud.bisync_commands.get_bisync_state_path",
            return_value=state_dir,
        ):
            result = bisync_state_exists("test-tenant")

        assert result is True

    def test_bisync_state_exists_false_no_dir(self, tmp_path):
        """Test checking for nonexistent state directory."""
        state_dir = tmp_path / "nonexistent"

        with patch(
            "basic_memory.cli.commands.cloud.bisync_commands.get_bisync_state_path",
            return_value=state_dir,
        ):
            result = bisync_state_exists("test-tenant")

        assert result is False

    def test_bisync_state_exists_false_empty_dir(self, tmp_path):
        """Test checking for empty state directory."""
        state_dir = tmp_path / "state"
        state_dir.mkdir()

        with patch(
            "basic_memory.cli.commands.cloud.bisync_commands.get_bisync_state_path",
            return_value=state_dir,
        ):
            result = bisync_state_exists("test-tenant")

        assert result is False


class TestGetBisyncDirectory:
    """Tests for get_bisync_directory()."""

    def test_returns_default_directory(self):
        """Test that default directory is returned when not configured."""
        with patch("basic_memory.cli.commands.cloud.bisync_commands.ConfigManager") as mock_config:
            mock_config.return_value.config.bisync_config = {}

            result = get_bisync_directory()

            expected = Path.home() / "basic-memory-cloud-sync"
            assert result == expected

    def test_returns_configured_directory(self, tmp_path):
        """Test that configured directory is returned."""
        custom_dir = tmp_path / "custom-sync"

        with patch("basic_memory.cli.commands.cloud.bisync_commands.ConfigManager") as mock_config:
            mock_config.return_value.config.bisync_config = {"sync_dir": str(custom_dir)}

            result = get_bisync_directory()

            assert result == custom_dir


class TestCloudProjectAutoRegistration:
    """Tests for project auto-registration logic."""

    @pytest.mark.asyncio
    async def test_extracts_directory_names_from_cloud_paths(self):
        """Test extraction of directory names from cloud project paths."""
        from basic_memory.cli.commands.cloud.bisync_commands import fetch_cloud_projects

        with patch(
            "basic_memory.cli.commands.cloud.bisync_commands.make_api_request"
        ) as mock_request:
            mock_response = Mock()
            mock_response.json.return_value = {
                "projects": [
                    {"name": "Main Project", "path": "/app/data/basic-memory"},
                    {"name": "Work", "path": "/app/data/work-notes"},
                    {"name": "Personal", "path": "/app/data/personal"},
                ]
            }
            mock_request.return_value = mock_response

            result = await fetch_cloud_projects()

            # Extract directory names as the code does
            cloud_dir_names = set()
            for p in result.projects:
                path = p.path
                if path.startswith("/app/data/"):
                    path = path[len("/app/data/") :]
                dir_name = Path(path).name
                cloud_dir_names.add(dir_name)

            assert cloud_dir_names == {"basic-memory", "work-notes", "personal"}

    @pytest.mark.asyncio
    async def test_create_cloud_project_generates_permalink(self):
        """Test that create_cloud_project generates correct permalink."""
        from basic_memory.cli.commands.cloud.bisync_commands import create_cloud_project

        with patch(
            "basic_memory.cli.commands.cloud.bisync_commands.make_api_request"
        ) as mock_request:
            with patch(
                "basic_memory.cli.commands.cloud.bisync_commands.generate_permalink"
            ) as mock_permalink:
                mock_permalink.return_value = "my-new-project"
                mock_response = Mock()
                mock_response.json.return_value = {
                    "name": "My New Project",
                    "path": "my-new-project",
                    "message": "Created",
                }
                mock_request.return_value = mock_response

                await create_cloud_project("My New Project")

                # Verify permalink was generated
                mock_permalink.assert_called_once_with("My New Project")

                # Verify request was made with correct data
                call_args = mock_request.call_args
                json_data = call_args.kwargs["json_data"]
                assert json_data["name"] == "My New Project"
                assert json_data["path"] == "my-new-project"
                assert json_data["set_default"] is False
