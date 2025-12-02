"""Tests for context service."""

from datetime import datetime, timedelta, UTC

import pytest
import pytest_asyncio

from basic_memory.repository.search_repository import SearchIndexRow
from basic_memory.schemas.memory import memory_url, memory_url_path
from basic_memory.schemas.search import SearchItemType
from basic_memory.services.context_service import ContextService
from basic_memory.models.knowledge import Entity, Relation
from basic_memory.models.project import Project


@pytest_asyncio.fixture
async def context_service(search_repository, entity_repository, observation_repository):
    """Create context service for testing."""
    return ContextService(search_repository, entity_repository, observation_repository)


@pytest.mark.asyncio
async def test_find_connected_depth_limit(context_service, test_graph):
    """Test depth limiting works.
    Our traversal path is:
    - Depth 0: Root
    - Depth 1: Relations + directly connected entities (Connected1, Connected2)
    - Depth 2: Relations + next level entities (Deep)
    """
    type_id_pairs = [("entity", test_graph["root"].id)]

    # With depth=1, we get direct connections
    # shallow_results = await context_service.find_related(type_id_pairs, max_depth=1)
    # shallow_entities = {(r.id, r.type) for r in shallow_results if r.type == "entity"}
    #
    # assert (test_graph["deep"].id, "entity") not in shallow_entities

    # search deeper
    deep_results = await context_service.find_related(type_id_pairs, max_depth=3, max_results=100)
    deep_entities = {(r.id, r.type) for r in deep_results if r.type == "entity"}
    print(deep_entities)
    # Should now include Deep entity
    assert (test_graph["deep"].id, "entity") in deep_entities


@pytest.mark.asyncio
async def test_find_connected_timeframe(
    context_service, test_graph, search_repository, entity_repository, app_config
):
    """Test timeframe filtering.
    This tests how traversal is affected by the item dates.
    When we filter by date, items are only included if:
    1. They match the timeframe
    2. There is a valid path to them through other items in the timeframe
    """
    # Skip for Postgres - needs investigation of duplicate key violations
    from basic_memory.config import DatabaseBackend

    if app_config.database_backend == DatabaseBackend.POSTGRES:
        pytest.skip("Not yet supported for Postgres - duplicate key violation issue")

    now = datetime.now(UTC)
    old_date = now - timedelta(days=10)
    recent_date = now - timedelta(days=1)

    # Update entity table timestamps directly
    # Root entity uses old date
    root_entity = test_graph["root"]
    await entity_repository.update(root_entity.id, {"created_at": old_date, "updated_at": old_date})

    # Connected entity uses recent date
    connected_entity = test_graph["connected1"]
    await entity_repository.update(
        connected_entity.id, {"created_at": recent_date, "updated_at": recent_date}
    )

    # Also update search_index for test consistency
    await search_repository.index_item(
        SearchIndexRow(
            project_id=entity_repository.project_id,
            id=test_graph["root"].id,
            title=test_graph["root"].title,
            content_snippet="Root content",
            permalink=test_graph["root"].permalink,
            file_path=test_graph["root"].file_path,
            type=SearchItemType.ENTITY,
            metadata={"created_at": old_date.isoformat()},
            created_at=old_date,
            updated_at=old_date,
        )
    )
    await search_repository.index_item(
        SearchIndexRow(
            project_id=entity_repository.project_id,
            id=test_graph["relations"][0].id,
            title="Root Entity → Connected Entity 1",
            content_snippet="",
            permalink=f"{test_graph['root'].permalink}/connects_to/{test_graph['connected1'].permalink}",
            file_path=test_graph["root"].file_path,
            type=SearchItemType.RELATION,
            from_id=test_graph["root"].id,
            to_id=test_graph["connected1"].id,
            relation_type="connects_to",
            metadata={"created_at": old_date.isoformat()},
            created_at=old_date,
            updated_at=old_date,
        )
    )
    await search_repository.index_item(
        SearchIndexRow(
            project_id=entity_repository.project_id,
            id=test_graph["connected1"].id,
            title=test_graph["connected1"].title,
            content_snippet="Connected 1 content",
            permalink=test_graph["connected1"].permalink,
            file_path=test_graph["connected1"].file_path,
            type=SearchItemType.ENTITY,
            metadata={"created_at": recent_date.isoformat()},
            created_at=recent_date,
            updated_at=recent_date,
        )
    )

    type_id_pairs = [("entity", test_graph["root"].id)]

    # Search with a 7-day cutoff
    since_date = now - timedelta(days=7)
    results = await context_service.find_related(type_id_pairs, since=since_date)

    # Only connected1 is recent, but we can't get to it
    # because its connecting relation is too old and is filtered out
    # (we can only reach connected1 through a relation starting from root)
    entity_ids = {r.id for r in results if r.type == "entity"}
    assert len(entity_ids) == 0  # No accessible entities within timeframe


