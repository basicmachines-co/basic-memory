"""Tests for the ProjectInfoRepository."""

import pytest
from sqlalchemy import text

from basic_memory import db
from basic_memory.repository.project_info_repository import ProjectInfoRepository
from basic_memory.models.project import Project  # Add a model reference


@pytest.mark.asyncio
async def test_project_info_repository_init(session_maker):
    """Test ProjectInfoRepository initialization."""
    # Create a ProjectInfoRepository
    repository = ProjectInfoRepository()

    # Verify it was initialized properly
    assert repository is not None
    # Model is set to a dummy value (Project is used as a reference here)
    assert repository.Model is Project


@pytest.mark.asyncio
async def test_project_info_repository_execute_query(session_maker):
    """Test ProjectInfoRepository execute_query method."""
    # Create a ProjectInfoRepository
    repository = ProjectInfoRepository()

    # Execute a simple query
    async with db.scoped_session(session_maker) as session:
        result = await repository.execute_query(session, text("SELECT 1 as test"))

    # Verify the result
    assert result is not None
    row = result.fetchone()
    assert row is not None
    assert row[0] == 1
