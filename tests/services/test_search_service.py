"""Tests for search service."""

from datetime import datetime

import pytest
from sqlalchemy import text

from basic_memory import db
from basic_memory.repository.search_index_row import SearchIndexRow
from basic_memory.schemas.search import SearchQuery, SearchItemType, SearchRetrievalMode
from basic_memory.services.search_service import _strip_nul


@pytest.mark.asyncio
async def test_search_permalink(search_service, test_graph):
    """Exact permalink"""
    results = await search_service.search(SearchQuery(permalink="test-project/test/root"))
    assert len(results) == 1

    for r in results:
        assert "test-project/test/root" in r.permalink


@pytest.mark.asyncio
async def test_search_limit_offset(search_service, test_graph):
    """Exact permalink"""
    results = await search_service.search(SearchQuery(permalink_match="test-project/test/*"))
    assert len(results) > 1

    results = await search_service.search(
        SearchQuery(permalink_match="test-project/test/*"), limit=1
    )
    assert len(results) == 1

    results = await search_service.search(
        SearchQuery(permalink_match="test-project/test/*"), limit=100
    )
    num_results = len(results)

    # assert offset
    offset_results = await search_service.search(
        SearchQuery(permalink_match="test-project/test/*"), limit=100, offset=1
    )
    assert len(offset_results) == num_results - 1


@pytest.mark.asyncio
async def test_search_permalink_observations_wildcard(search_service, test_graph):
    """Pattern matching"""
    results = await search_service.search(
        SearchQuery(permalink_match="test-project/test/root/observations/*")
    )
    assert len(results) == 2
    permalinks = {r.permalink for r in results}
    assert "test-project/test/root/observations/note/root-note-1" in permalinks
    assert "test-project/test/root/observations/tech/root-tech-note" in permalinks


@pytest.mark.asyncio
async def test_search_permalink_relation_wildcard(search_service, test_graph):
    """Pattern matching"""
    results = await search_service.search(
        SearchQuery(permalink_match="test-project/test/root/connects-to/*")
    )
    assert len(results) == 1
    permalinks = {r.permalink for r in results}
    assert "test-project/test/root/connects-to/test-project/test/connected-entity-1" in permalinks


@pytest.mark.asyncio
async def test_search_permalink_wildcard2(search_service, test_graph):
    """Pattern matching"""
    results = await search_service.search(
        SearchQuery(
            permalink_match="test-project/test/connected*",
        )
    )
    assert len(results) >= 2
    permalinks = {r.permalink for r in results}
    assert "test-project/test/connected-entity-1" in permalinks
    assert "test-project/test/connected-entity-2" in permalinks


@pytest.mark.asyncio
async def test_search_text(search_service, test_graph):
    """Full-text search"""
    results = await search_service.search(
        SearchQuery(text="Root Entity", entity_types=[SearchItemType.ENTITY])
    )
    assert len(results) >= 1
    assert results[0].permalink == "test-project/test/root"


@pytest.mark.asyncio
async def test_search_title(search_service, test_graph):
    """Title only search"""
    results = await search_service.search(
        SearchQuery(title="Root", entity_types=[SearchItemType.ENTITY])
    )
    assert len(results) >= 1
    assert results[0].permalink == "test-project/test/root"


@pytest.mark.asyncio
async def test_text_search_case_insensitive(search_service, test_graph):
    """Test text search functionality."""
    # Case insensitive
    results = await search_service.search(SearchQuery(text="ENTITY"))
    assert any("test-project/test/root" in r.permalink for r in results)


@pytest.mark.asyncio
async def test_text_search_content_word_match(search_service, test_graph):
    """Test text search functionality."""

    # content word match
    results = await search_service.search(SearchQuery(text="Connected"))
    assert len(results) > 0
    assert any(r.file_path == "test/Connected Entity 2.md" for r in results)


@pytest.mark.asyncio
async def test_text_search_multiple_terms(search_service, test_graph):
    """Test text search functionality."""

    # Multiple terms
    results = await search_service.search(SearchQuery(text="root note"))
    assert any("test-project/test/root" in r.permalink for r in results)


@pytest.mark.asyncio
async def test_pattern_matching(search_service, test_graph):
    """Test pattern matching with various wildcards."""
    # Test wildcards
    results = await search_service.search(SearchQuery(permalink_match="test-project/test/*"))
    for r in results:
        assert "test-project/test/" in r.permalink

    # Test start wildcards
    results = await search_service.search(SearchQuery(permalink_match="*/observations"))
    for r in results:
        assert "/observations" in r.permalink

    # Test permalink partial match
    results = await search_service.search(SearchQuery(permalink_match="test-project/test"))
    for r in results:
        assert "test-project/test/" in r.permalink


