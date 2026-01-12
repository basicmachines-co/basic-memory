"""Fixtures for V2 API tests."""

import pytest

from basic_memory.models import Project


@pytest.fixture
def v2_project_url(test_project: Project) -> str:
    """Create a URL prefix for v2 project-scoped routes using project external_id.

    This helps tests generate the correct URL for v2 project-scoped routes
    which use external_id UUIDs instead of permalinks or integer IDs.
    """
    return f"/v2/projects/{test_project.external_id}"


@pytest.fixture
def v2_projects_url() -> str:
    """Base URL for v2 project management endpoints."""
    return "/v2/projects"
