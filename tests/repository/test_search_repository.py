"""Tests for the SearchRepository."""

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import text

from basic_memory import db
from basic_memory.models import Entity
from basic_memory.models.project import Project
from basic_memory.repository.search_repository import SearchIndexRow
from basic_memory.repository.postgres_search_repository import PostgresSearchRepository
from basic_memory.schemas.search import SearchItemType


def is_postgres_backend(search_repository):
    """Helper to check if search repository is Postgres-based."""
    return isinstance(search_repository, PostgresSearchRepository)


@pytest_asyncio.fixture
async def search_entity(session_maker, test_project: Project):
    """Create a test entity for search testing."""
    async with db.scoped_session(session_maker) as session:
        entity = Entity(
            project_id=test_project.id,
            title="Search Test Entity",
            entity_type="test",
            permalink="test/search-test-entity",
            file_path="test/search_test_entity.md",
            content_type="text/markdown",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(entity)
        await session.flush()
        return entity


@pytest_asyncio.fixture
async def second_project(project_repository):
    """Create a second project for testing project isolation."""
    project_data = {
        "name": "Second Test Project",
        "description": "Another project for testing",
        "path": "/second/project/path",
        "is_active": True,
        "is_default": None,
    }
    return await project_repository.create(project_data)


@pytest_asyncio.fixture
async def second_project_repository(session_maker, second_project, search_repository):
    """Create a backend-appropriate repository for the second project.

    Uses the same type as search_repository to ensure backend consistency.
    """
    # Use the same repository class as the main search_repository
    return type(search_repository)(session_maker, project_id=second_project.id)


@pytest_asyncio.fixture
async def second_entity(session_maker, second_project: Project):
    """Create a test entity in the second project."""
    async with db.scoped_session(session_maker) as session:
        entity = Entity(
            project_id=second_project.id,
            title="Second Project Entity",
            entity_type="test",
            permalink="test/second-project-entity",
            file_path="test/second_project_entity.md",
            content_type="text/markdown",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(entity)
        await session.flush()
        return entity


@pytest.mark.asyncio
async def test_init_search_index(search_repository, app_config):
    """Test that search index can be initialized."""
    from basic_memory.config import DatabaseBackend

    await search_repository.init_search_index()

    # Verify search_index table exists (backend-specific query)
    async with db.scoped_session(search_repository.session_maker) as session:
        if app_config.database_backend == DatabaseBackend.POSTGRES:
            # For Postgres, query information_schema
            result = await session.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_name = 'search_index';"
                )
            )
        else:
            # For SQLite, query sqlite_master
            result = await session.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name='search_index';")
            )

        table_name = result.scalar()
        assert table_name == "search_index"


@pytest.mark.asyncio
async def test_index_item(search_repository, search_entity):
    """Test indexing an item with project_id."""
    # Create search index row for the entity
    search_row = SearchIndexRow(
        id=search_entity.id,
        type=SearchItemType.ENTITY.value,
        title=search_entity.title,
        content_stems="search test entity content",
        content_snippet="This is a test entity for search",
        permalink=search_entity.permalink,
        file_path=search_entity.file_path,
        entity_id=search_entity.id,
        metadata={"entity_type": search_entity.entity_type},
        created_at=search_entity.created_at,
        updated_at=search_entity.updated_at,
        project_id=search_repository.project_id,
    )

    # Index the item
    await search_repository.index_item(search_row)

    # Search for the item
    results = await search_repository.search(search_text="search test")

    # Verify we found the item
    assert len(results) == 1
    assert results[0].title == search_entity.title
    assert results[0].project_id == search_repository.project_id