@pytest.mark.asyncio
async def test_filters(search_service, test_graph):
    """Test search filters."""
    # Combined filters
    results = await search_service.search(
        SearchQuery(text="Deep", entity_types=[SearchItemType.ENTITY], note_types=["deep"])
    )
    assert len(results) == 1
    for r in results:
        assert r.type == SearchItemType.ENTITY
        assert r.metadata.get("note_type") == "deep"


@pytest.mark.asyncio
async def test_after_date(search_service, test_graph):
    """Test search filters."""

    # Should find with past date
    past_date = datetime(2020, 1, 1).astimezone()
    results = await search_service.search(
        SearchQuery(
            text="entity",
            after_date=past_date.isoformat(),
        )
    )
    for r in results:
        # Handle both string (SQLite) and datetime (Postgres) formats
        created_at = (
            r.created_at
            if isinstance(r.created_at, datetime)
            else datetime.fromisoformat(r.created_at)
        )
        assert created_at > past_date

    # Should not find with future date
    future_date = datetime(2030, 1, 1).astimezone()
    results = await search_service.search(
        SearchQuery(
            text="entity",
            after_date=future_date.isoformat(),
        )
    )
    assert len(results) == 0


@pytest.mark.asyncio
async def test_search_type(search_service, test_graph):
    """Test search filters."""

    # Should find only type
    results = await search_service.search(SearchQuery(note_types=["test"]))
    assert len(results) > 0
    for r in results:
        assert r.type == SearchItemType.ENTITY


@pytest.mark.asyncio
async def test_search_entity_type(search_service, test_graph):
    """Test search filters."""

    # Should find only type
    results = await search_service.search(SearchQuery(entity_types=[SearchItemType.ENTITY]))
    assert len(results) > 0
    for r in results:
        assert r.type == SearchItemType.ENTITY


@pytest.mark.asyncio
async def test_extract_entity_tags_exception_handling(search_service):
    """Test the _extract_entity_tags method exception handling (lines 147-151)."""
    from basic_memory.models.knowledge import Entity

    # Create entity with string tags that will cause parsing to fail and fall back to single tag
    entity_with_invalid_tags = Entity(
        title="Test Entity",
        note_type="test",
        entity_metadata={"tags": "just a string"},  # This will fail ast.literal_eval
        content_type="text/markdown",
        file_path="test/test-entity.md",
        project_id=1,
    )

    # This should trigger the except block on lines 147-149
    result = search_service._extract_entity_tags(entity_with_invalid_tags)
    assert result == ["just a string"]

    # Test with empty string (should return empty list) - covers line 149
    entity_with_empty_tags = Entity(
        title="Test Entity Empty",
        note_type="test",
        entity_metadata={"tags": ""},
        content_type="text/markdown",
        file_path="test/test-entity-empty.md",
        project_id=1,
    )

    result = search_service._extract_entity_tags(entity_with_empty_tags)
    assert result == []


@pytest.mark.asyncio
async def test_delete_entity_without_permalink(search_service, sample_entity):
    """Test deleting an entity that has no permalink (edge case)."""

    # Set the entity permalink to None to trigger the else branch on line 355
    sample_entity.permalink = None

    # This should trigger the delete_by_entity_id path (line 355) in handle_delete
    await search_service.handle_delete(sample_entity)


@pytest.mark.asyncio
async def test_no_criteria(search_service, test_graph):
    """Test search with no criteria returns empty list."""
    results = await search_service.search(SearchQuery())
    assert len(results) == 0


@pytest.mark.asyncio
async def test_init_search_index(search_service, session_maker, app_config):
    """Test search index initialization."""
    from basic_memory.config import DatabaseBackend

    async with db.scoped_session(session_maker) as session:
        # Use database-specific query to check table existence
        if app_config.database_backend == DatabaseBackend.POSTGRES:
            result = await session.execute(
                text("SELECT tablename FROM pg_catalog.pg_tables WHERE tablename='search_index';")
            )
        else:
            result = await session.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name='search_index';")
            )
        assert result.scalar() == "search_index"


@pytest.mark.asyncio
async def test_update_index(search_service, full_entity):
    """Test updating indexed content."""
    await search_service.index_entity(full_entity)

    # Update entity
    full_entity.title = "OMG I AM UPDATED"
    await search_service.index_entity(full_entity)

    # Search for new title
    results = await search_service.search(SearchQuery(text="OMG I AM UPDATED"))
    assert len(results) > 1


@pytest.mark.asyncio
async def test_boolean_and_search(search_service, test_graph):
    """Test boolean AND search."""
    # Create an entity with specific terms for testing
    # This assumes the test_graph fixture already has entities with relevant terms

    # Test AND operator - both terms must be present
    results = await search_service.search(SearchQuery(text="Root AND Entity"))
    assert len(results) >= 1

    # Verify the result contains both terms
    found = False
    for result in results:
        if (result.title and "Root" in result.title and "Entity" in result.title) or (
            result.content_snippet
            and "Root" in result.content_snippet
            and "Entity" in result.content_snippet
        ):
            found = True
            break
    assert found, "Boolean AND search failed to find items containing both terms"

    # Verify that items with only one term are not returned
    results = await search_service.search(SearchQuery(text="NonexistentTerm AND Root"))
    assert len(results) == 0, "Boolean AND search returned results when it shouldn't have"