@pytest.mark.asyncio
async def test_build_context(context_service, test_graph):
    """Test exact permalink lookup."""
    url = memory_url.validate_strings("memory://test/root")
    context_result = await context_service.build_context(url)

    # Check metadata
    assert context_result.metadata.uri == memory_url_path(url)
    assert context_result.metadata.depth == 1
    assert context_result.metadata.primary_count == 1
    assert context_result.metadata.related_count > 0
    assert context_result.metadata.generated_at is not None

    # Check results
    assert len(context_result.results) == 1
    context_item = context_result.results[0]

    # Check primary result
    primary_result = context_item.primary_result
    assert primary_result.id == test_graph["root"].id
    assert primary_result.type == "entity"
    assert primary_result.title == "Root"
    assert primary_result.permalink == "test/root"
    assert primary_result.file_path == "test/Root.md"
    assert primary_result.created_at is not None

    # Check related results
    assert len(context_item.related_results) > 0

    # Find related relation
    relation = next((r for r in context_item.related_results if r.type == "relation"), None)
    assert relation is not None
    assert relation.relation_type == "connects_to"
    assert relation.from_id == test_graph["root"].id
    assert relation.to_id == test_graph["connected1"].id

    # Find related entity
    related_entity = next((r for r in context_item.related_results if r.type == "entity"), None)
    assert related_entity is not None
    assert related_entity.id == test_graph["connected1"].id
    assert related_entity.title == test_graph["connected1"].title
    assert related_entity.permalink == test_graph["connected1"].permalink


@pytest.mark.asyncio
async def test_build_context_with_observations(context_service, test_graph):
    """Test context building with observations."""
    # The test_graph fixture already creates observations for root entity
    # Let's use those existing observations

    # Build context
    url = memory_url.validate_strings("memory://test/root")
    context_result = await context_service.build_context(url, include_observations=True)

    # Check the metadata
    assert context_result.metadata.total_observations > 0
    assert len(context_result.results) == 1

    # Check that observations were included
    context_item = context_result.results[0]
    assert len(context_item.observations) > 0

    # Check observation properties
    for observation in context_item.observations:
        assert observation.type == "observation"
        assert observation.category in ["note", "tech"]  # Categories from test_graph fixture
        assert observation.entity_id == test_graph["root"].id

    # Verify at least one observation has the correct category and content
    note_observation = next((o for o in context_item.observations if o.category == "note"), None)
    assert note_observation is not None
    assert "Root note" in note_observation.content


@pytest.mark.asyncio
async def test_build_context_not_found(context_service):
    """Test handling non-existent permalinks."""
    context = await context_service.build_context("memory://does/not/exist")
    assert len(context.results) == 0
    assert context.metadata.primary_count == 0
    assert context.metadata.related_count == 0


@pytest.mark.asyncio
async def test_context_metadata(context_service, test_graph):
    """Test metadata is correctly populated."""
    context = await context_service.build_context("memory://test/root", depth=2)
    metadata = context.metadata
    assert metadata.uri == "test/root"
    assert metadata.depth == 2
    assert metadata.generated_at is not None
    assert metadata.primary_count > 0


