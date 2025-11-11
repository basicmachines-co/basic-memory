"""Tests for upload module."""

from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest

from basic_memory.cli.commands.cloud.upload import _get_files_to_upload, upload_path


class TestGetFilesToUpload:
    """Tests for _get_files_to_upload()."""

    def test_collects_files_from_directory(self, tmp_path):
        """Test collecting files from a directory."""
        # Create test directory structure
        (tmp_path / "file1.txt").write_text("content1")
        (tmp_path / "file2.md").write_text("content2")
        (tmp_path / "subdir").mkdir()
        (tmp_path / "subdir" / "file3.py").write_text("content3")

        # Call with real ignore utils (no mocking)
        result = _get_files_to_upload(tmp_path, verbose=False, use_gitignore=True)

        # Should find all 3 files
        assert len(result) == 3

        # Extract just the relative paths for easier assertion
        relative_paths = [rel_path for _, rel_path in result]
        assert "file1.txt" in relative_paths
        assert "file2.md" in relative_paths
        assert "subdir/file3.py" in relative_paths

    def test_respects_gitignore_patterns(self, tmp_path):
        """Test that gitignore patterns are respected."""
        # Create test files
        (tmp_path / "keep.txt").write_text("keep")
        (tmp_path / "ignore.pyc").write_text("ignore")

        # Create .gitignore file
        gitignore_file = tmp_path / ".gitignore"
        gitignore_file.write_text("*.pyc\n")

        result = _get_files_to_upload(tmp_path)

        # Should only find keep.txt (not .pyc or .gitignore itself)
        relative_paths = [rel_path for _, rel_path in result]
        assert "keep.txt" in relative_paths
        assert "ignore.pyc" not in relative_paths

    def test_handles_empty_directory(self, tmp_path):
        """Test handling of empty directory."""
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()

        result = _get_files_to_upload(empty_dir)

        assert result == []

    def test_converts_windows_paths_to_forward_slashes(self, tmp_path):
        """Test that Windows backslashes are converted to forward slashes."""
        # Create nested structure
        (tmp_path / "dir1").mkdir()
        (tmp_path / "dir1" / "dir2").mkdir()
        (tmp_path / "dir1" / "dir2" / "file.txt").write_text("content")

        result = _get_files_to_upload(tmp_path)

        # Remote path should use forward slashes
        _, remote_path = result[0]
        assert "\\" not in remote_path  # No backslashes
        assert "dir1/dir2/file.txt" == remote_path