@pytest.mark.asyncio
async def test_boolean_or_search(search_service, test_graph):
    """Test boolean OR search."""
    # Test OR operator - either term can be present
    results = await search_service.search(SearchQuery(text="Root OR Connected"))

    # Should find both "Root Entity" and "Connected Entity"
    assert len(results) >= 2

    # Verify we find items with either term
    root_found = False
    connected_found = False

    for result in results:
        if result.permalink == "test-project/test/root":
            root_found = True
        elif "connected" in result.permalink.lower():
            connected_found = True

    assert root_found, "Boolean OR search failed to find 'Root' term"
    assert connected_found, "Boolean OR search failed to find 'Connected' term"


@pytest.mark.asyncio
async def test_boolean_not_search(search_service, test_graph):
    """Test boolean NOT search."""
    # Test NOT operator - exclude certain terms
    results = await search_service.search(SearchQuery(text="Entity NOT Connected"))

    # Should find "Root Entity" but not "Connected Entity"
    for result in results:
        assert "connected" not in result.permalink.lower(), (
            "Boolean NOT search returned excluded term"
        )


@pytest.mark.asyncio
async def test_boolean_group_search(search_service, test_graph):
    """Test boolean grouping with parentheses."""
    # Test grouping - (A OR B) AND C
    results = await search_service.search(SearchQuery(title="(Root OR Connected) AND Entity"))

    # Should find both entities that contain "Entity" and either "Root" or "Connected"
    assert len(results) >= 2

    for result in results:
        # Each result should contain "Entity" and either "Root" or "Connected"
        contains_entity = "entity" in result.title.lower()
        contains_root_or_connected = (
            "root" in result.title.lower() or "connected" in result.title.lower()
        )

        assert contains_entity and contains_root_or_connected, (
            "Boolean grouped search returned incorrect results"
        )


@pytest.mark.asyncio
async def test_boolean_operators_detection(search_service):
    """Test detection of boolean operators in query."""
    # Test various queries that should be detected as boolean
    boolean_queries = [
        "term1 AND term2",
        "term1 OR term2",
        "term1 NOT term2",
        "(term1 OR term2) AND term3",
        "complex (nested OR grouping) AND term",
    ]

    for query_text in boolean_queries:
        query = SearchQuery(text=query_text)
        assert query.has_boolean_operators(), f"Failed to detect boolean operators in: {query_text}"

    # Test queries that should not be detected as boolean
    non_boolean_queries = [
        "normal search query",
        "brand name",  # Should not detect "AND" within "brand"
        "understand this concept",  # Should not detect "AND" within "understand"
        "command line",
        "sandbox testing",
    ]

    for query_text in non_boolean_queries:
        query = SearchQuery(text=query_text)
        assert not query.has_boolean_operators(), (
            f"Incorrectly detected boolean operators in: {query_text}"
        )


@pytest.mark.asyncio
async def test_plain_multiterm_fts_retries_with_relaxed_or_when_strict_empty(
    search_service, monkeypatch
):
    """Plain multi-term FTS should retry with relaxed OR query after strict no-results."""
    call_texts: list[str | None] = []

    now = datetime.now().astimezone()
    fallback_row = SearchIndexRow(
        project_id=1,
        id=1,
        type=SearchItemType.ENTITY.value,
        file_path="test/fallback.md",
        created_at=now,
        updated_at=now,
        permalink="test/fallback",
        metadata={"note_type": "note"},
        title="Fallback Match",
        score=1.0,
    )

    async def fake_search(**kwargs):
        call_texts.append(kwargs.get("search_text"))
        if len(call_texts) == 1:
            return []
        return [fallback_row]

    monkeypatch.setattr(search_service.repository, "search", fake_search)

    results = await search_service.search(
        SearchQuery(text="fundraising venture capital", retrieval_mode=SearchRetrievalMode.FTS)
    )

    assert len(results) == 1
    assert call_texts[0] == "fundraising venture capital"
    assert call_texts[1] == "fundraising OR venture OR capital"
    assert len(call_texts) == 2


@pytest.mark.asyncio
async def test_relaxed_query_prunes_stopwords(search_service):
    """Relaxed query should remove stopwords and keep high-signal terms."""
    relaxed = search_service._build_relaxed_fts_query("who are our main competitors and partners?")
    assert relaxed == "main OR competitors OR partners"


@pytest.mark.asyncio
async def test_no_relax_for_explicit_boolean_query(search_service, monkeypatch):
    """Explicit boolean query should remain strict and avoid fallback retries."""
    call_texts: list[str | None] = []

    async def fake_search(**kwargs):
        call_texts.append(kwargs.get("search_text"))
        return []

    monkeypatch.setattr(search_service.repository, "search", fake_search)

    await search_service.search(
        SearchQuery(text="term1 AND term2", retrieval_mode=SearchRetrievalMode.FTS)
    )

    assert call_texts == ["term1 AND term2"]


