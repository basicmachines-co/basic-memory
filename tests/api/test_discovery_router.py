"""Tests for discovery router endpoints."""

import pytest
import pytest_asyncio
from httpx import AsyncClient

from basic_memory.models.knowledge import Entity
from basic_memory.repository.entity_repository import EntityRepository
from basic_memory.schemas import EntityTypeList

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def test_entities(entity_repository: EntityRepository) -> list[Entity]:
    """Create test entities with different types."""
    entities = [
        Entity(
            name="Memory Service",
            entity_type="technical_component",
            description="Core memory service",
            path_id="component/memory_service",
            file_path="component/memory_service.md",
        ),
        Entity(
            name="File Format",
            entity_type="specification",
            description="File format spec",
            path_id="spec/file_format",
            file_path="spec/file_format.md",
        ),
        Entity(
            name="Technical Decision",
            entity_type="decision",
            description="Architecture decision",
            path_id="decision/tech_choice",
            file_path="decision/tech_choice.md",
        ),
    ]
    
    created = await entity_repository.add_all(entities)
    return created


@pytest.mark.asyncio
async def test_get_entity_types(client: AsyncClient, test_entities):
    """Test getting list of entity types."""
    # Get types
    response = await client.get("/discovery/entity-types")
    assert response.status_code == 200
    
    # Parse response
    data = EntityTypeList.model_validate(response.json())
    
    # Should have types from test data
    assert len(data.types) > 0
    assert "technical_component" in data.types
    assert "specification" in data.types
    assert "decision" in data.types
    
    # Types should all be strings
    assert isinstance(data.types, list)
    assert all(isinstance(t, str) for t in data.types)
    
    # Types should be unique
    assert len(data.types) == len(set(data.types))
