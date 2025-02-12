"""Tests for link resolution service."""

import pytest
from datetime import datetime, timezone

import pytest_asyncio

from basic_memory.models.knowledge import Entity
from basic_memory.services.link_resolver import LinkResolver


@pytest_asyncio.fixture
async def test_entities(entity_repository, file_service):
    """Create a set of test entities."""
    entities = [
        Entity(
            title="Core Service",
            entity_type="component",
            permalink="components/core-service",
            file_path="components/core-service.md",
            content_type="text/markdown",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        ),
        Entity(
            title="Service Config",
            entity_type="config",
            permalink="config/service-config",
            file_path="config/service-config.md",
            content_type="text/markdown",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        ),
        Entity(
            title="Auth Service",
            entity_type="component",
            permalink="components/auth/service",
            file_path="components/auth/service.md",
            content_type="text/markdown",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        ),
        Entity(
            title="Core Features",
            entity_type="specs",
            permalink="specs/features/core",
            file_path="specs/features/core.md",
            content_type="text/markdown",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        ),
    ]
    
    for entity in entities:
        await file_service.write_entity_file(entity)

    # Add to repository
    return await entity_repository.add_all(entities)


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
    assert result.permalink == "components/auth/service"

@pytest.mark.asyncio
async def test_fuzzy_title_exact_match(link_resolver):
    # Test partial match
    result = await link_resolver.resolve_link("service")
    assert result.permalink == "components/auth/service"


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


@pytest.mark.skip("Advanced relevance scoring not yet implemented")
@pytest.mark.asyncio
async def test_multiple_matches_resolution(link_resolver):
    """Test resolution when multiple potential matches exist."""
    # Add some similar entities
    test_cases = [
        {
            "link": "Service",  # Ambiguous
            "expected_prefix": "components/",  # Should prefer component directory match
        },
        {
            "link": "Core",  # Ambiguous
            "expected_prefix": "specs/",  # Should prefer specs directory match
        },
        {
            "link": "Service",
            "expected": "components/core-service",  # Should pick shortest/highest scored
        },
    ]

    for case in test_cases:
        result = await link_resolver.resolve_link(case["link"])
        if "expected_prefix" in case:
            assert result.startswith(case["expected_prefix"])
        else:
            assert result == case["expected"]