@pytest.mark.asyncio
async def test_no_relax_for_quoted_phrase_query(search_service, monkeypatch):
    """Quoted phrase query should remain strict and avoid fallback retries."""
    call_texts: list[str | None] = []

    async def fake_search(**kwargs):
        call_texts.append(kwargs.get("search_text"))
        return []

    monkeypatch.setattr(search_service.repository, "search", fake_search)

    await search_service.search(
        SearchQuery(text='"weekly standup"', retrieval_mode=SearchRetrievalMode.FTS)
    )

    assert call_texts == ['"weekly standup"']


@pytest.mark.asyncio
async def test_no_relax_for_two_term_query(search_service, monkeypatch):
    """Two-term queries should remain strict to avoid short-query false positives."""
    call_texts: list[str | None] = []

    async def fake_search(**kwargs):
        call_texts.append(kwargs.get("search_text"))
        return []

    monkeypatch.setattr(search_service.repository, "search", fake_search)

    await search_service.search(
        SearchQuery(text="new feature", retrieval_mode=SearchRetrievalMode.FTS)
    )

    assert call_texts == ["new feature"]


@pytest.mark.asyncio
async def test_no_relax_for_numeric_identifier_query(search_service, monkeypatch):
    """Queries with numeric identifiers should remain strict to avoid OR over-broadening."""
    call_texts: list[str | None] = []

    async def fake_search(**kwargs):
        call_texts.append(kwargs.get("search_text"))
        return []

    monkeypatch.setattr(search_service.repository, "search", fake_search)

    await search_service.search(
        SearchQuery(text="root note 1", retrieval_mode=SearchRetrievalMode.FTS)
    )

    assert call_texts == ["root note 1"]


@pytest.mark.asyncio
@pytest.mark.parametrize("retrieval_mode", [SearchRetrievalMode.VECTOR, SearchRetrievalMode.HYBRID])
async def test_no_relax_for_vector_or_hybrid_modes(search_service, monkeypatch, retrieval_mode):
    """Vector and hybrid modes should never run the FTS fallback retry path."""
    call_texts: list[str | None] = []

    async def fake_search(**kwargs):
        call_texts.append(kwargs.get("search_text"))
        return []

    monkeypatch.setattr(search_service.repository, "search", fake_search)

    await search_service.search(
        SearchQuery(text="who are our competitors", retrieval_mode=retrieval_mode)
    )

    assert call_texts == ["who are our competitors"]


# Tests for frontmatter tag search functionality


@pytest.mark.asyncio
async def test_extract_entity_tags_list_format(search_service, session_maker):
    """Test tag extraction from list format in entity metadata."""
    from basic_memory.models import Entity

    entity = Entity(
        title="Test Entity",
        note_type="note",
        entity_metadata={"tags": ["business", "strategy", "planning"]},
        content_type="text/markdown",
        file_path="test/business-strategy.md",
        project_id=1,
    )

    tags = search_service._extract_entity_tags(entity)
    assert tags == ["business", "strategy", "planning"]


@pytest.mark.asyncio
async def test_extract_entity_tags_string_format(search_service, session_maker):
    """Test tag extraction from string format in entity metadata."""
    from basic_memory.models import Entity

    entity = Entity(
        title="Test Entity",
        note_type="note",
        entity_metadata={"tags": "['documentation', 'tools', 'best-practices']"},
        content_type="text/markdown",
        file_path="test/docs.md",
        project_id=1,
    )

    tags = search_service._extract_entity_tags(entity)
    assert tags == ["documentation", "tools", "best-practices"]


@pytest.mark.asyncio
async def test_extract_entity_tags_empty_list(search_service, session_maker):
    """Test tag extraction from empty list in entity metadata."""
    from basic_memory.models import Entity

    entity = Entity(
        title="Test Entity",
        note_type="note",
        entity_metadata={"tags": []},
        content_type="text/markdown",
        file_path="test/empty-tags.md",
        project_id=1,
    )

    tags = search_service._extract_entity_tags(entity)
    assert tags == []


@pytest.mark.asyncio
async def test_extract_entity_tags_empty_string(search_service, session_maker):
    """Test tag extraction from empty string in entity metadata."""
    from basic_memory.models import Entity

    entity = Entity(
        title="Test Entity",
        note_type="note",
        entity_metadata={"tags": "[]"},
        content_type="text/markdown",
        file_path="test/empty-string-tags.md",
        project_id=1,
    )

    tags = search_service._extract_entity_tags(entity)
    assert tags == []


