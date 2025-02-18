"""Tests for link resolution service."""

import pytest

import pytest_asyncio

from basic_memory.schemas.base import Entity as EntitySchema
from basic_memory.services.link_resolver import LinkResolver


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
        )
    )
    e2, _ = await entity_service.create_or_update_entity(
        EntitySchema(
            title="Service Config",
            entity_type="config",
            folder="config",
        )
    )
    e3, _ = await entity_service.create_or_update_entity(
        EntitySchema(
            title="Auth Service",
            entity_type="component",
            folder="components",
        )
    )
    e4, _ = await entity_service.create_or_update_entity(
        EntitySchema(
            title="Core Features",
            entity_type="specs",
            folder="specs",
        )
    )
    e5, _ = await entity_service.create_or_update_entity(
        EntitySchema(
            title="Sub Features 1",
            entity_type="specs",
            folder="specs/subspec",
        )
    )
    e6, _ = await entity_service.create_or_update_entity(
        EntitySchema(
            title="Sub Features 2",
            entity_type="specs",
            folder="specs/subspec",
        )
    )

    return [e1, e2, e3, e4]


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


@pytest.mark.skip(reason="Fuzzy misspelling not yet implemented")
@pytest.mark.asyncio
async def test_fuzzy_title_match_misspelling(link_resolver):
    # Test slight misspelling
    result = await link_resolver.resolve_link("Core Servise")
    assert result.permalink == "components/core-service"


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
