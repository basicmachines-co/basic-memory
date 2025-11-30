"""Tests for the ObservationRepository."""

from datetime import datetime, timezone

import pytest
import pytest_asyncio
import sqlalchemy
from sqlalchemy.ext.asyncio import async_sessionmaker

from basic_memory import db
from basic_memory.models import Entity, Observation, Project
from basic_memory.repository.observation_repository import ObservationRepository


@pytest_asyncio.fixture(scope="function")
async def repo(observation_repository):
    """Create an ObservationRepository instance"""
    return observation_repository


@pytest_asyncio.fixture(scope="function")
async def sample_observation(repo, sample_entity: Entity):
    """Create a sample observation for testing"""
    observation_data = {
        "entity_id": sample_entity.id,
        "content": "Test observation",
        "context": "test-context",
    }
    return await repo.create(observation_data)


@pytest.mark.asyncio
async def test_create_observation(
    observation_repository: ObservationRepository, sample_entity: Entity
):
    """Test creating a new observation"""
    observation_data = {
        "entity_id": sample_entity.id,
        "content": "Test content",
        "context": "test-context",
    }
    observation = await observation_repository.create(observation_data)

    assert observation.entity_id == sample_entity.id
    assert observation.content == "Test content"
    assert observation.id is not None  # Should be auto-generated


@pytest.mark.asyncio
async def test_create_observation_entity_does_not_exist(
    observation_repository: ObservationRepository, sample_entity: Entity
):
    """Test creating a new observation"""
    observation_data = {
        "entity_id": 99999,  # Non-existent entity ID (integer for Postgres compatibility)
        "content": "Test content",
        "context": "test-context",
    }
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        await observation_repository.create(observation_data)


@pytest.mark.asyncio
async def test_find_by_entity(
    observation_repository: ObservationRepository,
    sample_observation: Observation,
    sample_entity: Entity,
):
    """Test finding observations by entity"""
    observations = await observation_repository.find_by_entity(sample_entity.id)
    assert len(observations) == 1
    assert observations[0].id == sample_observation.id
    assert observations[0].content == sample_observation.content


@pytest.mark.asyncio
async def test_find_by_context(
    observation_repository: ObservationRepository, sample_observation: Observation
):
    """Test finding observations by context"""
    observations = await observation_repository.find_by_context("test-context")
    assert len(observations) == 1
    assert observations[0].id == sample_observation.id
    assert observations[0].content == sample_observation.content