@pytest.mark.asyncio
async def test_extract_entity_tags_no_metadata(search_service, session_maker):
    """Test tag extraction when entity has no metadata."""
    from basic_memory.models import Entity

    entity = Entity(
        title="Test Entity",
        note_type="note",
        entity_metadata=None,
        content_type="text/markdown",
        file_path="test/no-metadata.md",
        project_id=1,
    )

    tags = search_service._extract_entity_tags(entity)
    assert tags == []


@pytest.mark.asyncio
async def test_extract_entity_tags_no_tags_key(search_service, session_maker):
    """Test tag extraction when metadata exists but has no tags key."""
    from basic_memory.models import Entity

    entity = Entity(
        title="Test Entity",
        note_type="note",
        entity_metadata={"title": "Some Title", "type": "note"},
        content_type="text/markdown",
        file_path="test/no-tags-key.md",
        project_id=1,
    )

    tags = search_service._extract_entity_tags(entity)
    assert tags == []


@pytest.mark.asyncio
async def test_search_tag_prefix_maps_to_tags_filter(search_service, entity_service):
    """`tag:foo` prefix should translate to tags filter and return tagged entities."""
    from basic_memory.schemas import Entity as EntitySchema

    tagged_entity, _ = await entity_service.create_or_update_entity(
        EntitySchema(
            title="Tagged Note Missing",
            directory="tags",
            note_type="note",
            content="# Tagged Note",
            entity_metadata={"tags": ["tier1", "alpha"]},
        )
    )

    await search_service.index_entity(tagged_entity)

    results = await search_service.search(SearchQuery(text="tag:tier1"))

    assert any(r.permalink == tagged_entity.permalink for r in results)


@pytest.mark.asyncio
async def test_search_tag_prefix_with_nonexistent_tag_returns_empty(search_service, entity_service):
    """`tag:missing` should return no results when tags do not match."""
    from basic_memory.schemas import Entity as EntitySchema

    tagged_entity, _ = await entity_service.create_or_update_entity(
        EntitySchema(
            title="Tagged Note",
            directory="tags",
            note_type="note",
            content="# Tagged Note",
            entity_metadata={"tags": ["tier1", "alpha"]},
        )
    )

    await search_service.index_entity(tagged_entity)

    results = await search_service.search(SearchQuery(text="tag:missing"))

    assert not results


@pytest.mark.asyncio
async def test_search_tag_prefix_multiple_tags_requires_all(search_service, entity_service):
    """`tag:tier1,alpha` should match entities containing all listed tags."""
    from basic_memory.schemas import Entity as EntitySchema

    tagged_entity, _ = await entity_service.create_or_update_entity(
        EntitySchema(
            title="Multi Tagged Note",
            directory="tags/multi",
            note_type="note",
            content="# Tagged Note",
            entity_metadata={"tags": ["tier1", "alpha"]},
        )
    )

    await search_service.index_entity(tagged_entity)

    results = await search_service.search(SearchQuery(text="tag:tier1,alpha"))

    assert any(r.permalink == tagged_entity.permalink for r in results)


@pytest.mark.asyncio
async def test_search_by_frontmatter_tags(search_service, session_maker, test_project):
    """Test that entities can be found by searching for their frontmatter tags."""
    from basic_memory.repository import EntityRepository

    entity_repo = EntityRepository(session_maker, project_id=test_project.id)

    # Create entity with tags
    from datetime import datetime

    entity_data = {
        "title": "Business Strategy Guide",
        "note_type": "note",
        "entity_metadata": {"tags": ["business", "strategy", "planning", "organization"]},
        "content_type": "text/markdown",
        "file_path": "guides/business-strategy.md",
        "permalink": "guides/business-strategy",
        "project_id": test_project.id,
        "created_at": datetime.now(),
        "updated_at": datetime.now(),
    }

    entity = await entity_repo.create(entity_data)

    await search_service.index_entity(entity, content="")

    # Search for entities by tag
    results = await search_service.search(SearchQuery(text="business"))
    assert len(results) >= 1

    # Check that our entity is in the results
    entity_found = False
    for result in results:
        if result.title == "Business Strategy Guide":
            entity_found = True
            break
    assert entity_found, "Entity with 'business' tag should be found in search results"

    # Test searching by another tag
    results = await search_service.search(SearchQuery(text="planning"))
    assert len(results) >= 1

    entity_found = False
    for result in results:
        if result.title == "Business Strategy Guide":
            entity_found = True
            break
    assert entity_found, "Entity with 'planning' tag should be found in search results"