@pytest.mark.asyncio
async def test_project_isolation(
    search_repository, second_project_repository, search_entity, second_entity
):
    """Test that search is isolated by project."""
    # Index entities in both projects
    search_row1 = SearchIndexRow(
        id=search_entity.id,
        type=SearchItemType.ENTITY.value,
        title=search_entity.title,
        content_stems="unique first project content",
        content_snippet="This is a test entity in the first project",
        permalink=search_entity.permalink,
        file_path=search_entity.file_path,
        entity_id=search_entity.id,
        metadata={"entity_type": search_entity.entity_type},
        created_at=search_entity.created_at,
        updated_at=search_entity.updated_at,
        project_id=search_repository.project_id,
    )

    search_row2 = SearchIndexRow(
        id=second_entity.id,
        type=SearchItemType.ENTITY.value,
        title=second_entity.title,
        content_stems="unique second project content",
        content_snippet="This is a test entity in the second project",
        permalink=second_entity.permalink,
        file_path=second_entity.file_path,
        entity_id=second_entity.id,
        metadata={"entity_type": second_entity.entity_type},
        created_at=second_entity.created_at,
        updated_at=second_entity.updated_at,
        project_id=second_project_repository.project_id,
    )

    # Index items in their respective repositories
    await search_repository.index_item(search_row1)
    await second_project_repository.index_item(search_row2)

    # Search in first project
    results1 = await search_repository.search(search_text="unique first")
    assert len(results1) == 1
    assert results1[0].title == search_entity.title
    assert results1[0].project_id == search_repository.project_id

    # Search in second project
    results2 = await second_project_repository.search(search_text="unique second")
    assert len(results2) == 1
    assert results2[0].title == second_entity.title
    assert results2[0].project_id == second_project_repository.project_id

    # Make sure first project can't see second project's content
    results_cross1 = await search_repository.search(search_text="unique second")
    assert len(results_cross1) == 0

    # Make sure second project can't see first project's content
    results_cross2 = await second_project_repository.search(search_text="unique first")
    assert len(results_cross2) == 0


@pytest.mark.asyncio
async def test_delete_by_permalink(search_repository, search_entity):
    """Test deleting an item by permalink respects project isolation."""
    # Index the item
    search_row = SearchIndexRow(
        id=search_entity.id,
        type=SearchItemType.ENTITY.value,
        title=search_entity.title,
        content_stems="content to delete",
        content_snippet="This content should be deleted",
        permalink=search_entity.permalink,
        file_path=search_entity.file_path,
        entity_id=search_entity.id,
        metadata={"entity_type": search_entity.entity_type},
        created_at=search_entity.created_at,
        updated_at=search_entity.updated_at,
        project_id=search_repository.project_id,
    )

    await search_repository.index_item(search_row)

    # Verify it exists
    results = await search_repository.search(search_text="content to delete")
    assert len(results) == 1

    # Delete by permalink
    await search_repository.delete_by_permalink(search_entity.permalink)

    # Verify it's gone
    results_after = await search_repository.search(search_text="content to delete")
    assert len(results_after) == 0


@pytest.mark.asyncio
async def test_delete_by_entity_id(search_repository, search_entity):
    """Test deleting an item by entity_id respects project isolation."""
    # Index the item
    search_row = SearchIndexRow(
        id=search_entity.id,
        type=SearchItemType.ENTITY.value,
        title=search_entity.title,
        content_stems="entity to delete",
        content_snippet="This entity should be deleted",
        permalink=search_entity.permalink,
        file_path=search_entity.file_path,
        entity_id=search_entity.id,
        metadata={"entity_type": search_entity.entity_type},
        created_at=search_entity.created_at,
        updated_at=search_entity.updated_at,
        project_id=search_repository.project_id,
    )

    await search_repository.index_item(search_row)

    # Verify it exists
    results = await search_repository.search(search_text="entity to delete")
    assert len(results) == 1

    # Delete by entity_id
    await search_repository.delete_by_entity_id(search_entity.id)

    # Verify it's gone
    results_after = await search_repository.search(search_text="entity to delete")
    assert len(results_after) == 0


@pytest.mark.asyncio
async def test_to_insert_includes_project_id(search_repository):
    """Test that the to_insert method includes project_id."""
    # Create a search index row with project_id
    row = SearchIndexRow(
        id=1234,
        type=SearchItemType.ENTITY.value,
        title="Test Title",
        content_stems="test content",
        content_snippet="test snippet",
        permalink="test/permalink",
        file_path="test/file.md",
        metadata={"test": "metadata"},
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        project_id=search_repository.project_id,
    )

    # Get insert data
    insert_data = row.to_insert()

    # Verify project_id is included
    assert "project_id" in insert_data
    assert insert_data["project_id"] == search_repository.project_id