@pytest.mark.asyncio
async def test_delete_observations(session_maker: async_sessionmaker, repo, test_project: Project):
    """Test deleting observations by entity_id."""
    # Create test entity
    async with db.scoped_session(session_maker) as session:
        entity = Entity(
            project_id=test_project.id,
            title="test_entity",
            entity_type="test",
            permalink="test/test-entity",
            file_path="test/test_entity.md",
            content_type="text/markdown",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(entity)
        await session.flush()

        # Create test observations
        obs1 = Observation(
            entity_id=entity.id,
            content="Test observation 1",
        )
        obs2 = Observation(
            entity_id=entity.id,
            content="Test observation 2",
        )
        session.add_all([obs1, obs2])

    # Test deletion by entity_id
    deleted = await repo.delete_by_fields(entity_id=entity.id)
    assert deleted is True

    # Verify observations were deleted
    remaining = await repo.find_by_entity(entity.id)
    assert len(remaining) == 0


@pytest.mark.asyncio
async def test_delete_observation_by_id(
    session_maker: async_sessionmaker, repo, test_project: Project
):
    """Test deleting a single observation by its ID."""
    # Create test entity
    async with db.scoped_session(session_maker) as session:
        entity = Entity(
            project_id=test_project.id,
            title="test_entity",
            entity_type="test",
            permalink="test/test-entity",
            file_path="test/test_entity.md",
            content_type="text/markdown",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(entity)
        await session.flush()

        # Create test observation
        obs = Observation(
            entity_id=entity.id,
            content="Test observation",
        )
        session.add(obs)

    # Test deletion by ID
    deleted = await repo.delete(obs.id)
    assert deleted is True

    # Verify observation was deleted
    remaining = await repo.find_by_id(obs.id)
    assert remaining is None


@pytest.mark.asyncio
async def test_delete_observation_by_content(
    session_maker: async_sessionmaker, repo, test_project: Project
):
    """Test deleting observations by content."""
    # Create test entity
    async with db.scoped_session(session_maker) as session:
        entity = Entity(
            project_id=test_project.id,
            title="test_entity",
            entity_type="test",
            permalink="test/test-entity",
            file_path="test/test_entity.md",
            content_type="text/markdown",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(entity)
        await session.flush()

        # Create test observations
        obs1 = Observation(
            entity_id=entity.id,
            content="Delete this observation",
        )
        obs2 = Observation(
            entity_id=entity.id,
            content="Keep this observation",
        )
        session.add_all([obs1, obs2])

    # Test deletion by content
    deleted = await repo.delete_by_fields(content="Delete this observation")
    assert deleted is True

    # Verify only matching observation was deleted
    remaining = await repo.find_by_entity(entity.id)
    assert len(remaining) == 1
    assert remaining[0].content == "Keep this observation"


@pytest.mark.asyncio
async def test_find_by_category(session_maker: async_sessionmaker, repo, test_project: Project):
    """Test finding observations by their category."""
    # Create test entity
    async with db.scoped_session(session_maker) as session:
        entity = Entity(
            project_id=test_project.id,
            title="test_entity",
            entity_type="test",
            permalink="test/test-entity",
            file_path="test/test_entity.md",
            content_type="text/markdown",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(entity)
        await session.flush()

        # Create test observations with different categories
        observations = [
            Observation(
                entity_id=entity.id,
                content="Tech observation",
                category="tech",
            ),
            Observation(
                entity_id=entity.id,
                content="Design observation",
                category="design",
            ),
            Observation(
                entity_id=entity.id,
                content="Another tech observation",
                category="tech",
            ),
        ]
        session.add_all(observations)
        await session.commit()

    # Find tech observations
    tech_obs = await repo.find_by_category("tech")
    assert len(tech_obs) == 2
    assert all(obs.category == "tech" for obs in tech_obs)
    assert set(obs.content for obs in tech_obs) == {"Tech observation", "Another tech observation"}

    # Find design observations
    design_obs = await repo.find_by_category("design")
    assert len(design_obs) == 1
    assert design_obs[0].category == "design"
    assert design_obs[0].content == "Design observation"

    # Search for non-existent category
    missing_obs = await repo.find_by_category("missing")
    assert len(missing_obs) == 0


@pytest.mark.asyncio
async def test_observation_categories(
    session_maker: async_sessionmaker, repo, test_project: Project
):
    """Test retrieving distinct observation categories."""
    # Create test entity
    async with db.scoped_session(session_maker) as session:
        entity = Entity(
            project_id=test_project.id,
            title="test_entity",
            entity_type="test",
            permalink="test/test-entity",
            file_path="test/test_entity.md",
            content_type="text/markdown",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(entity)
        await session.flush()

        # Create observations with various categories
        observations = [
            Observation(
                entity_id=entity.id,
                content="First tech note",
                category="tech",
            ),
            Observation(
                entity_id=entity.id,
                content="Second tech note",
                category="tech",  # Duplicate category
            ),
            Observation(
                entity_id=entity.id,
                content="Design note",
                category="design",
            ),
            Observation(
                entity_id=entity.id,
                content="Feature note",
                category="feature",
            ),
        ]
        session.add_all(observations)
        await session.commit()

    # Get distinct categories
    categories = await repo.observation_categories()

    # Should have unique categories in a deterministic order
    assert set(categories) == {"tech", "design", "feature"}


@pytest.mark.asyncio
async def test_find_by_category_with_empty_db(repo):
    """Test category operations with an empty database."""
    # Find by category should return empty list
    obs = await repo.find_by_category("tech")
    assert len(obs) == 0

    # Get categories should return empty list
    categories = await repo.observation_categories()
    assert len(categories) == 0


@pytest.mark.asyncio
async def test_find_by_category_case_sensitivity(
    session_maker: async_sessionmaker, repo, test_project: Project
):
    """Test how category search handles case sensitivity."""
    async with db.scoped_session(session_maker) as session:
        entity = Entity(
            project_id=test_project.id,
            title="test_entity",
            entity_type="test",
            permalink="test/test-entity",
            file_path="test/test_entity.md",
            content_type="text/markdown",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(entity)
        await session.flush()

        # Create a test observation
        obs = Observation(
            entity_id=entity.id,
            content="Tech note",
            category="tech",  # lowercase in database
        )
        session.add(obs)
        await session.commit()

    # Search should work regardless of case
    # Note: If we want case-insensitive search, we'll need to update the query
    # For now, this test documents the current behavior
    exact_match = await repo.find_by_category("tech")
    assert len(exact_match) == 1

    upper_case = await repo.find_by_category("TECH")
    assert len(upper_case) == 0  # Currently case-sensitive


@pytest.mark.asyncio
async def test_observation_permalink_truncation(
    session_maker: async_sessionmaker, repo, test_project: Project
):
    """Test that observation permalinks are truncated to prevent database index overflow (issue #446).

    PostgreSQL btree indexes have a maximum size of 2704 bytes. Long observation content
    was causing permalinks to exceed this limit. The fix truncates content to 150 chars
    when generating permalinks.
    """
    async with db.scoped_session(session_maker) as session:
        entity = Entity(
            project_id=test_project.id,
            title="test_entity",
            entity_type="test",
            permalink="test/test-entity",
            file_path="test/test_entity.md",
            content_type="text/markdown",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(entity)
        await session.flush()

        # Create observation with very long content (simulating transcript dialogue)
        long_content = "This is a very long observation content that would normally cause the permalink to exceed the PostgreSQL btree index limit of 2704 bytes. " * 50
        obs = Observation(
            entity_id=entity.id,
            content=long_content,
            category="transcript",
        )
        session.add(obs)
        await session.commit()

        # Refresh to get the relationship
        await session.refresh(obs)
        await session.refresh(obs, ["entity"])

        # Verify the full content is stored
        assert len(obs.content) > 150
        assert obs.content == long_content

        # Verify the permalink is truncated
        permalink = obs.permalink
        assert permalink is not None
        assert len(permalink) < 500  # Should be much shorter than full content

        # Verify permalink contains truncated content, not full content
        # The permalink format is: {entity.permalink}/observations/{category}/{truncated_content}
        assert "test-entity/observations/transcript/" in permalink

        # Verify the content portion is limited
        # Extract the content portion after the last slash
        content_portion = permalink.split("/")[-1]
        # The content portion should correspond to truncated content (150 chars max)
        # After generate_permalink processing (lowercase, spaces to hyphens, etc.)
        assert len(content_portion) <= 200  # Allow some buffer for URL encoding