@pytest.mark.asyncio
async def test_search_by_frontmatter_tags_string_format(
    search_service, session_maker, test_project
):
    """Test that entities with string format tags can be found in search."""
    from basic_memory.repository import EntityRepository

    entity_repo = EntityRepository(session_maker, project_id=test_project.id)

    # Create entity with tags in string format
    from datetime import datetime

    entity_data = {
        "title": "Documentation Guidelines",
        "note_type": "note",
        "entity_metadata": {"tags": "['documentation', 'tools', 'best-practices']"},
        "content_type": "text/markdown",
        "file_path": "guides/documentation.md",
        "permalink": "guides/documentation",
        "project_id": test_project.id,
        "created_at": datetime.now(),
        "updated_at": datetime.now(),
    }

    entity = await entity_repo.create(entity_data)

    await search_service.index_entity(entity, content="")

    # Search for entities by tag
    results = await search_service.search(SearchQuery(text="documentation"))
    assert len(results) >= 1

    # Check that our entity is in the results
    entity_found = False
    for result in results:
        if result.title == "Documentation Guidelines":
            entity_found = True
            break
    assert entity_found, "Entity with 'documentation' tag should be found in search results"


@pytest.mark.asyncio
async def test_search_special_characters_in_title(search_service, session_maker, test_project):
    """Test that entities with special characters in titles can be searched without FTS5 syntax errors."""
    from basic_memory.repository import EntityRepository

    entity_repo = EntityRepository(session_maker, project_id=test_project.id)

    # Create entities with special characters that could cause FTS5 syntax errors
    special_titles = [
        "Note with spaces",
        "Note-with-dashes",
        "Note_with_underscores",
        "Note (with parentheses)",  # This is the problematic one
        "Note & Symbols!",
        "Note [with brackets]",
        "Note {with braces}",
        'Note "with quotes"',
        "Note 'with apostrophes'",
    ]

    entities = []
    for i, title in enumerate(special_titles):
        from datetime import datetime

        entity_data = {
            "title": title,
            "note_type": "note",
            "entity_metadata": {"tags": ["special", "characters"]},
            "content_type": "text/markdown",
            "file_path": f"special/{title}.md",
            "permalink": f"special/note-{i}",
            "project_id": test_project.id,
            "created_at": datetime.now(),
            "updated_at": datetime.now(),
        }

        entity = await entity_repo.create(entity_data)
        entities.append(entity)

    # Index all entities
    for entity in entities:
        await search_service.index_entity(entity, content="")

    # Test searching for each title - this should not cause FTS5 syntax errors
    for title in special_titles:
        results = await search_service.search(SearchQuery(title=title))

        # Should find the entity without throwing FTS5 syntax errors
        entity_found = False
        for result in results:
            if result.title == title:
                entity_found = True
                break

        assert entity_found, f"Entity with title '{title}' should be found in search results"


@pytest.mark.asyncio
async def test_search_title_with_parentheses_specific(search_service, session_maker, test_project):
    """Test searching specifically for title with parentheses to reproduce FTS5 error."""
    from basic_memory.repository import EntityRepository

    entity_repo = EntityRepository(session_maker, project_id=test_project.id)

    # Create the problematic entity
    from datetime import datetime

    entity_data = {
        "title": "Note (with parentheses)",
        "note_type": "note",
        "entity_metadata": {"tags": ["test"]},
        "content_type": "text/markdown",
        "file_path": "special/Note (with parentheses).md",
        "permalink": "special/note-with-parentheses",
        "project_id": test_project.id,
        "created_at": datetime.now(),
        "updated_at": datetime.now(),
    }

    entity = await entity_repo.create(entity_data)

    # Index the entity
    await search_service.index_entity(entity, content="")

    # Test searching for the title - this should not cause FTS5 syntax errors
    search_query = SearchQuery(title="Note (with parentheses)")
    results = await search_service.search(search_query)

    # Should find the entity without throwing FTS5 syntax errors
    assert len(results) >= 1
    assert any(result.title == "Note (with parentheses)" for result in results)


@pytest.mark.asyncio
async def test_search_title_via_repository_direct(search_service, session_maker, test_project):
    """Test searching via search repository directly to isolate the FTS5 error."""
    from basic_memory.repository import EntityRepository

    entity_repo = EntityRepository(session_maker, project_id=test_project.id)

    # Create the problematic entity
    from datetime import datetime

    entity_data = {
        "title": "Note (with parentheses)",
        "note_type": "note",
        "entity_metadata": {"tags": ["test"]},
        "content_type": "text/markdown",
        "file_path": "special/Note (with parentheses).md",
        "permalink": "special/note-with-parentheses",
        "project_id": test_project.id,
        "created_at": datetime.now(),
        "updated_at": datetime.now(),
    }

    entity = await entity_repo.create(entity_data)

    # Index the entity
    await search_service.index_entity(entity, content="")

    # Test searching via repository directly - this reproduces the error path
    results = await search_service.repository.search(
        title="Note (with parentheses)",
        limit=10,
        offset=0,
    )

    # Should find the entity without throwing FTS5 syntax errors
    assert len(results) >= 1
    assert any(result.title == "Note (with parentheses)" for result in results)


# Tests for duplicate observation permalink deduplication


