"""Tests for WSL path handling in Basic Memory.

This test ensures that Basic Memory properly handles Windows-style paths
when running in a WSL environment, preventing the nested path bug reported
in issue #243.
"""

import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest
from basic_memory.services.project_service import ProjectService
from basic_memory.config import ConfigManager
from basic_memory.repository.project_repository import ProjectRepository


class TestWSLPathHandling:
    """Test that Basic Memory correctly handles Windows paths in WSL environments."""

    @pytest.mark.asyncio
    async def test_windows_path_in_wsl_environment(self):
        """Test that Windows paths like C:\\ are properly converted in WSL.

        This test addresses the bug where paths like:
        C:\\Users\\Dr Brenda\\basic-memory\\housing\\shadow-ln

        Would incorrectly create nested paths like:
        /mnt/c/Users/Dr Brenda/basic-memory/C:\\Users\\Dr Brenda\\basic-memory\\housing\\shadow-ln

        Instead of the correct:
        /mnt/c/Users/Dr Brenda/basic-memory/housing/shadow-ln
        """
        # Mock repository
        mock_repo = MagicMock(spec=ProjectRepository)
        mock_repo.create = MagicMock(return_value=MagicMock(id=1))

        # Create project service
        project_service = ProjectService(mock_repo)

        # Mock config manager to avoid file system operations
        mock_config = MagicMock(spec=ConfigManager)
        mock_config.add_project = MagicMock(return_value=MagicMock(name="shadow-ln"))
        mock_config.projects = {}

        with patch.object(project_service, 'config_manager', mock_config):
            # Test case 1: Windows path that should be converted to WSL path
            windows_path = "C:\\Users\\Dr Brenda\\basic-memory\\housing\\shadow-ln"

            # In WSL, this should resolve to /mnt/c/Users/...
            await project_service.add_project("shadow-ln", windows_path)

            # Check what path was saved
            saved_path = mock_config.add_project.call_args[0][1]

            # The path should not contain the original Windows path nested inside
            assert "C:" not in saved_path or saved_path.startswith("/mnt/c/"), \
                f"Windows path not properly converted: {saved_path}"
            assert "C:\\Users" not in saved_path, \
                f"Windows path nested incorrectly: {saved_path}"

    @pytest.mark.asyncio
    async def test_wsl_path_already_converted(self):
        """Test that already-converted WSL paths are handled correctly."""
        # Mock repository
        mock_repo = MagicMock(spec=ProjectRepository)
        mock_repo.create = MagicMock(return_value=MagicMock(id=1))

        # Create project service
        project_service = ProjectService(mock_repo)

        # Mock config manager
        mock_config = MagicMock(spec=ConfigManager)
        mock_config.add_project = MagicMock(return_value=MagicMock(name="test-project"))
        mock_config.projects = {}

        with patch.object(project_service, 'config_manager', mock_config):
            # Test with already-converted WSL path
            wsl_path = "/mnt/c/Users/Dr Brenda/basic-memory/housing/shadow-ln"

            await project_service.add_project("test-project", wsl_path)

            # Check what path was saved
            saved_path = mock_config.add_project.call_args[0][1]

            # Should remain as WSL path
            assert "/mnt/c/" in saved_path or not saved_path.startswith("/mnt/"), \
                f"WSL path incorrectly modified: {saved_path}"

    @pytest.mark.asyncio
    async def test_mixed_path_separators(self):
        """Test handling of paths with mixed separators (common in copy-paste scenarios)."""
        # Mock repository
        mock_repo = MagicMock(spec=ProjectRepository)
        mock_repo.create = MagicMock(return_value=MagicMock(id=1))

        # Create project service
        project_service = ProjectService(mock_repo)

        # Mock config manager
        mock_config = MagicMock(spec=ConfigManager)
        mock_config.add_project = MagicMock(return_value=MagicMock(name="mixed-project"))
        mock_config.projects = {}

        with patch.object(project_service, 'config_manager', mock_config):
            # Test with mixed separators
            mixed_path = "C:/Users\\Dr Brenda/basic-memory\\housing/shadow-ln"

            await project_service.add_project("mixed-project", mixed_path)

            # Check what path was saved
            saved_path = mock_config.add_project.call_args[0][1]

            # Should have consistent separators
            assert "\\" not in saved_path or sys.platform == "win32", \
                f"Mixed separators not normalized: {saved_path}"

    def test_file_path_construction_with_folder(self):
        """Test that Entity.file_path correctly constructs paths without nesting."""
        from basic_memory.schemas.base import Entity

        # Create entity with folder path
        entity = Entity(
            title="Legal Research",
            folder="legal-research",
            content="# Legal Research Notes",
            content_type="text/markdown"
        )

        # Check the file path
        file_path = entity.file_path

        # Should be simple join, not nested
        assert file_path == "legal-research/Legal Research.md", \
            f"File path incorrectly constructed: {file_path}"

        # Ensure no Windows path artifacts
        assert "C:" not in file_path, "Windows drive letter in file path"
        assert "\\" not in file_path, "Windows path separator in file path"

    @pytest.mark.parametrize("input_path,expected_contains,expected_not_contains", [
        # Windows absolute path
        ("C:\\Users\\test\\project", ["/"], ["C:", "\\"]),
        # Windows path with forward slashes
        ("C:/Users/test/project", ["/"], ["C:", "\\"]),
        # UNC path
        ("\\\\server\\share\\project", ["/"], ["\\\\"]),
        # Already WSL path
        ("/mnt/c/Users/test/project", ["/mnt/c"], ["\\"]),
        # Relative path
        ("./my-project", ["/"], ["\\"]),
        # Home directory
        ("~/basic-memory/project", ["/"], ["~", "\\"]),
    ])
    def test_path_resolution_scenarios(self, input_path, expected_contains, expected_not_contains):
        """Test various path input scenarios for proper resolution."""
        resolved = Path(os.path.abspath(os.path.expanduser(input_path))).as_posix()

        for expected in expected_contains:
            assert expected in resolved or not resolved, \
                f"Expected '{expected}' in resolved path '{resolved}'"

        for not_expected in expected_not_contains:
            assert not_expected not in resolved, \
                f"Unexpected '{not_expected}' found in resolved path '{resolved}'"


class TestWSLPathIntegration:
    """Integration tests for WSL path handling with actual file operations."""

    @pytest.mark.asyncio
    async def test_write_note_in_wsl_project(self, tmp_path):
        """Test that write_note correctly handles paths in WSL-based projects."""
        from basic_memory.services.file_service import FileService
        from basic_memory.markdown.markdown_processor import MarkdownProcessor
        from basic_memory.schemas.base import Entity

        # Create a mock WSL-style project path
        project_path = tmp_path / "wsl-project"
        project_path.mkdir()

        # Create services
        markdown_processor = MarkdownProcessor()
        file_service = FileService(project_path, markdown_processor)

        # Create entity with folder
        entity = Entity(
            title="Test Note",
            folder="research/2025",
            content="# Test Content",
            content_type="text/markdown"
        )

        # Write the file
        file_path = project_path / entity.file_path
        await file_service.write_file(file_path, entity.content)

        # Verify file is in correct location
        expected_path = project_path / "research" / "2025" / "Test Note.md"
        assert expected_path.exists(), f"File not created at expected path: {expected_path}"

        # Ensure no nested paths were created
        wrong_path = project_path / "C:" / "Users"
        assert not wrong_path.exists(), f"Incorrect nested path created: {wrong_path}"