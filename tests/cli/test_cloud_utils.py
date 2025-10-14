"""Tests for cloud_utils module."""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from basic_memory.cli.commands.cloud.cloud_utils import (
    CloudUtilsError,
    create_cloud_project,
    fetch_cloud_projects,
    project_exists,
    sync_project,
)


class TestFetchCloudProjects:
    """Tests for fetch_cloud_projects()."""

    @pytest.mark.asyncio
    async def test_fetches_projects_successfully(self):
        """Test successful fetch of cloud projects."""
        with patch("basic_memory.cli.commands.cloud.cloud_utils.make_api_request") as mock_request:
            with patch("basic_memory.cli.commands.cloud.cloud_utils.ConfigManager") as mock_config:
                # Setup config
                mock_config.return_value.config.cloud_host = "https://example.com"

                # Mock API response
                mock_response = Mock()
                mock_response.json.return_value = {
                    "projects": [
                        {"name": "Project 1", "path": "/app/data/project-1"},
                        {"name": "Project 2", "path": "/app/data/project-2"},
                    ]
                }
                mock_request.return_value = mock_response

                result = await fetch_cloud_projects()

        # Verify result
        assert len(result.projects) == 2
        assert result.projects[0].name == "Project 1"
        assert result.projects[1].name == "Project 2"

        # Verify API was called correctly
        mock_request.assert_called_once_with(
            method="GET", url="https://example.com/proxy/projects/projects"
        )

    @pytest.mark.asyncio
    async def test_strips_trailing_slash_from_host(self):
        """Test that trailing slash is stripped from cloud_host."""
        with patch("basic_memory.cli.commands.cloud.cloud_utils.make_api_request") as mock_request:
            with patch("basic_memory.cli.commands.cloud.cloud_utils.ConfigManager") as mock_config:
                # Setup config with trailing slash
                mock_config.return_value.config.cloud_host = "https://example.com/"

                mock_response = Mock()
                mock_response.json.return_value = {"projects": []}
                mock_request.return_value = mock_response

                await fetch_cloud_projects()

        # Verify trailing slash was removed
        call_args = mock_request.call_args
        assert call_args[1]["url"] == "https://example.com/proxy/projects/projects"

    @pytest.mark.asyncio
    async def test_raises_error_on_api_failure(self):
        """Test that CloudUtilsError is raised on API failure."""
        with patch("basic_memory.cli.commands.cloud.cloud_utils.make_api_request") as mock_request:
            with patch("basic_memory.cli.commands.cloud.cloud_utils.ConfigManager") as mock_config:
                mock_config.return_value.config.cloud_host = "https://example.com"
                mock_request.side_effect = Exception("API Error")

                with pytest.raises(CloudUtilsError) as exc_info:
                    await fetch_cloud_projects()

        assert "Failed to fetch cloud projects" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_handles_empty_project_list(self):
        """Test handling of empty project list."""
        with patch("basic_memory.cli.commands.cloud.cloud_utils.make_api_request") as mock_request:
            with patch("basic_memory.cli.commands.cloud.cloud_utils.ConfigManager") as mock_config:
                mock_config.return_value.config.cloud_host = "https://example.com"

                mock_response = Mock()
                mock_response.json.return_value = {"projects": []}
                mock_request.return_value = mock_response

                result = await fetch_cloud_projects()

        assert len(result.projects) == 0


class TestCreateCloudProject:
    """Tests for create_cloud_project()."""

    @pytest.mark.asyncio
    async def test_creates_project_successfully(self):
        """Test successful project creation."""
        with patch("basic_memory.cli.commands.cloud.cloud_utils.make_api_request") as mock_request:
            with patch("basic_memory.cli.commands.cloud.cloud_utils.ConfigManager") as mock_config:
                with patch(
                    "basic_memory.cli.commands.cloud.cloud_utils.generate_permalink"
                ) as mock_permalink:
                    # Setup mocks
                    mock_config.return_value.config.cloud_host = "https://example.com"
                    mock_permalink.return_value = "my-project"

                    mock_response = Mock()
                    mock_response.json.return_value = {
                        "name": "My Project",
                        "path": "my-project",
                        "message": "Created successfully",
                    }
                    mock_request.return_value = mock_response

                    result = await create_cloud_project("My Project")

        # Verify result
        assert result.name == "My Project"
        assert result.path == "my-project"
        assert result.message == "Created successfully"

        # Verify permalink was generated
        mock_permalink.assert_called_once_with("My Project")

        # Verify API request
        call_args = mock_request.call_args
        assert call_args[1]["method"] == "POST"
        assert call_args[1]["url"] == "https://example.com/proxy/projects/projects"
        assert call_args[1]["headers"]["Content-Type"] == "application/json"

        json_data = call_args[1]["json_data"]
        assert json_data["name"] == "My Project"
        assert json_data["path"] == "my-project"
        assert json_data["set_default"] is False

    @pytest.mark.asyncio
    async def test_generates_permalink_from_name(self):
        """Test that permalink is generated from project name."""
        with patch("basic_memory.cli.commands.cloud.cloud_utils.make_api_request") as mock_request:
            with patch("basic_memory.cli.commands.cloud.cloud_utils.ConfigManager") as mock_config:
                with patch(
                    "basic_memory.cli.commands.cloud.cloud_utils.generate_permalink"
                ) as mock_permalink:
                    mock_config.return_value.config.cloud_host = "https://example.com"
                    mock_permalink.return_value = "test-project-123"

                    mock_response = Mock()
                    mock_response.json.return_value = {
                        "name": "Test Project 123",
                        "path": "test-project-123",
                        "message": "Created",
                    }
                    mock_request.return_value = mock_response

                    await create_cloud_project("Test Project 123")

        # Verify generate_permalink was called with project name
        mock_permalink.assert_called_once_with("Test Project 123")

    @pytest.mark.asyncio
    async def test_raises_error_on_api_failure(self):
        """Test that CloudUtilsError is raised on API failure."""
        with patch("basic_memory.cli.commands.cloud.cloud_utils.make_api_request") as mock_request:
            with patch("basic_memory.cli.commands.cloud.cloud_utils.ConfigManager") as mock_config:
                with patch(
                    "basic_memory.cli.commands.cloud.cloud_utils.generate_permalink"
                ) as mock_permalink:
                    mock_config.return_value.config.cloud_host = "https://example.com"
                    mock_permalink.return_value = "project"
                    mock_request.side_effect = Exception("API Error")

                    with pytest.raises(CloudUtilsError) as exc_info:
                        await create_cloud_project("Test Project")

        assert "Failed to create cloud project 'Test Project'" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_strips_trailing_slash_from_host(self):
        """Test that trailing slash is stripped from cloud_host."""
        with patch("basic_memory.cli.commands.cloud.cloud_utils.make_api_request") as mock_request:
            with patch("basic_memory.cli.commands.cloud.cloud_utils.ConfigManager") as mock_config:
                with patch(
                    "basic_memory.cli.commands.cloud.cloud_utils.generate_permalink"
                ) as mock_permalink:
                    mock_config.return_value.config.cloud_host = "https://example.com/"
                    mock_permalink.return_value = "project"

                    mock_response = Mock()
                    mock_response.json.return_value = {
                        "name": "Project",
                        "path": "project",
                        "message": "Created",
                    }
                    mock_request.return_value = mock_response

                    await create_cloud_project("Project")

        # Verify trailing slash was removed
        call_args = mock_request.call_args
        assert call_args[1]["url"] == "https://example.com/proxy/projects/projects"


