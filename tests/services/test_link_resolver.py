"""Tests for link resolution service."""

import uuid
from datetime import datetime, timezone

import pytest

import pytest_asyncio

from basic_memory.models.knowledge import Entity as EntityModel
from basic_memory.repository import EntityRepository
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
            note_type="component",
            directory="components",
            project=entity_service.repository.project_id,
        )
    )
    e2, _ = await entity_service.create_or_update_entity(
        EntitySchema(
            title="Service Config",
            note_type="config",
            directory="config",
            project=entity_service.repository.project_id,
        )
    )
    e3, _ = await entity_service.create_or_update_entity(
        EntitySchema(
            title="Auth Service",
            note_type="component",
            directory="components",
            project=entity_service.repository.project_id,
        )
    )
    e4, _ = await entity_service.create_or_update_entity(
        EntitySchema(
            title="Core Features",
            note_type="specs",
            directory="specs",
            project=entity_service.repository.project_id,
        )
    )
    e5, _ = await entity_service.create_or_update_entity(
        EntitySchema(
            title="Sub Features 1",
            note_type="specs",
            directory="specs/subspec",
            project=entity_service.repository.project_id,
        )
    )
    e6, _ = await entity_service.create_or_update_entity(
        EntitySchema(
            title="Sub Features 2",
            note_type="specs",
            directory="specs/subspec",
            project=entity_service.repository.project_id,
        )
    )

    # non markdown entity
    e7 = await entity_service.repository.add(
        EntityModel(
            title="Image.png",
            note_type="file",
            content_type="image/png",
            file_path="Image.png",
            permalink="image",  # Required for Postgres NOT NULL constraint
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            project_id=entity_service.repository.project_id,
        )
    )

    e8 = await entity_service.create_entity(  # duplicate title
        EntitySchema(
            title="Core Service",
            note_type="component",
            directory="components2",
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


@pytest.fixture
def project_prefix(test_entities) -> str:
    """Project permalink prefix for expected permalinks."""
    return test_entities[0].permalink.split("/", 1)[0]


@pytest.mark.asyncio
async def test_exact_permalink_match(link_resolver, test_entities, project_prefix):
    """Test resolving a link that exactly matches a permalink."""
    entity = await link_resolver.resolve_link("components/core-service")
    assert entity.permalink == f"{project_prefix}/components/core-service"


@pytest.mark.asyncio
async def test_exact_title_match(link_resolver, test_entities, project_prefix):
    """Test resolving a link that matches an entity title."""
    entity = await link_resolver.resolve_link("Core Service")
    assert entity.permalink == f"{project_prefix}/components/core-service"


@pytest.mark.asyncio
async def test_duplicate_title_match(link_resolver, test_entities, project_prefix):
    """Test resolving a link that matches an entity title."""
    entity = await link_resolver.resolve_link("Core Service")
    assert entity.permalink == f"{project_prefix}/components/core-service"


@pytest.mark.asyncio
async def test_fuzzy_title_partial_match(link_resolver, project_prefix):
    # Test partial match
    result = await link_resolver.resolve_link("Auth Serv")
    assert result is not None, "Did not find partial match"
    assert result.permalink == f"{project_prefix}/components/auth-service"


@pytest.mark.asyncio
async def test_fuzzy_title_exact_match(link_resolver, project_prefix):
    # Test partial match
    result = await link_resolver.resolve_link("auth-service")
    assert result.permalink == f"{project_prefix}/components/auth-service"


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
    assert resolved.note_type == "file"
    assert resolved.title == "Image.png"


@pytest.mark.asyncio
async def test_folder_title_pattern_with_md_extension(link_resolver, test_entities, project_prefix):
    """Test resolving folder/title patterns that need .md extension added.

    This tests the new logic added in step 4 of resolve_link that handles
    patterns like 'folder/title' by trying 'folder/title.md' as file path.
    """
    # Test folder/title pattern for markdown entities
    # "components/Core Service" should resolve to file path "components/Core Service.md"
    entity = await link_resolver.resolve_link("components/Core Service")
    assert entity is not None
    assert entity.permalink == f"{project_prefix}/components/core-service"
    assert entity.file_path == "components/Core Service.md"

    # Test with different entity
    entity = await link_resolver.resolve_link("config/Service Config")
    assert entity is not None
    assert entity.permalink == f"{project_prefix}/config/service-config"
    assert entity.file_path == "config/Service Config.md"

    # Test with nested folder structure
    entity = await link_resolver.resolve_link("specs/subspec/Sub Features 1")
    assert entity is not None
    assert entity.permalink == f"{project_prefix}/specs/subspec/sub-features-1"
    assert entity.file_path == "specs/subspec/Sub Features 1.md"

    # Test that it doesn't try to add .md to things that already have it
    entity = await link_resolver.resolve_link("components/Core Service.md")
    assert entity is not None
    assert entity.permalink == f"{project_prefix}/components/core-service"

    # Test that it doesn't try to add .md to single words (no slash)
    entity = await link_resolver.resolve_link("NonExistent")
    assert entity is None

    # Test that it doesn't interfere with exact permalink matches
    entity = await link_resolver.resolve_link("components/core-service")
    assert entity is not None
    assert entity.permalink == f"{project_prefix}/components/core-service"


# Tests for strict mode parameter combinations
@pytest.mark.asyncio
async def test_strict_mode_parameter_combinations(link_resolver, test_entities, project_prefix):
    """Test all combinations of use_search and strict parameters."""

    # Test queries
    exact_match = "Auth Service"  # Should always work (unique title)
    fuzzy_match = "Auth Serv"  # Should only work with fuzzy search enabled
    non_existent = "Does Not Exist"  # Should never work

    # Case 1: use_search=True, strict=False (default behavior - fuzzy matching allowed)
    result = await link_resolver.resolve_link(exact_match, use_search=True, strict=False)
    assert result is not None
    assert result.permalink == f"{project_prefix}/components/auth-service"

    result = await link_resolver.resolve_link(fuzzy_match, use_search=True, strict=False)
    assert result is not None  # Should find "Auth Service" via fuzzy matching
    assert result.permalink == f"{project_prefix}/components/auth-service"

    result = await link_resolver.resolve_link(non_existent, use_search=True, strict=False)
    assert result is None

    # Case 2: use_search=True, strict=True (exact matches only, even with search enabled)
    result = await link_resolver.resolve_link(exact_match, use_search=True, strict=True)
    assert result is not None
    assert result.permalink == f"{project_prefix}/components/auth-service"

    result = await link_resolver.resolve_link(fuzzy_match, use_search=True, strict=True)
    assert result is None  # Should NOT find via fuzzy matching in strict mode

    result = await link_resolver.resolve_link(non_existent, use_search=True, strict=True)
    assert result is None

    # Case 3: use_search=False, strict=False (no search, exact repository matches only)
    result = await link_resolver.resolve_link(exact_match, use_search=False, strict=False)
    assert result is not None
    assert result.permalink == f"{project_prefix}/components/auth-service"

    result = await link_resolver.resolve_link(fuzzy_match, use_search=False, strict=False)
    assert result is None  # No search means no fuzzy matching

    result = await link_resolver.resolve_link(non_existent, use_search=False, strict=False)
    assert result is None

    # Case 4: use_search=False, strict=True (redundant but should work same as case 3)
    result = await link_resolver.resolve_link(exact_match, use_search=False, strict=True)
    assert result is not None
    assert result.permalink == f"{project_prefix}/components/auth-service"

    result = await link_resolver.resolve_link(fuzzy_match, use_search=False, strict=True)
    assert result is None  # No search means no fuzzy matching

    result = await link_resolver.resolve_link(non_existent, use_search=False, strict=True)
    assert result is None


@pytest.mark.asyncio
async def test_exact_match_types_in_strict_mode(link_resolver, test_entities, project_prefix):
    """Test that all types of exact matches work in strict mode."""

    # 1. Exact permalink match
    result = await link_resolver.resolve_link("components/core-service", strict=True)
    assert result is not None
    assert result.permalink == f"{project_prefix}/components/core-service"

    # 2. Exact title match
    result = await link_resolver.resolve_link("Core Service", strict=True)
    assert result is not None
    assert result.permalink == f"{project_prefix}/components/core-service"

    # 3. Exact file path match
    result = await link_resolver.resolve_link("components/Core Service.md", strict=True)
    assert result is not None
    assert result.permalink == f"{project_prefix}/components/core-service"

    # 4. Folder/title pattern with .md extension added
    result = await link_resolver.resolve_link("components/Core Service", strict=True)
    assert result is not None
    assert result.permalink == f"{project_prefix}/components/core-service"

    # 5. Non-markdown file (Image.png)
    result = await link_resolver.resolve_link("Image.png", strict=True)
    assert result is not None
    assert result.title == "Image.png"


@pytest.mark.asyncio
async def test_fuzzy_matching_blocked_in_strict_mode(link_resolver, test_entities):
    """Test that various fuzzy matching scenarios are blocked in strict mode."""

    # Partial matches that would work in normal mode
    fuzzy_queries = [
        "Auth Serv",  # Partial title
        "auth-service",  # Lowercase permalink variation
        "Core",  # Single word from title
        "Service",  # Common word
        "Serv",  # Partial word
    ]

    for query in fuzzy_queries:
        # Should NOT work in strict mode
        strict_result = await link_resolver.resolve_link(query, strict=True)
        assert strict_result is None, f"Query '{query}' should return None in strict mode"


@pytest.mark.asyncio
async def test_link_normalization_with_strict_mode(link_resolver, test_entities, project_prefix):
    """Test that link normalization still works in strict mode."""

    # Test bracket removal and alias handling in strict mode
    queries_and_expected = [
        ("[[Core Service]]", f"{project_prefix}/components/core-service"),
        ("[[Core Service|Main]]", f"{project_prefix}/components/core-service"),
        ("  [[  Core Service  ]]  ", f"{project_prefix}/components/core-service"),
    ]

    for query, expected_permalink in queries_and_expected:
        result = await link_resolver.resolve_link(query, strict=True)
        assert result is not None, f"Query '{query}' should find entity in strict mode"
        assert result.permalink == expected_permalink


@pytest.mark.asyncio
async def test_duplicate_title_handling_in_strict_mode(
    link_resolver, test_entities, project_prefix
):
    """Test how duplicate titles are handled in strict mode."""

    # "Core Service" appears twice in test data (components/core-service and components2/core-service)
    # In strict mode, if there are multiple exact title matches, it should still return the first one
    # (same behavior as normal mode for exact matches)

    result = await link_resolver.resolve_link("Core Service", strict=True)
    assert result is not None
    # Should return the first match (components/core-service based on test fixture order)
    assert result.permalink == f"{project_prefix}/components/core-service"


@pytest.mark.asyncio
async def test_cross_project_link_resolution(
    session_maker, entity_repository, search_service, tmp_path
):
    """Test resolving explicit cross-project links."""
    from basic_memory.repository.project_repository import ProjectRepository

    project_repo = ProjectRepository(session_maker)
    other_project = await project_repo.create(
        {
            "name": "other-project",
            "description": "Secondary project",
            "path": str(tmp_path / "other-project"),
            "is_active": True,
            "is_default": False,
        }
    )

    now = datetime.now(timezone.utc)
    other_entity_repo = EntityRepository(session_maker, project_id=other_project.id)
    target = await other_entity_repo.add(
        EntityModel(
            title="Cross Project Note",
            note_type="note",
            content_type="text/markdown",
            file_path="docs/Cross Project Note.md",
            permalink=f"{other_project.permalink}/docs/cross-project-note",
            created_at=now,
            updated_at=now,
            project_id=other_project.id,
        )
    )

    resolver = LinkResolver(entity_repository, search_service)
    resolved = await resolver.resolve_link("other-project::Cross Project Note", strict=True)

    assert resolved is not None
    assert resolved.id == target.id
    assert resolved.project_id == other_project.id


# ============================================================================
# Context-aware resolution tests (source_path parameter)
# ============================================================================


@pytest_asyncio.fixture
async def context_aware_entities(entity_repository):
    """Create entities for testing context-aware resolution.

    Structure:
    ├── testing.md                    (title: "testing", root level)
    ├── main/
    │   └── testing/
    │       ├── testing.md            (title: "testing", nested)
    │       └── another-test.md       (title: "another-test")
    ├── other/
    │   └── testing.md                (title: "testing", different branch)
    └── deep/
        └── nested/
            └── folder/
                └── note.md           (title: "note")
    """
    entities = []
    now = datetime.now(timezone.utc)
    project_id = entity_repository.project_id

    # Root level testing.md
    e1 = await entity_repository.add(
        EntityModel(
            title="testing",
            note_type="note",
            content_type="text/markdown",
            file_path="testing.md",
            permalink="testing",
            created_at=now,
            updated_at=now,
            project_id=project_id,
        )
    )
    entities.append(e1)

    # main/testing/testing.md
    e2 = await entity_repository.add(
        EntityModel(
            title="testing",
            note_type="note",
            content_type="text/markdown",
            file_path="main/testing/testing.md",
            permalink="main/testing/testing",
            created_at=now,
            updated_at=now,
            project_id=project_id,
        )
    )
    entities.append(e2)

    # main/testing/another-test.md
    e3 = await entity_repository.add(
        EntityModel(
            title="another-test",
            note_type="note",
            content_type="text/markdown",
            file_path="main/testing/another-test.md",
            permalink="main/testing/another-test",
            created_at=now,
            updated_at=now,
            project_id=project_id,
        )
    )
    entities.append(e3)

    # other/testing.md
    e4 = await entity_repository.add(
        EntityModel(
            title="testing",
            note_type="note",
            content_type="text/markdown",
            file_path="other/testing.md",
            permalink="other/testing",
            created_at=now,
            updated_at=now,
            project_id=project_id,
        )
    )
    entities.append(e4)

    # deep/nested/folder/note.md
    e5 = await entity_repository.add(
        EntityModel(
            title="note",
            note_type="note",
            content_type="text/markdown",
            file_path="deep/nested/folder/note.md",
            permalink="deep/nested/folder/note",
            created_at=now,
            updated_at=now,
            project_id=project_id,
        )
    )
    entities.append(e5)

    # deep/note.md (for ancestor testing)
    e6 = await entity_repository.add(
        EntityModel(
            title="note",
            note_type="note",
            content_type="text/markdown",
            file_path="deep/note.md",
            permalink="deep/note",
            created_at=now,
            updated_at=now,
            project_id=project_id,
        )
    )
    entities.append(e6)

    # note.md at root (for ancestor testing)
    e7 = await entity_repository.add(
        EntityModel(
            title="note",
            note_type="note",
            content_type="text/markdown",
            file_path="note.md",
            permalink="note",
            created_at=now,
            updated_at=now,
            project_id=project_id,
        )
    )
    entities.append(e7)

    return entities


@pytest_asyncio.fixture
async def context_link_resolver(entity_repository, search_service, context_aware_entities):
    """Create LinkResolver instance with context-aware test data.

    Note: We don't index entities for search because these tests focus on
    exact title/permalink matching, not fuzzy search. The entities are
    database-only records (no files on disk).
    """
    return LinkResolver(entity_repository, search_service)


@pytest.mark.asyncio
async def test_source_path_same_folder_preference(context_link_resolver):
    """Test that links prefer notes in the same folder as the source."""
    # From main/testing/another-test.md, [[testing]] should find main/testing/testing.md
    result = await context_link_resolver.resolve_link(
        "testing", source_path="main/testing/another-test.md"
    )
    assert result is not None
    assert result.file_path == "main/testing/testing.md"


@pytest.mark.asyncio
async def test_source_path_from_root_prefers_root(context_link_resolver):
    """Test that links from root-level notes prefer root-level matches."""
    # From root-note.md, [[testing]] should find testing.md (root level)
    result = await context_link_resolver.resolve_link("testing", source_path="some-root-note.md")
    assert result is not None
    assert result.file_path == "testing.md"


@pytest.mark.asyncio
async def test_source_path_different_branch_prefers_closest(context_link_resolver):
    """Test resolution when source is in a different branch of the folder tree."""
    # From other/testing.md, [[testing]] should find other/testing.md (same folder)
    # Wait, other/testing.md IS the testing note in that folder, so this tests self-reference
    # Let's test from a hypothetical other/different.md
    result = await context_link_resolver.resolve_link("testing", source_path="other/different.md")
    assert result is not None
    # Should find other/testing.md since it's in the same folder
    assert result.file_path == "other/testing.md"


@pytest.mark.asyncio
async def test_source_path_ancestor_preference(context_link_resolver):
    """Test that closer ancestors are preferred over distant ones."""
    # From deep/nested/folder/note.md, [[note]] with multiple "note" titles
    # should prefer the closest ancestor match

    # First verify there are multiple "note" entities
    # deep/nested/folder/note.md, deep/note.md, note.md

    # From deep/nested/folder/some-file.md, [[note]] should prefer:
    # 1. deep/nested/folder/note.md (same folder) - but that's the note itself
    # Let's say we're linking from a different file in that folder
    result = await context_link_resolver.resolve_link(
        "note", source_path="deep/nested/folder/other-file.md"
    )
    assert result is not None
    # Should find deep/nested/folder/note.md (same folder)
    assert result.file_path == "deep/nested/folder/note.md"


@pytest.mark.asyncio
async def test_source_path_parent_folder_preference(context_link_resolver):
    """Test that parent folder is preferred when no same-folder match exists."""
    # From deep/nested/folder/x.md where there's no "common" in same folder,
    # but there's one in deep/nested/ - should prefer closer ancestor

    # For this test, let's check that from deep/nested/other/file.md,
    # [[note]] finds deep/note.md (ancestor) rather than note.md (root)
    result = await context_link_resolver.resolve_link(
        "note", source_path="deep/nested/other/file.md"
    )
    assert result is not None
    # No note.md in deep/nested/other/, so should find deep/note.md (closest ancestor)
    # Actually deep/nested/folder/note.md might be considered... let me think
    # deep/nested/other/file.md -> ancestors are deep/nested/, deep/, root
    # Siblings/cousins like deep/nested/folder/ are NOT ancestors
    # So should find deep/note.md
    assert result.file_path == "deep/note.md"


@pytest.mark.asyncio
async def test_source_path_no_context_falls_back_to_shortest_path(context_link_resolver):
    """Test that without source_path, resolution falls back to shortest path."""
    # Without source_path, should use standard resolution (permalink first, then title)
    result = await context_link_resolver.resolve_link("testing")
    assert result is not None
    # Should get the one with shortest path or matching permalink
    # "testing" matches permalink "testing" of root testing.md
    assert result.file_path == "testing.md"


@pytest.mark.asyncio
async def test_source_path_unique_title_ignores_context(context_link_resolver):
    """Test that unique titles resolve correctly regardless of source_path."""
    # "another-test" only exists in one place
    result = await context_link_resolver.resolve_link(
        "another-test",
        source_path="other/some-file.md",  # Different folder
    )
    assert result is not None
    assert result.file_path == "main/testing/another-test.md"


@pytest.mark.asyncio
async def test_source_path_with_permalink_conflict(context_link_resolver):
    """Test that same-folder title match beats permalink match from different folder."""
    # Root testing.md has permalink "testing"
    # main/testing/testing.md has title "testing"
    # From main/testing/another-test.md, [[testing]] should prefer the same-folder match
    # even though there's a permalink match at root

    result = await context_link_resolver.resolve_link(
        "testing", source_path="main/testing/another-test.md"
    )
    assert result is not None
    # Should prefer same-folder title match over root permalink match
    assert result.file_path == "main/testing/testing.md"


@pytest.mark.asyncio
async def test_find_closest_entity_same_folder(context_link_resolver, context_aware_entities):
    """Test _find_closest_entity helper with same folder match."""
    # Get entities with title "testing"
    testing_entities = [e for e in context_aware_entities if e.title == "testing"]
    assert len(testing_entities) == 3  # root, main/testing, other

    closest = context_link_resolver._find_closest_entity(
        testing_entities, "main/testing/another-test.md"
    )
    assert closest.file_path == "main/testing/testing.md"


@pytest.mark.asyncio
async def test_find_closest_entity_ancestor_preference(
    context_link_resolver, context_aware_entities
):
    """Test _find_closest_entity prefers closer ancestors."""
    # Get entities with title "note"
    note_entities = [e for e in context_aware_entities if e.title == "note"]
    assert len(note_entities) == 3  # deep/nested/folder, deep, root

    # From deep/nested/other/file.md, should prefer deep/note.md over note.md
    closest = context_link_resolver._find_closest_entity(note_entities, "deep/nested/other/file.md")
    assert closest.file_path == "deep/note.md"


@pytest.mark.asyncio
async def test_find_closest_entity_root_source(context_link_resolver, context_aware_entities):
    """Test _find_closest_entity when source is at root."""
    testing_entities = [e for e in context_aware_entities if e.title == "testing"]

    # From root level, should prefer root testing.md
    closest = context_link_resolver._find_closest_entity(testing_entities, "some-root-file.md")
    assert closest.file_path == "testing.md"


@pytest.mark.asyncio
async def test_nonexistent_link_with_source_path(context_link_resolver):
    """Test that non-existent links return None even with source_path."""
    result = await context_link_resolver.resolve_link(
        "does-not-exist", source_path="main/testing/another-test.md"
    )
    assert result is None


# ============================================================================
# Relative path resolution tests
# ============================================================================


@pytest_asyncio.fixture
async def relative_path_entities(entity_repository):
    """Create entities for testing relative path resolution.

    Structure:
    ├── testing/
    │   ├── link-test.md               (source file for testing)
    │   └── nested/
    │       └── deep-note.md           (target for relative path)
    ├── nested/
    │   └── deep-note.md               (different deep-note at root level)
    └── other/
        └── file.md
    """
    entities = []
    now = datetime.now(timezone.utc)
    project_id = entity_repository.project_id

    # testing/link-test.md (source file)
    e1 = await entity_repository.add(
        EntityModel(
            title="link-test",
            note_type="note",
            content_type="text/markdown",
            file_path="testing/link-test.md",
            permalink="testing/link-test",
            created_at=now,
            updated_at=now,
            project_id=project_id,
        )
    )
    entities.append(e1)

    # testing/nested/deep-note.md (relative target)
    e2 = await entity_repository.add(
        EntityModel(
            title="deep-note",
            note_type="note",
            content_type="text/markdown",
            file_path="testing/nested/deep-note.md",
            permalink="testing/nested/deep-note",
            created_at=now,
            updated_at=now,
            project_id=project_id,
        )
    )
    entities.append(e2)

    # nested/deep-note.md (absolute path target)
    e3 = await entity_repository.add(
        EntityModel(
            title="deep-note",
            note_type="note",
            content_type="text/markdown",
            file_path="nested/deep-note.md",
            permalink="nested/deep-note",
            created_at=now,
            updated_at=now,
            project_id=project_id,
        )
    )
    entities.append(e3)

    # other/file.md
    e4 = await entity_repository.add(
        EntityModel(
            title="file",
            note_type="note",
            content_type="text/markdown",
            file_path="other/file.md",
            permalink="other/file",
            created_at=now,
            updated_at=now,
            project_id=project_id,
        )
    )
    entities.append(e4)

    return entities


@pytest_asyncio.fixture
async def relative_path_resolver(entity_repository, search_service, relative_path_entities):
    """Create LinkResolver instance with relative path test data."""
    return LinkResolver(entity_repository, search_service)


@pytest.mark.asyncio
async def test_relative_path_resolution_from_subfolder(relative_path_resolver):
    """Test that [[nested/deep-note]] from testing/link-test.md resolves to testing/nested/deep-note.md."""
    # From testing/link-test.md, [[nested/deep-note]] should resolve to testing/nested/deep-note.md
    result = await relative_path_resolver.resolve_link(
        "nested/deep-note", source_path="testing/link-test.md"
    )
    assert result is not None
    assert result.file_path == "testing/nested/deep-note.md"


@pytest.mark.asyncio
async def test_relative_path_falls_back_to_absolute(relative_path_resolver):
    """Test that if relative path doesn't exist, falls back to absolute resolution."""
    # From other/file.md, [[nested/deep-note]] should resolve to nested/deep-note.md (absolute)
    # because other/nested/deep-note.md doesn't exist
    result = await relative_path_resolver.resolve_link(
        "nested/deep-note", source_path="other/file.md"
    )
    assert result is not None
    assert result.file_path == "nested/deep-note.md"


@pytest.mark.asyncio
async def test_relative_path_without_source_uses_absolute(relative_path_resolver):
    """Test that without source_path, paths are resolved as absolute."""
    # Without source_path, [[nested/deep-note]] should resolve to nested/deep-note.md
    result = await relative_path_resolver.resolve_link("nested/deep-note")
    assert result is not None
    assert result.file_path == "nested/deep-note.md"


@pytest.mark.asyncio
async def test_relative_path_from_root_falls_through(relative_path_resolver):
    """Test that paths from root-level files don't try relative resolution."""
    # From root-file.md (no folder), [[nested/deep-note]] should resolve to nested/deep-note.md
    result = await relative_path_resolver.resolve_link(
        "nested/deep-note", source_path="root-file.md"
    )
    assert result is not None
    assert result.file_path == "nested/deep-note.md"


@pytest.mark.asyncio
async def test_simple_link_no_slash_skips_relative_resolution(relative_path_resolver):
    """Test that links without '/' don't trigger relative path resolution."""
    # [[deep-note]] should use context-aware title matching, not relative paths
    result = await relative_path_resolver.resolve_link(
        "deep-note", source_path="testing/link-test.md"
    )
    assert result is not None
    # Should find testing/nested/deep-note.md via title match with same-folder preference
    # Actually both have title "deep-note", so it should prefer the one closer to source
    # testing/nested/ is not the same folder as testing/, but it's closer than nested/
    # The context-aware resolution will pick the closest match
    assert result.file_path == "testing/nested/deep-note.md"


# ============================================================================
# External ID (UUID) resolution tests
# ============================================================================


@pytest.mark.asyncio
async def test_resolve_link_by_external_id(link_resolver, test_entities):
    """Test resolving a link using a valid external_id (UUID)."""
    entity = test_entities[0]
    result = await link_resolver.resolve_link(entity.external_id)
    assert result is not None
    assert result.id == entity.id
    assert result.external_id == entity.external_id


@pytest.mark.asyncio
async def test_resolve_link_by_external_id_uppercase(link_resolver, test_entities):
    """Test that uppercase UUID is canonicalized and resolves correctly."""
    entity = test_entities[0]
    upper_id = entity.external_id.upper()
    result = await link_resolver.resolve_link(upper_id)
    assert result is not None
    assert result.id == entity.id


@pytest.mark.asyncio
async def test_resolve_link_by_external_id_nonexistent(link_resolver):
    """Test that a valid UUID format that doesn't match any entity returns None."""
    fake_id = str(uuid.uuid4())
    result = await link_resolver.resolve_link(fake_id)
    assert result is None


@pytest.mark.asyncio
async def test_resolve_link_non_uuid_falls_through(link_resolver, test_entities, project_prefix):
    """Test that non-UUID strings skip UUID resolution and use normal lookup."""
    result = await link_resolver.resolve_link("Core Service")
    assert result is not None
    assert result.permalink == f"{project_prefix}/components/core-service"
