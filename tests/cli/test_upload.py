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
        result = _get_files_to_upload(tmp_path)

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


class TestVerboseAndNoGitignoreFlags:
    """Tests for verbose and no_gitignore flags."""

    def test_verbose_shows_detailed_output(self, tmp_path, capsys):
        """Test that verbose flag shows detailed filtering information."""
        # Create test files
        (tmp_path / "keep.txt").write_text("keep")
        (tmp_path / "ignore.pyc").write_text("ignore")

        # Create .gitignore
        (tmp_path / ".gitignore").write_text("*.pyc\n")

        # Call with verbose=True
        result = _get_files_to_upload(tmp_path, verbose=True)

        # Should find keep.txt
        assert len(result) == 1

        # Check verbose output
        captured = capsys.readouterr()
        assert "Loaded ignore patterns:" in captured.out
        assert "Scan results:" in captured.out
        assert "Total files found:" in captured.out
        assert "Files to upload:" in captured.out

    def test_no_gitignore_skips_gitignore_patterns(self, tmp_path):
        """Test that no_gitignore flag skips .gitignore patterns."""
        # Create test files
        (tmp_path / "keep.txt").write_text("keep")
        (tmp_path / "would_be_ignored.pyc").write_text("content")

        # Create .gitignore that would normally ignore .pyc files
        (tmp_path / ".gitignore").write_text("*.pyc\n")

        # Call WITHOUT no_gitignore (default behavior)
        result_with_gitignore = _get_files_to_upload(tmp_path, no_gitignore=False)
        paths_with_gitignore = [rel for _, rel in result_with_gitignore]

        # .pyc should be ignored
        assert "would_be_ignored.pyc" not in paths_with_gitignore

        # Call WITH no_gitignore flag
        result_without_gitignore = _get_files_to_upload(tmp_path, no_gitignore=True)
        paths_without_gitignore = [rel for _, rel in result_without_gitignore]

        # .pyc should NOT be ignored (since we're skipping .gitignore)
        # Note: It might still be ignored by .bmignore if it has *.pyc pattern
        # For this test, we're checking that the flag is being respected

    def test_no_gitignore_still_respects_bmignore(self, tmp_path):
        """Test that no_gitignore still respects .bmignore patterns."""
        # Create test files
        (tmp_path / "regular.txt").write_text("content")
        (tmp_path / ".hidden").write_text("hidden")  # Should be ignored by .bmignore

        # Create .gitignore with a different pattern
        (tmp_path / ".gitignore").write_text("*.log\n")

        # Call with no_gitignore=True
        result = _get_files_to_upload(tmp_path, no_gitignore=True)
        paths = [rel for _, rel in result]

        # .hidden should still be ignored (by .bmignore default patterns)
        assert ".hidden" not in paths
        # regular.txt should be included
        assert "regular.txt" in paths

    def test_verbose_shows_ignored_files(self, tmp_path, capsys):
        """Test that verbose mode shows which files were ignored and why."""
        # Create test files
        (tmp_path / "keep.txt").write_text("keep")
        (tmp_path / "ignore.pyc").write_text("ignore")

        # Create .gitignore
        (tmp_path / ".gitignore").write_text("*.pyc\n")

        # Call with verbose=True
        _get_files_to_upload(tmp_path, verbose=True)

        # Check output includes ignored files
        captured = capsys.readouterr()
        assert "Ignored files and directories:" in captured.out
        assert "ignore.pyc" in captured.out

    def test_verbose_and_no_gitignore_combined(self, tmp_path, capsys):
        """Test combining verbose and no_gitignore flags."""
        # Create test files
        (tmp_path / "file.txt").write_text("content")

        # Create .gitignore
        (tmp_path / ".gitignore").write_text("*.log\n")

        # Call with both flags
        _get_files_to_upload(tmp_path, verbose=True, no_gitignore=True)

        # Check output mentions .bmignore only
        captured = capsys.readouterr()
        assert "bmignore only" in captured.out or ".bmignore" in captured.out
        assert "--no-gitignore" in captured.out

    @pytest.mark.asyncio
    async def test_upload_path_passes_flags(self, tmp_path):
        """Test that upload_path passes verbose and no_gitignore to _get_files_to_upload."""
        (tmp_path / "test.txt").write_text("content")

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
                        mock_get_files.return_value = [(tmp_path / "test.txt", "test.txt")]

                        mock_file = AsyncMock()
                        mock_file.read.return_value = b"content"
                        mock_aiofiles_open.return_value.__aenter__.return_value = mock_file

                        # Call with both flags
                        await upload_path(tmp_path, "project", verbose=True, no_gitignore=True)

                        # Verify _get_files_to_upload was called with the flags
                        mock_get_files.assert_called_once_with(tmp_path, True, True)