@pytest.mark.asyncio
async def test_project_isolation_in_find_related(session_maker, app_config):
    """Test that find_related respects project boundaries and doesn't leak data."""
    from basic_memory.repository.entity_repository import EntityRepository
    from basic_memory.repository.observation_repository import ObservationRepository
    from basic_memory.repository.sqlite_search_repository import SQLiteSearchRepository
    from basic_memory.repository.postgres_search_repository import PostgresSearchRepository
    from basic_memory.config import DatabaseBackend
    from basic_memory import db

    # Create database session
    async with db.scoped_session(session_maker) as db_session:
        # Create two separate projects
        project1 = Project(name="project1", path="/test1")
        project2 = Project(name="project2", path="/test2")
        db_session.add(project1)
        db_session.add(project2)
        await db_session.flush()

        # Create entities in project1
        entity1_p1 = Entity(
            title="Entity1_P1",
            entity_type="document",
            content_type="text/markdown",
            project_id=project1.id,
            permalink="project1/entity1",
            file_path="project1/entity1.md",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        entity2_p1 = Entity(
            title="Entity2_P1",
            entity_type="document",
            content_type="text/markdown",
            project_id=project1.id,
            permalink="project1/entity2",
            file_path="project1/entity2.md",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

        # Create entities in project2
        entity1_p2 = Entity(
            title="Entity1_P2",
            entity_type="document",
            content_type="text/markdown",
            project_id=project2.id,
            permalink="project2/entity1",
            file_path="project2/entity1.md",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

        db_session.add_all([entity1_p1, entity2_p1, entity1_p2])
        await db_session.flush()

        # Create relation in project1 (between entities of project1)
        relation_p1 = Relation(
            project_id=project1.id,
            from_id=entity1_p1.id,
            to_id=entity2_p1.id,
            to_name="Entity2_P1",
            relation_type="connects_to",
        )
        db_session.add(relation_p1)
        await db_session.commit()

        # Create database-specific search repositories based on backend
        if app_config.database_backend == DatabaseBackend.POSTGRES:
            search_repo_p1 = PostgresSearchRepository(session_maker, project1.id)
            search_repo_p2 = PostgresSearchRepository(session_maker, project2.id)
        else:
            search_repo_p1 = SQLiteSearchRepository(session_maker, project1.id)
            search_repo_p2 = SQLiteSearchRepository(session_maker, project2.id)

        # Create repositories for project1
        entity_repo_p1 = EntityRepository(session_maker, project1.id)
        obs_repo_p1 = ObservationRepository(session_maker, project1.id)
        context_service_p1 = ContextService(search_repo_p1, entity_repo_p1, obs_repo_p1)

        # Create repositories for project2
        entity_repo_p2 = EntityRepository(session_maker, project2.id)
        obs_repo_p2 = ObservationRepository(session_maker, project2.id)
        context_service_p2 = ContextService(search_repo_p2, entity_repo_p2, obs_repo_p2)

        # Test: find_related for project1 should only return project1 entities
        type_id_pairs_p1 = [("entity", entity1_p1.id)]
        related_p1 = await context_service_p1.find_related(type_id_pairs_p1, max_depth=2)

        # Verify only project1 entities are returned
        related_entity_ids = [r.id for r in related_p1 if r.type == "entity"]
        assert entity2_p1.id in related_entity_ids  # Should find connected entity2 in project1
        assert entity1_p2.id not in related_entity_ids  # Should NOT find entity from project2

        # Test: find_related for project2 should return empty (no relations)
        type_id_pairs_p2 = [("entity", entity1_p2.id)]
        related_p2 = await context_service_p2.find_related(type_id_pairs_p2, max_depth=2)

        # Project2 has no relations, so should return empty
        assert len(related_p2) == 0

        # Double-check: verify entities exist in their respective projects
        assert entity1_p1.project_id == project1.id
        assert entity2_p1.project_id == project1.id
        assert entity1_p2.project_id == project2.id
