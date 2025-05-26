"""Tests for link resolution service."""

from datetime import datetime, timezone

import pytest

import pytest_asyncio

from basic_memory.schemas.base import Entity as EntitySchema
from basic_memory.services.link_resolver import LinkResolver
from basic_memory.models.knowledge import Entity as EntityModel


@pytest_asyncio.fixture
async def test_entities(entity_service, file_service):
    """Create a set of test entities.

    ├── components
    │   ├── Auth Service.md
    │   └── Core Service.md
    ├── config
    │   └── Service Config.md
    └── specs
        └── Core Features.md

    """

    e1, _ = await entity_service.create_or_update_entity(
        EntitySchema(
            title="Core Service",
            entity_type="component",
            folder="components",
            project=entity_service.repository.project_id,
        )
    )
    e2, _ = await entity_service.create_or_update_entity(
        EntitySchema(
            title="Service Config",
            entity_type="config",
            folder="config",
            project=entity_service.repository.project_id,
        )
    )
    e3, _ = await entity_service.create_or_update_entity(
        EntitySchema(
            title="Auth Service",
            entity_type="component",
            folder="components",
            project=entity_service.repository.project_id,
        )
    )
    e4, _ = await entity_service.create_or_update_entity(
        EntitySchema(
            title="Core Features",
            entity_type="specs",
            folder="specs",
            project=entity_service.repository.project_id,
        )
    )
    e5, _ = await entity_service.create_or_update_entity(
        EntitySchema(
            title="Sub Features 1",
            entity_type="specs",
            folder="specs/subspec",
            project=entity_service.repository.project_id,
        )
    )
    e6, _ = await entity_service.create_or_update_entity(
        EntitySchema(
            title="Sub Features 2",
            entity_type="specs",
            folder="specs/subspec",
            project=entity_service.repository.project_id,
        )
    )

    # non markdown entity
    e7 = await entity_service.repository.add(
        EntityModel(
            title="Image.png",
            entity_type="file",
            content_type="image/png",
            file_path="Image.png",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            project_id=entity_service.repository.project_id,
        )
    )

    e8 = await entity_service.create_entity(  # duplicate title
        EntitySchema(
            title="Core Service",
            entity_type="component",
            folder="components2",
            project=entity_service.repository.project_id,
        )
    )

    return [e1, e2, e3, e4, e5, e6, e7, e8]


@pytest_asyncio.fixture
async def link_resolver(entity_repository, search_service, test_entities):
    """Create LinkResolver instance with indexed test data."""
    # Index all test entities
    for entity in test_entities:
        await search_service.index_entity(entity)

    return LinkResolver(entity_repository, search_service)


@pytest.mark.asyncio
async def test_exact_permalink_match(link_resolver, test_entities):
    """Test resolving a link that exactly matches a permalink."""
    entity = await link_resolver.resolve_link("components/core-service")
    assert entity.permalink == "components/core-service"


@pytest.mark.asyncio
async def test_exact_title_match(link_resolver, test_entities):
    """Test resolving a link that matches an entity title."""
    entity = await link_resolver.resolve_link("Core Service")
    assert entity.permalink == "components/core-service"


@pytest.mark.asyncio
async def test_duplicate_title_match(link_resolver, test_entities):
    """Test resolving a link that matches an entity title."""
    entity = await link_resolver.resolve_link("Core Service")
    assert entity.permalink == "components/core-service"


@pytest.mark.asyncio
async def test_fuzzy_title_partial_match(link_resolver):
    # Test partial match
    result = await link_resolver.resolve_link("Auth Serv")
    assert result is not None, "Did not find partial match"
    assert result.permalink == "components/auth-service"


@pytest.mark.asyncio
async def test_fuzzy_title_exact_match(link_resolver):
    # Test partial match
    result = await link_resolver.resolve_link("auth-service")
    assert result.permalink == "components/auth-service"


@pytest.mark.asyncio
async def test_link_text_normalization(link_resolver):
    """Test link text normalization."""
    # Basic normalization
    text, alias = link_resolver._normalize_link_text("[[Core Service]]")
    assert text == "Core Service"
    assert alias is None

    # With alias
    text, alias = link_resolver._normalize_link_text("[[Core Service|Main Service]]")
    assert text == "Core Service"
    assert alias == "Main Service"

    # Extra whitespace
    text, alias = link_resolver._normalize_link_text("  [[  Core Service  |  Main Service  ]]  ")
    assert text == "Core Service"
    assert alias == "Main Service"


@pytest.mark.asyncio
async def test_resolve_none(link_resolver):
    """Test resolving non-existent entity."""
    # Basic new entity
    assert await link_resolver.resolve_link("New Feature") is None


@pytest.mark.asyncio
async def test_resolve_file(link_resolver):
    """Test resolving non-existent entity."""
    # Basic new entity
    resolved = await link_resolver.resolve_link("Image.png")
    assert resolved is not None
    assert resolved.entity_type == "file"
    assert resolved.title == "Image.png"


@pytest.mark.asyncio
async def test_folder_title_pattern_with_md_extension(link_resolver, test_entities):
    """Test resolving folder/title patterns that need .md extension added.

    This tests the new logic added in step 4 of resolve_link that handles
    patterns like 'folder/title' by trying 'folder/title.md' as file path.
    """
    # Test folder/title pattern for markdown entities
    # "components/Core Service" should resolve to file path "components/Core Service.md"
    entity = await link_resolver.resolve_link("components/Core Service")
    assert entity is not None
    assert entity.permalink == "components/core-service"
    assert entity.file_path == "components/Core Service.md"

    # Test with different entity
    entity = await link_resolver.resolve_link("config/Service Config")
    assert entity is not None
    assert entity.permalink == "config/service-config"
    assert entity.file_path == "config/Service Config.md"

    # Test with nested folder structure
    entity = await link_resolver.resolve_link("specs/subspec/Sub Features 1")
    assert entity is not None
    assert entity.permalink == "specs/subspec/sub-features-1"
    assert entity.file_path == "specs/subspec/Sub Features 1.md"

    # Test that it doesn't try to add .md to things that already have it
    entity = await link_resolver.resolve_link("components/Core Service.md")
    assert entity is not None
    assert entity.permalink == "components/core-service"

    # Test that it doesn't try to add .md to single words (no slash)
    entity = await link_resolver.resolve_link("NonExistent")
    assert entity is None

    # Test that it doesn't interfere with exact permalink matches
    entity = await link_resolver.resolve_link("components/core-service")
    assert entity is not None
    assert entity.permalink == "components/core-service"