@pytest.mark.asyncio
async def test_index_entity_with_duplicate_observations(
    search_service, session_maker, test_project
):
    """Test that indexing an entity with duplicate observations doesn't cause unique constraint violations.

    Two observations with the same category and content generate identical permalinks,
    which would violate the unique constraint on the search_index table.
    """
    from basic_memory.repository import EntityRepository, ObservationRepository
    from datetime import datetime

    entity_repo = EntityRepository(session_maker, project_id=test_project.id)
    obs_repo = ObservationRepository(session_maker, project_id=test_project.id)

    # Create entity
    entity_data = {
        "title": "Entity With Duplicate Observations",
        "note_type": "note",
        "entity_metadata": {},
        "content_type": "text/markdown",
        "file_path": "test/duplicate-obs.md",
        "permalink": "test/duplicate-obs",
        "project_id": test_project.id,
        "created_at": datetime.now(),
        "updated_at": datetime.now(),
    }

    entity = await entity_repo.create(entity_data)

    # Create duplicate observations - same category and content
    duplicate_content = "This is a duplicated observation"
    await obs_repo.create(
        {"entity_id": entity.id, "category": "note", "content": duplicate_content}
    )
    await obs_repo.create(
        {"entity_id": entity.id, "category": "note", "content": duplicate_content}
    )

    # Reload entity with observations (get_by_permalink eagerly loads observations)
    entity = await entity_repo.get_by_permalink("test/duplicate-obs")

    # Verify we have duplicate observations
    assert len(entity.observations) == 2
    assert entity.observations[0].permalink == entity.observations[1].permalink

    # This should not raise a unique constraint violation
    await search_service.index_entity(entity, content="")

    # Verify entity is searchable
    results = await search_service.search(SearchQuery(text="Duplicate Observations"))
    assert len(results) >= 1
    assert any(r.title == "Entity With Duplicate Observations" for r in results)


@pytest.mark.asyncio
async def test_index_entity_dedupes_observations_by_permalink(
    search_service, session_maker, test_project
):
    """Test that only unique observation permalinks are indexed.

    When an entity has observations with identical permalinks, only the first one
    should be indexed to avoid unique constraint violations.
    """
    from basic_memory.repository import EntityRepository, ObservationRepository
    from datetime import datetime

    entity_repo = EntityRepository(session_maker, project_id=test_project.id)
    obs_repo = ObservationRepository(session_maker, project_id=test_project.id)

    # Create entity
    entity_data = {
        "title": "Dedupe Test Entity",
        "note_type": "note",
        "entity_metadata": {},
        "content_type": "text/markdown",
        "file_path": "test/dedupe-test.md",
        "permalink": "test/dedupe-test",
        "project_id": test_project.id,
        "created_at": datetime.now(),
        "updated_at": datetime.now(),
    }

    entity = await entity_repo.create(entity_data)

    # Create three observations: two duplicates and one unique
    duplicate_content = "Duplicate observation content"
    unique_content = "Unique observation content"

    await obs_repo.create(
        {"entity_id": entity.id, "category": "note", "content": duplicate_content}
    )
    await obs_repo.create(
        {"entity_id": entity.id, "category": "note", "content": duplicate_content}
    )
    await obs_repo.create({"entity_id": entity.id, "category": "note", "content": unique_content})

    # Reload entity with observations (get_by_permalink eagerly loads observations)
    entity = await entity_repo.get_by_permalink("test/dedupe-test")
    assert len(entity.observations) == 3

    # Index the entity
    await search_service.index_entity(entity, content="")

    # Search for the unique observation - should find it
    results = await search_service.search(SearchQuery(text="Unique observation"))
    assert len(results) >= 1

    # Search for duplicate observation - should find it (only one indexed)
    results = await search_service.search(SearchQuery(text="Duplicate observation"))
    assert len(results) >= 1


@pytest.mark.asyncio
async def test_index_entity_multiple_categories_same_content(
    search_service, session_maker, test_project
):
    """Test that observations with same content but different categories are not deduped.

    The permalink includes the category, so observations with different categories
    but same content should have different permalinks and both be indexed.
    """
    from basic_memory.repository import EntityRepository, ObservationRepository
    from datetime import datetime

    entity_repo = EntityRepository(session_maker, project_id=test_project.id)
    obs_repo = ObservationRepository(session_maker, project_id=test_project.id)

    # Create entity
    entity_data = {
        "title": "Multi Category Entity",
        "note_type": "note",
        "entity_metadata": {},
        "content_type": "text/markdown",
        "file_path": "test/multi-category.md",
        "permalink": "test/multi-category",
        "project_id": test_project.id,
        "created_at": datetime.now(),
        "updated_at": datetime.now(),
    }

    entity = await entity_repo.create(entity_data)

    # Create observations with same content but different categories
    shared_content = "Shared content across categories"
    await obs_repo.create({"entity_id": entity.id, "category": "tech", "content": shared_content})
    await obs_repo.create({"entity_id": entity.id, "category": "design", "content": shared_content})

    # Reload entity with observations (get_by_permalink eagerly loads observations)
    entity = await entity_repo.get_by_permalink("test/multi-category")
    assert len(entity.observations) == 2

    # Verify permalinks are different due to different categories
    permalinks = {obs.permalink for obs in entity.observations}
    assert len(permalinks) == 2  # Should be 2 unique permalinks

    # Index the entity - both should be indexed since permalinks differ
    await search_service.index_entity(entity, content="")

    # Search for the shared content - should find both observations
    results = await search_service.search(SearchQuery(text="Shared content"))
    assert len(results) >= 2