def test_directory_property():
    """Test the directory property of SearchIndexRow."""
    # Test a file in a nested directory
    row1 = SearchIndexRow(
        id=1,
        type=SearchItemType.ENTITY.value,
        file_path="projects/notes/ideas.md",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        project_id=1,
    )
    assert row1.directory == "/projects/notes"

    # Test a file at the root level
    row2 = SearchIndexRow(
        id=2,
        type=SearchItemType.ENTITY.value,
        file_path="README.md",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        project_id=1,
    )
    assert row2.directory == "/"

    # Test a non-entity type with empty file_path
    row3 = SearchIndexRow(
        id=3,
        type=SearchItemType.OBSERVATION.value,
        file_path="",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        project_id=1,
    )
    assert row3.directory == ""


class TestSearchTermPreparation:
    """Test cases for search term preparation.

    Note: Tests with `[sqlite]` marker test SQLite FTS5-specific implementation details.
    Tests with `[asyncio-sqlite]` or `[asyncio-postgres]` test backend-agnostic functionality.
    """

    def test_simple_terms_get_prefix_wildcard(self, search_repository):
        """Simple alphanumeric terms should get prefix matching."""
        from basic_memory.repository.postgres_search_repository import PostgresSearchRepository

        if isinstance(search_repository, PostgresSearchRepository):
            # Postgres tsquery uses :* for prefix matching
            assert search_repository._prepare_search_term("hello") == "hello:*"
            assert search_repository._prepare_search_term("project") == "project:*"
            assert search_repository._prepare_search_term("test123") == "test123:*"
        else:
            # SQLite FTS5 uses * for prefix matching
            assert search_repository._prepare_search_term("hello") == "hello*"
            assert search_repository._prepare_search_term("project") == "project*"
            assert search_repository._prepare_search_term("test123") == "test123*"

    def test_terms_with_existing_wildcard_unchanged(self, search_repository):
        """Terms that already contain * should remain unchanged."""
        if is_postgres_backend(search_repository):
            # Postgres uses different syntax (:* instead of *)
            assert search_repository._prepare_search_term("hello*") == "hello:*"
            assert search_repository._prepare_search_term("test*world") == "test:*world"
        else:
            assert search_repository._prepare_search_term("hello*") == "hello*"
            assert search_repository._prepare_search_term("test*world") == "test*world"

    def test_boolean_operators_preserved(self, search_repository):
        """Boolean operators should be preserved without modification."""
        if is_postgres_backend(search_repository):
            # Postgres converts AND/OR/NOT to &/|/!
            assert search_repository._prepare_search_term("hello AND world") == "hello & world"
            assert search_repository._prepare_search_term("cat OR dog") == "cat | dog"
            # NOT must be converted to "& !" for proper tsquery syntax
            assert (
                search_repository._prepare_search_term("project NOT meeting")
                == "project & !meeting"
            )
            assert (
                search_repository._prepare_search_term("(hello AND world) OR test")
                == "(hello & world) | test"
            )
        else:
            assert search_repository._prepare_search_term("hello AND world") == "hello AND world"
            assert search_repository._prepare_search_term("cat OR dog") == "cat OR dog"
            assert (
                search_repository._prepare_search_term("project NOT meeting")
                == "project NOT meeting"
            )
            assert (
                search_repository._prepare_search_term("(hello AND world) OR test")
                == "(hello AND world) OR test"
            )

    def test_hyphenated_terms_with_boolean_operators(self, search_repository):
        """Hyphenated terms with Boolean operators should be properly quoted."""
        if is_postgres_backend(search_repository):
            pytest.skip("This test is for SQLite FTS5-specific quoting behavior")

        # Test the specific case from the GitHub issue
        result = search_repository._prepare_search_term("tier1-test AND unicode")
        assert result == '"tier1-test" AND unicode'

        # Test other hyphenated Boolean combinations
        assert (
            search_repository._prepare_search_term("multi-word OR single")
            == '"multi-word" OR single'
        )
        assert (
            search_repository._prepare_search_term("well-formed NOT badly-formed")
            == '"well-formed" NOT "badly-formed"'
        )
        assert (
            search_repository._prepare_search_term("test-case AND (hello OR world)")
            == '"test-case" AND (hello OR world)'
        )

        # Test mixed special characters with Boolean operators
        assert (
            search_repository._prepare_search_term("config.json AND test-file")
            == '"config.json" AND "test-file"'
        )
        assert (
            search_repository._prepare_search_term("C++ OR python-script")
            == '"C++" OR "python-script"'
        )

    def test_programming_terms_should_work(self, search_repository):
        """Programming-related terms with special chars should be searchable."""
        if is_postgres_backend(search_repository):
            pytest.skip("This test is for SQLite FTS5-specific behavior")

        # These should be quoted to handle special characters safely
        assert search_repository._prepare_search_term("C++") == '"C++"*'
        assert search_repository._prepare_search_term("function()") == '"function()"*'
        assert search_repository._prepare_search_term("email@domain.com") == '"email@domain.com"*'
        assert search_repository._prepare_search_term("array[index]") == '"array[index]"*'
        assert search_repository._prepare_search_term("config.json") == '"config.json"*'

    def test_malformed_fts5_syntax_quoted(self, search_repository):
        """Malformed FTS5 syntax should be quoted to prevent errors."""
        if is_postgres_backend(search_repository):
            pytest.skip("This test is for SQLite FTS5-specific behavior")

        # Multiple operators without proper syntax
        assert search_repository._prepare_search_term("+++invalid+++") == '"+++invalid+++"*'
        assert search_repository._prepare_search_term("!!!error!!!") == '"!!!error!!!"*'
        assert search_repository._prepare_search_term("@#$%^&*()") == '"@#$%^&*()"*'

    def test_quoted_strings_handled_properly(self, search_repository):
        """Strings with quotes should have quotes escaped."""
        if is_postgres_backend(search_repository):
            pytest.skip("This test is for SQLite FTS5-specific behavior")

        assert search_repository._prepare_search_term('say "hello"') == '"say ""hello"""*'
        assert search_repository._prepare_search_term("it's working") == '"it\'s working"*'

    def test_file_paths_no_prefix_wildcard(self, search_repository):
        """File paths should not get prefix wildcards."""
        if is_postgres_backend(search_repository):
            pytest.skip("This test is for SQLite FTS5-specific behavior")

        assert (
            search_repository._prepare_search_term("config.json", is_prefix=False)
            == '"config.json"'
        )
        assert (
            search_repository._prepare_search_term("docs/readme.md", is_prefix=False)
            == '"docs/readme.md"'
        )

    def test_spaces_handled_correctly(self, search_repository):
        """Terms with spaces should use boolean AND for word order independence."""
        if is_postgres_backend(search_repository):
            pytest.skip("This test is for SQLite FTS5-specific behavior")

        assert search_repository._prepare_search_term("hello world") == "hello* AND world*"
        assert (
            search_repository._prepare_search_term("project planning") == "project* AND planning*"
        )

    def test_version_strings_with_dots_handled_correctly(self, search_repository):
        """Version strings with dots should be quoted to prevent FTS5 syntax errors."""
        if is_postgres_backend(search_repository):
            pytest.skip("This test is for SQLite FTS5-specific behavior")

        # This reproduces the bug where "Basic Memory v0.13.0b2" becomes "Basic* AND Memory* AND v0.13.0b2*"
        # which causes FTS5 syntax errors because v0.13.0b2* is not valid FTS5 syntax
        result = search_repository._prepare_search_term("Basic Memory v0.13.0b2")
        # Should be quoted because of dots in v0.13.0b2
        assert result == '"Basic Memory v0.13.0b2"*'

    def test_mixed_special_characters_in_multi_word_queries(self, search_repository):
        """Multi-word queries with special characters in any word should be fully quoted."""
        if is_postgres_backend(search_repository):
            pytest.skip("This test is for SQLite FTS5-specific behavior")

        # Any word containing special characters should cause the entire phrase to be quoted
        assert search_repository._prepare_search_term("config.json file") == '"config.json file"*'
        assert (
            search_repository._prepare_search_term("user@email.com account")
            == '"user@email.com account"*'
        )
        assert search_repository._prepare_search_term("node.js and react") == '"node.js and react"*'

    @pytest.mark.asyncio
    async def test_search_with_special_characters_returns_results(self, search_repository):
        """Integration test: search with special characters should work gracefully."""
        # This test ensures the search doesn't crash with FTS5 syntax errors

        # These should all return empty results gracefully, not crash
        results1 = await search_repository.search(search_text="C++")
        assert isinstance(results1, list)  # Should not crash

        results2 = await search_repository.search(search_text="function()")
        assert isinstance(results2, list)  # Should not crash

        results3 = await search_repository.search(search_text="+++malformed+++")
        assert isinstance(results3, list)  # Should not crash, return empty results

        results4 = await search_repository.search(search_text="email@domain.com")
        assert isinstance(results4, list)  # Should not crash

    @pytest.mark.asyncio
    async def test_boolean_search_still_works(self, search_repository):
        """Boolean search operations should continue to work."""
        # These should not crash and should respect boolean logic
        results1 = await search_repository.search(search_text="hello AND world")
        assert isinstance(results1, list)

        results2 = await search_repository.search(search_text="cat OR dog")
        assert isinstance(results2, list)

        results3 = await search_repository.search(search_text="project NOT meeting")
        assert isinstance(results3, list)

    @pytest.mark.asyncio
    async def test_permalink_match_exact_with_slash(self, search_repository):
        """Test exact permalink matching with slash (line 249 coverage)."""
        # This tests the exact match path: if "/" in permalink_text:
        results = await search_repository.search(permalink_match="test/path")
        assert isinstance(results, list)
        # Should use exact equality matching for paths with slashes

    @pytest.mark.asyncio
    async def test_permalink_match_simple_term(self, search_repository):
        """Test permalink matching with simple term (no slash)."""
        # This tests the simple term path that goes through _prepare_search_term
        results = await search_repository.search(permalink_match="simpleterm")
        assert isinstance(results, list)
        # Should use FTS5 MATCH for simple terms

    @pytest.mark.asyncio
    async def test_fts5_error_handling_database_error(self, search_repository):
        """Test that non-FTS5 database errors are properly re-raised."""
        import unittest.mock

        # Mock the scoped_session to raise a non-FTS5 error
        with unittest.mock.patch("basic_memory.db.scoped_session") as mock_scoped_session:
            mock_session = unittest.mock.AsyncMock()
            mock_scoped_session.return_value.__aenter__.return_value = mock_session

            # Simulate a database error that's NOT an FTS5 syntax error
            mock_session.execute.side_effect = Exception("Database connection failed")

            # This should re-raise the exception (not return empty list)
            with pytest.raises(Exception, match="Database connection failed"):
                await search_repository.search(search_text="test")

    @pytest.mark.asyncio
    async def test_version_string_search_integration(self, search_repository, search_entity):
        """Integration test: searching for version strings should work without FTS5 errors."""
        # Index an entity with version information
        search_row = SearchIndexRow(
            id=search_entity.id,
            type=SearchItemType.ENTITY.value,
            title="Basic Memory v0.13.0b2 Release",
            content_stems="basic memory version 0.13.0b2 beta release notes features",
            content_snippet="Basic Memory v0.13.0b2 is a beta release with new features",
            permalink=search_entity.permalink,
            file_path=search_entity.file_path,
            entity_id=search_entity.id,
            metadata={"entity_type": search_entity.entity_type},
            created_at=search_entity.created_at,
            updated_at=search_entity.updated_at,
            project_id=search_repository.project_id,
        )

        await search_repository.index_item(search_row)

        # This should not cause FTS5 syntax errors and should find the entity
        results = await search_repository.search(search_text="Basic Memory v0.13.0b2")
        assert len(results) == 1
        assert results[0].title == "Basic Memory v0.13.0b2 Release"

        # Test other version-like patterns
        results2 = await search_repository.search(search_text="v0.13.0b2")
        assert len(results2) == 1  # Should still find it due to content_stems

        # Test with other problematic patterns
        results3 = await search_repository.search(search_text="node.js version")
        assert isinstance(results3, list)  # Should not crash

    @pytest.mark.asyncio
    async def test_wildcard_only_search(self, search_repository, search_entity):
        """Test that wildcard-only search '*' doesn't cause FTS5 errors (line 243 coverage)."""
        # Index an entity for testing
        search_row = SearchIndexRow(
            id=search_entity.id,
            type=SearchItemType.ENTITY.value,
            title="Test Entity",
            content_stems="test entity content",
            content_snippet="This is a test entity",
            permalink=search_entity.permalink,
            file_path=search_entity.file_path,
            entity_id=search_entity.id,
            metadata={"entity_type": search_entity.entity_type},
            created_at=search_entity.created_at,
            updated_at=search_entity.updated_at,
            project_id=search_repository.project_id,
        )

        await search_repository.index_item(search_row)

        # Test wildcard-only search - should not crash and should return results
        results = await search_repository.search(search_text="*")
        assert isinstance(results, list)  # Should not crash
        assert len(results) >= 1  # Should return all results, including our test entity

        # Test empty string search - should also not crash
        results_empty = await search_repository.search(search_text="")
        assert isinstance(results_empty, list)  # Should not crash

        # Test whitespace-only search
        results_whitespace = await search_repository.search(search_text="   ")
        assert isinstance(results_whitespace, list)  # Should not crash

    def test_boolean_query_empty_parts_coverage(self, search_repository):
        """Test Boolean query parsing with empty parts (line 143 coverage)."""
        # Create queries that will result in empty parts after splitting
        result1 = search_repository._prepare_boolean_query(
            "hello AND  AND world"
        )  # Double operator
        assert "hello" in result1 and "world" in result1

        result2 = search_repository._prepare_boolean_query("  OR test")  # Leading operator
        assert "test" in result2

        result3 = search_repository._prepare_boolean_query("test OR  ")  # Trailing operator
        assert "test" in result3

    def test_parenthetical_term_quote_escaping(self, search_repository):
        """Test quote escaping in parenthetical terms (lines 190-191 coverage)."""
        if is_postgres_backend(search_repository):
            pytest.skip("This test is for SQLite FTS5-specific behavior")

        # Test term with quotes that needs escaping
        result = search_repository._prepare_parenthetical_term('(say "hello" world)')
        # Should escape quotes by doubling them
        assert '""hello""' in result

        # Test term with single quotes
        result2 = search_repository._prepare_parenthetical_term("(it's working)")
        assert "it's working" in result2

    def test_needs_quoting_empty_input(self, search_repository):
        """Test _needs_quoting with empty inputs (line 207 coverage)."""
        if is_postgres_backend(search_repository):
            pytest.skip("This test is for SQLite FTS5-specific behavior")

        # Test empty string
        assert not search_repository._needs_quoting("")

        # Test whitespace-only string
        assert not search_repository._needs_quoting("   ")

        # Test None-like cases
        assert not search_repository._needs_quoting("\t")

    def test_prepare_single_term_empty_input(self, search_repository):
        """Test _prepare_single_term with empty inputs (line 227 coverage)."""
        # Test empty string
        result1 = search_repository._prepare_single_term("")
        assert result1 == ""

        # Test whitespace-only string
        result2 = search_repository._prepare_single_term("   ")
        assert result2 == "   "  # Should return as-is

        # Test string that becomes empty after strip
        result3 = search_repository._prepare_single_term("\t\n")
        assert result3 == "\t\n"  # Should return original