class TestSyncProject:
    """Tests for sync_project()."""

    @pytest.mark.asyncio
    async def test_syncs_project_successfully(self):
        """Test successful project sync."""
        # Patch at the point where it's imported (inside the function)
        with patch("basic_memory.cli.commands.command_utils.run_sync", new_callable=AsyncMock) as mock_sync:
            await sync_project("test-project")

        # Verify run_sync was called with project name
        mock_sync.assert_called_once_with(project="test-project")

    @pytest.mark.asyncio
    async def test_raises_error_on_sync_failure(self):
        """Test that CloudUtilsError is raised on sync failure."""
        # Patch at the point where it's imported (inside the function)
        with patch("basic_memory.cli.commands.command_utils.run_sync", new_callable=AsyncMock) as mock_sync:
            mock_sync.side_effect = Exception("Sync failed")

            with pytest.raises(CloudUtilsError) as exc_info:
                await sync_project("test-project")

        assert "Failed to sync project 'test-project'" in str(exc_info.value)


class TestProjectExists:
    """Tests for project_exists()."""

    @pytest.mark.asyncio
    async def test_returns_true_when_project_exists(self):
        """Test that True is returned when project exists."""
        from basic_memory.schemas.cloud import CloudProject, CloudProjectList

        with patch(
            "basic_memory.cli.commands.cloud.cloud_utils.fetch_cloud_projects"
        ) as mock_fetch:
            # Create actual CloudProject objects
            projects = CloudProjectList(
                projects=[
                    CloudProject(name="project-1", path="/app/data/project-1"),
                    CloudProject(name="test-project", path="/app/data/test-project"),
                    CloudProject(name="project-2", path="/app/data/project-2"),
                ]
            )
            mock_fetch.return_value = projects

            result = await project_exists("test-project")

        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_project_not_found(self):
        """Test that False is returned when project doesn't exist."""
        with patch(
            "basic_memory.cli.commands.cloud.cloud_utils.fetch_cloud_projects"
        ) as mock_fetch:
            # Mock project list without matching project
            mock_projects = Mock()
            mock_projects.projects = [
                Mock(name="project-1"),
                Mock(name="project-2"),
            ]
            mock_fetch.return_value = mock_projects

            result = await project_exists("nonexistent-project")

        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_on_api_error(self):
        """Test that False is returned on API error."""
        with patch(
            "basic_memory.cli.commands.cloud.cloud_utils.fetch_cloud_projects"
        ) as mock_fetch:
            mock_fetch.side_effect = Exception("API Error")

            result = await project_exists("test-project")

        # Should return False instead of raising exception
        assert result is False

    @pytest.mark.asyncio
    async def test_handles_empty_project_list(self):
        """Test handling of empty project list."""
        with patch(
            "basic_memory.cli.commands.cloud.cloud_utils.fetch_cloud_projects"
        ) as mock_fetch:
            mock_projects = Mock()
            mock_projects.projects = []
            mock_fetch.return_value = mock_projects

            result = await project_exists("any-project")

        assert result is False

    @pytest.mark.asyncio
    async def test_case_sensitive_matching(self):
        """Test that project name matching is case-sensitive."""
        with patch(
            "basic_memory.cli.commands.cloud.cloud_utils.fetch_cloud_projects"
        ) as mock_fetch:
            mock_projects = Mock()
            mock_projects.projects = [Mock(name="Test-Project")]
            mock_fetch.return_value = mock_projects

            # Different case should not match
            result = await project_exists("test-project")

        assert result is False