class TestUploadPath:
    """Tests for upload_path()."""

    @pytest.mark.asyncio
    async def test_uploads_single_file(self, tmp_path):
        """Test uploading a single file."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("test content")

        # Mock the client and HTTP response
        mock_client = AsyncMock()
        mock_response = Mock()
        mock_response.raise_for_status = Mock()

        with patch("basic_memory.cli.commands.cloud.upload.get_client") as mock_get_client:
            with patch("basic_memory.cli.commands.cloud.upload.call_put") as mock_put:
                with patch("aiofiles.open", create=True) as mock_aiofiles_open:
                    # Setup mocks
                    mock_get_client.return_value.__aenter__.return_value = mock_client
                    mock_get_client.return_value.__aexit__.return_value = None
                    mock_put.return_value = mock_response

                    # Mock file reading
                    mock_file = AsyncMock()
                    mock_file.read.return_value = b"test content"
                    mock_aiofiles_open.return_value.__aenter__.return_value = mock_file

                    result = await upload_path(test_file, "test-project")

        # Verify success
        assert result is True

        # Verify PUT was called with correct path
        mock_put.assert_called_once()
        call_args = mock_put.call_args
        assert call_args[0][0] == mock_client
        assert call_args[0][1] == "/webdav/test-project/test.txt"
        assert call_args[1]["content"] == b"test content"

    @pytest.mark.asyncio
    async def test_uploads_directory(self, tmp_path):
        """Test uploading a directory with multiple files."""
        # Create test files
        (tmp_path / "file1.txt").write_text("content1")
        (tmp_path / "file2.txt").write_text("content2")

        mock_client = AsyncMock()
        mock_response = Mock()
        mock_response.raise_for_status = Mock()

        with patch("basic_memory.cli.commands.cloud.upload.get_client") as mock_get_client:
            with patch("basic_memory.cli.commands.cloud.upload.call_put") as mock_put:
                with patch(
                    "basic_memory.cli.commands.cloud.upload._get_files_to_upload"
                ) as mock_get_files:
                    with patch("aiofiles.open", create=True) as mock_aiofiles_open:
                        # Setup mocks
                        mock_get_client.return_value.__aenter__.return_value = mock_client
                        mock_get_client.return_value.__aexit__.return_value = None
                        mock_put.return_value = mock_response

                        # Mock file listing
                        mock_get_files.return_value = [
                            (tmp_path / "file1.txt", "file1.txt"),
                            (tmp_path / "file2.txt", "file2.txt"),
                        ]

                        # Mock file reading
                        mock_file = AsyncMock()
                        mock_file.read.side_effect = [b"content1", b"content2"]
                        mock_aiofiles_open.return_value.__aenter__.return_value = mock_file

                        result = await upload_path(tmp_path, "test-project")

        # Verify success
        assert result is True

        # Verify PUT was called twice
        assert mock_put.call_count == 2

    @pytest.mark.asyncio
    async def test_handles_nonexistent_path(self, tmp_path):
        """Test handling of nonexistent path."""
        nonexistent = tmp_path / "does-not-exist"

        result = await upload_path(nonexistent, "test-project")

        # Should return False
        assert result is False

    @pytest.mark.asyncio
    async def test_handles_http_error(self, tmp_path):
        """Test handling of HTTP errors during upload."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("test content")

        mock_client = AsyncMock()
        mock_response = Mock()
        mock_response.status_code = 403
        mock_response.text = "Forbidden"
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Forbidden", request=Mock(), response=mock_response
        )

        with patch("basic_memory.cli.commands.cloud.upload.get_client") as mock_get_client:
            with patch("basic_memory.cli.commands.cloud.upload.call_put") as mock_put:
                with patch("aiofiles.open", create=True) as mock_aiofiles_open:
                    # Setup mocks
                    mock_get_client.return_value.__aenter__.return_value = mock_client
                    mock_get_client.return_value.__aexit__.return_value = None
                    mock_put.return_value = mock_response

                    # Mock file reading
                    mock_file = AsyncMock()
                    mock_file.read.return_value = b"test content"
                    mock_aiofiles_open.return_value.__aenter__.return_value = mock_file

                    result = await upload_path(test_file, "test-project")

        # Should return False on error
        assert result is False

    @pytest.mark.asyncio
    async def test_handles_empty_directory(self, tmp_path):
        """Test uploading an empty directory."""
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()

        with patch("basic_memory.cli.commands.cloud.upload._get_files_to_upload") as mock_get_files:
            mock_get_files.return_value = []

            result = await upload_path(empty_dir, "test-project")

        # Should return True (no-op success)
        assert result is True

    @pytest.mark.asyncio
    async def test_formats_file_size_bytes(self, tmp_path, capsys):
        """Test file size formatting for small files (bytes)."""
        test_file = tmp_path / "small.txt"
        test_file.write_text("hi")  # 2 bytes

        mock_client = AsyncMock()
        mock_response = Mock()
        mock_response.raise_for_status = Mock()

        with patch("basic_memory.cli.commands.cloud.upload.get_client") as mock_get_client:
            with patch("basic_memory.cli.commands.cloud.upload.call_put") as mock_put:
                with patch("aiofiles.open", create=True) as mock_aiofiles_open:
                    mock_get_client.return_value.__aenter__.return_value = mock_client
                    mock_get_client.return_value.__aexit__.return_value = None
                    mock_put.return_value = mock_response

                    mock_file = AsyncMock()
                    mock_file.read.return_value = b"hi"
                    mock_aiofiles_open.return_value.__aenter__.return_value = mock_file

                    await upload_path(test_file, "test-project")

        # Check output contains "bytes"
        captured = capsys.readouterr()
        assert "bytes" in captured.out

    @pytest.mark.asyncio
    async def test_formats_file_size_kilobytes(self, tmp_path, capsys):
        """Test file size formatting for medium files (KB)."""
        test_file = tmp_path / "medium.txt"
        # Create file with 2KB of content
        test_file.write_text("x" * 2048)

        mock_client = AsyncMock()
        mock_response = Mock()
        mock_response.raise_for_status = Mock()

        with patch("basic_memory.cli.commands.cloud.upload.get_client") as mock_get_client:
            with patch("basic_memory.cli.commands.cloud.upload.call_put") as mock_put:
                with patch("aiofiles.open", create=True) as mock_aiofiles_open:
                    mock_get_client.return_value.__aenter__.return_value = mock_client
                    mock_get_client.return_value.__aexit__.return_value = None
                    mock_put.return_value = mock_response

                    mock_file = AsyncMock()
                    mock_file.read.return_value = b"x" * 2048
                    mock_aiofiles_open.return_value.__aenter__.return_value = mock_file

                    await upload_path(test_file, "test-project")

        # Check output contains "KB"
        captured = capsys.readouterr()
        assert "KB" in captured.out

    @pytest.mark.asyncio
    async def test_formats_file_size_megabytes(self, tmp_path, capsys):
        """Test file size formatting for large files (MB)."""
        test_file = tmp_path / "large.txt"
        # Create file with 2MB of content
        test_file.write_text("x" * (2 * 1024 * 1024))

        mock_client = AsyncMock()
        mock_response = Mock()
        mock_response.raise_for_status = Mock()

        with patch("basic_memory.cli.commands.cloud.upload.get_client") as mock_get_client:
            with patch("basic_memory.cli.commands.cloud.upload.call_put") as mock_put:
                with patch("aiofiles.open", create=True) as mock_aiofiles_open:
                    mock_get_client.return_value.__aenter__.return_value = mock_client
                    mock_get_client.return_value.__aexit__.return_value = None
                    mock_put.return_value = mock_response

                    mock_file = AsyncMock()
                    mock_file.read.return_value = b"x" * (2 * 1024 * 1024)
                    mock_aiofiles_open.return_value.__aenter__.return_value = mock_file

                    await upload_path(test_file, "test-project")

        # Check output contains "MB"
        captured = capsys.readouterr()
        assert "MB" in captured.out

    @pytest.mark.asyncio
    async def test_builds_correct_webdav_path(self, tmp_path):
        """Test that WebDAV path is correctly constructed."""
        # Create nested structure
        (tmp_path / "subdir").mkdir()
        test_file = tmp_path / "subdir" / "file.txt"
        test_file.write_text("content")

        mock_client = AsyncMock()
        mock_response = Mock()
        mock_response.raise_for_status = Mock()

        with patch("basic_memory.cli.commands.cloud.upload.get_client") as mock_get_client:
            with patch("basic_memory.cli.commands.cloud.upload.call_put") as mock_put:
                with patch(
                    "basic_memory.cli.commands.cloud.upload._get_files_to_upload"
                ) as mock_get_files:
                    with patch("aiofiles.open", create=True) as mock_aiofiles_open:
                        mock_get_client.return_value.__aenter__.return_value = mock_client
                        mock_get_client.return_value.__aexit__.return_value = None
                        mock_put.return_value = mock_response

                        # Mock file listing with relative path
                        mock_get_files.return_value = [(test_file, "subdir/file.txt")]

                        mock_file = AsyncMock()
                        mock_file.read.return_value = b"content"
                        mock_aiofiles_open.return_value.__aenter__.return_value = mock_file

                        await upload_path(tmp_path, "my-project")

        # Verify WebDAV path format: /webdav/{project_name}/{relative_path}
        mock_put.assert_called_once()
        call_args = mock_put.call_args
        assert call_args[0][1] == "/webdav/my-project/subdir/file.txt"

    @pytest.mark.asyncio
    async def test_skips_archive_files(self, tmp_path, capsys):
        """Test that archive files are skipped during upload."""
        # Create test files including archives
        (tmp_path / "notes.md").write_text("content")
        (tmp_path / "backup.zip").write_text("fake zip")
        (tmp_path / "data.tar.gz").write_text("fake tar")

        mock_client = AsyncMock()
        mock_response = Mock()
        mock_response.raise_for_status = Mock()

        with patch("basic_memory.cli.commands.cloud.upload.get_client") as mock_get_client:
            with patch("basic_memory.cli.commands.cloud.upload.call_put") as mock_put:
                with patch(
                    "basic_memory.cli.commands.cloud.upload._get_files_to_upload"
                ) as mock_get_files:
                    with patch("aiofiles.open", create=True) as mock_aiofiles_open:
                        mock_get_client.return_value.__aenter__.return_value = mock_client
                        mock_get_client.return_value.__aexit__.return_value = None
                        mock_put.return_value = mock_response

                        # Mock file listing with all files
                        mock_get_files.return_value = [
                            (tmp_path / "notes.md", "notes.md"),
                            (tmp_path / "backup.zip", "backup.zip"),
                            (tmp_path / "data.tar.gz", "data.tar.gz"),
                        ]

                        mock_file = AsyncMock()
                        mock_file.read.return_value = b"content"
                        mock_aiofiles_open.return_value.__aenter__.return_value = mock_file

                        result = await upload_path(tmp_path, "test-project")

        # Should succeed
        assert result is True

        # Should only upload the .md file (not the archives)
        assert mock_put.call_count == 1
        call_args = mock_put.call_args
        assert "notes.md" in call_args[0][1]

        # Check output mentions skipping
        captured = capsys.readouterr()
        assert "Skipping archive file" in captured.out
        assert "backup.zip" in captured.out
        assert "Skipped 2 archive file(s)" in captured.out

    def test_no_gitignore_skips_gitignore_patterns(self, tmp_path):
        """Test that --no-gitignore flag skips .gitignore patterns."""
        # Create test files
        (tmp_path / "keep.txt").write_text("keep")
        (tmp_path / "secret.bak").write_text("secret")  # Use .bak instead of .pyc

        # Create .gitignore file that ignores .bak files
        gitignore_file = tmp_path / ".gitignore"
        gitignore_file.write_text("*.bak\n")

        # With use_gitignore=False, should include .bak files
        result = _get_files_to_upload(tmp_path, verbose=False, use_gitignore=False)

        # Extract relative paths
        relative_paths = [rel_path for _, rel_path in result]

        # Both files should be included when gitignore is disabled
        assert "keep.txt" in relative_paths
        assert "secret.bak" in relative_paths

    def test_no_gitignore_still_respects_bmignore(self, tmp_path):
        """Test that --no-gitignore still respects .bmignore patterns."""
        # Create test files
        (tmp_path / "keep.txt").write_text("keep")
        (tmp_path / ".hidden").write_text(
            "hidden"
        )  # Should be ignored by .bmignore default pattern

        # Create .gitignore that would allow .hidden
        gitignore_file = tmp_path / ".gitignore"
        gitignore_file.write_text("# Allow all\n")

        # With use_gitignore=False, should still filter hidden files via .bmignore
        result = _get_files_to_upload(tmp_path, verbose=False, use_gitignore=False)

        # Extract relative paths
        relative_paths = [rel_path for _, rel_path in result]

        # keep.txt should be included, .hidden should be filtered by .bmignore
        assert "keep.txt" in relative_paths
        assert ".hidden" not in relative_paths

    def test_verbose_shows_filtering_info(self, tmp_path, capsys):
        """Test that verbose mode shows filtering information."""
        # Create test files
        (tmp_path / "keep.txt").write_text("keep")
        (tmp_path / "ignore.pyc").write_text("ignore")

        # Create .gitignore
        gitignore_file = tmp_path / ".gitignore"
        gitignore_file.write_text("*.pyc\n")

        # Run with verbose=True
        _get_files_to_upload(tmp_path, verbose=True, use_gitignore=True)

        # Capture output
        captured = capsys.readouterr()

        # Should show scanning information
        assert "Scanning directory:" in captured.out
        assert "Using .bmignore: Yes" in captured.out
        assert "Using .gitignore:" in captured.out
        assert "Ignore patterns loaded:" in captured.out

        # Should show file status
        assert "[INCLUDE]" in captured.out or "[IGNORED]" in captured.out

        # Should show summary
        assert "Summary:" in captured.out
        assert "Files to upload:" in captured.out
        assert "Files ignored:" in captured.out

    def test_wildcard_gitignore_filters_all_files(self, tmp_path):
        """Test that a wildcard * in .gitignore filters all files."""
        # Create test files
        (tmp_path / "file1.txt").write_text("content1")
        (tmp_path / "file2.md").write_text("content2")

        # Create .gitignore with wildcard
        gitignore_file = tmp_path / ".gitignore"
        gitignore_file.write_text("*\n")

        # Should filter all files
        result = _get_files_to_upload(tmp_path, verbose=False, use_gitignore=True)
        assert len(result) == 0

        # With use_gitignore=False, should include files
        result = _get_files_to_upload(tmp_path, verbose=False, use_gitignore=False)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_dry_run_shows_files_without_uploading(self, tmp_path, capsys):
        """Test that --dry-run shows what would be uploaded without uploading."""
        # Create test files
        (tmp_path / "file1.txt").write_text("content1")
        (tmp_path / "file2.txt").write_text("content2")

        # Don't mock anything - we want to verify no actual upload happens
        result = await upload_path(tmp_path, "test-project", dry_run=True)

        # Should return success
        assert result is True

        # Check output shows dry run info
        captured = capsys.readouterr()
        assert "Found 2 file(s) to upload" in captured.out
        assert "Files that would be uploaded:" in captured.out
        assert "file1.txt" in captured.out
        assert "file2.txt" in captured.out
        assert "Total:" in captured.out

    @pytest.mark.asyncio
    async def test_dry_run_with_verbose(self, tmp_path, capsys):
        """Test that --dry-run works with --verbose."""
        # Create test files
        (tmp_path / "keep.txt").write_text("keep")
        (tmp_path / "ignore.pyc").write_text("ignore")

        # Create .gitignore
        gitignore_file = tmp_path / ".gitignore"
        gitignore_file.write_text("*.pyc\n")

        result = await upload_path(tmp_path, "test-project", verbose=True, dry_run=True)

        # Should return success
        assert result is True

        # Check output shows both verbose and dry run info
        captured = capsys.readouterr()
        assert "Scanning directory:" in captured.out
        assert "[INCLUDE] keep.txt" in captured.out
        assert "[IGNORED] ignore.pyc" in captured.out
        assert "Files that would be uploaded:" in captured.out
        assert "keep.txt" in captured.out