# Tests for NUL byte stripping


def test_strip_nul_removes_nul_bytes():
    """_strip_nul removes \\x00 from strings."""
    assert _strip_nul("hello\x00world") == "helloworld"
    assert _strip_nul("\x00\x00\x00") == ""
    assert _strip_nul("clean string") == "clean string"


@pytest.mark.asyncio
async def test_index_entity_markdown_strips_nul_bytes(search_service, session_maker, test_project):
    """Content with NUL bytes should be stripped before indexing.

    rclone preallocation on virtual filesystems (e.g. Google Drive File Stream)
    can pad files with \\x00 bytes, causing PostgreSQL CharacterNotInRepertoireError.

    Note: NUL bytes arrive via file content read from disk, not from the database.
    Postgres rejects \\x00 in text columns at the ORM level, so we only test
    the content path (passed to index_entity) rather than observation creation.
    """
    from basic_memory.repository import EntityRepository
    from basic_memory.repository.search_repository import SearchRepository

    entity_repo = EntityRepository(session_maker, project_id=test_project.id)

    entity_data = {
        "title": "NUL Test Entity",
        "note_type": "note",
        "entity_metadata": {},
        "content_type": "text/markdown",
        "file_path": "test/nul-test.md",
        "permalink": "test/nul-test",
        "project_id": test_project.id,
        "created_at": datetime.now(),
        "updated_at": datetime.now(),
    }
    entity = await entity_repo.create(entity_data)
    entity = await entity_repo.get_by_permalink("test/nul-test")

    # Index with NUL-containing content (simulates rclone-preallocated file)
    nul_content = "# NUL Test\x00\x00\nSome content\x00here"
    await search_service.index_entity(entity, content=nul_content)

    # Verify no NUL bytes in stored search index rows
    search_repo: SearchRepository = search_service.repository
    results = await search_repo.search(permalink_match="test/nul-test*")
    for row in results:
        if row.content_snippet:
            assert "\x00" not in row.content_snippet, (
                f"NUL found in content_snippet for {row.permalink}"
            )


@pytest.mark.asyncio
async def test_reindex_vectors(search_service, session_maker, test_project):
    """Test that reindex_vectors processes all entities and reports stats."""
    from basic_memory.repository import EntityRepository
    from datetime import datetime

    entity_repo = EntityRepository(session_maker, project_id=test_project.id)

    # Create some entities
    for i in range(3):
        entity = await entity_repo.create(
            {
                "title": f"Vector Test Entity {i}",
                "note_type": "note",
                "entity_metadata": {},
                "content_type": "text/markdown",
                "file_path": f"test/vector-test-{i}.md",
                "permalink": f"test/vector-test-{i}",
                "project_id": test_project.id,
                "created_at": datetime.now(),
                "updated_at": datetime.now(),
            }
        )
        await search_service.index_entity(entity, content=f"Content for entity {i}")

    # Track progress calls
    progress_calls = []

    def on_progress(entity_id, index, total):
        progress_calls.append((entity_id, index, total))

    stats = await search_service.reindex_vectors(progress_callback=on_progress)

    # Should have processed at least 3 entities
    assert stats["total_entities"] >= 3
    # embedded + errors should equal total
    assert stats["embedded"] + stats["errors"] == stats["total_entities"]
    # Should have gotten progress callbacks
    assert len(progress_calls) == stats["total_entities"]
    # Progress indices should be sequential
    for i, (_, index, total) in enumerate(progress_calls):
        assert index == i
        assert total == stats["total_entities"]


@pytest.mark.asyncio
async def test_reindex_vectors_no_callback(search_service, session_maker, test_project):
    """Test reindex_vectors works without a progress callback."""
    from basic_memory.repository import EntityRepository
    from datetime import datetime

    entity_repo = EntityRepository(session_maker, project_id=test_project.id)
    entity = await entity_repo.create(
        {
            "title": "No Callback Entity",
            "note_type": "note",
            "entity_metadata": {},
            "content_type": "text/markdown",
            "file_path": "test/no-callback.md",
            "permalink": "test/no-callback",
            "project_id": test_project.id,
            "created_at": datetime.now(),
            "updated_at": datetime.now(),
        }
    )
    await search_service.index_entity(entity, content="Test content")

    stats = await search_service.reindex_vectors()
    assert stats["total_entities"] >= 1
    assert stats["embedded"] + stats["errors"] == stats["total_entities"]
