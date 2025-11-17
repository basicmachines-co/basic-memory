"""Tests for the search repository edit bug fix.

This test reproduces the critical bug where editing notes causes them to disappear
from the search index due to missing project_id filter in index_item() method.
"""

from datetime import datetime, timezone

import pytest
import pytest_asyncio

from basic_memory.models.project import Project
from basic_memory.repository.search_index_row import SearchIndexRow
from basic_memory.repository.sqlite_search_repository import SQLiteSearchRepository
from basic_memory.schemas.search import SearchItemType


@pytest_asyncio.fixture
async def second_test_project(project_repository):
    """Create a second project for testing project isolation during edits."""
    project_data = {
        "name": "Second Edit Test Project",
        "description": "Another project for testing edit bug",
        "path": "/second/edit/test/path",
        "is_active": True,
        "is_default": None,
    }
    return await project_repository.create(project_data)


@pytest_asyncio.fixture
async def second_search_repo(session_maker, second_test_project):
    """Create a search repository for the second project."""
    return SQLiteSearchRepository(session_maker, project_id=second_test_project.id)


@pytest.mark.asyncio
async def test_index_item_respects_project_isolation_during_edit():
    """Test that index_item() doesn't delete records from other projects during edits.

    This test reproduces the critical bug where editing a note in one project
    would delete search index entries with the same permalink from ALL projects,
    causing notes to disappear from the search index.
    """
    from basic_memory import db
    from basic_memory.models.base import Base
    from basic_memory.repository.sqlite_search_repository import SQLiteSearchRepository
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

    # Create a separate in-memory database for this test
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    # Create the database schema
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Create two projects
    async with db.scoped_session(session_maker) as session:
        project1 = Project(
            name="Project 1",
            description="First project",
            path="/project1/path",
            is_active=True,
            is_default=True,
        )
        project2 = Project(
            name="Project 2",
            description="Second project",
            path="/project2/path",
            is_active=True,
            is_default=False,
        )
        session.add(project1)
        session.add(project2)
        await session.flush()

        project1_id = project1.id
        project2_id = project2.id
        await session.commit()

    # Create search repositories for both projects
    repo1 = SQLiteSearchRepository(session_maker, project_id=project1_id)
    repo2 = SQLiteSearchRepository(session_maker, project_id=project2_id)

    # Initialize search index
    await repo1.init_search_index()

    # Create two notes with the SAME permalink in different projects
    # This simulates the same note name/structure across different projects
    same_permalink = "notes/test-note"

    search_row1 = SearchIndexRow(
        id=1,
        type=SearchItemType.ENTITY.value,
        title="Test Note in Project 1",
        content_stems="project 1 content original",
        content_snippet="This is the original content in project 1",
        permalink=same_permalink,
        file_path="notes/test_note.md",
        entity_id=1,
        metadata={"entity_type": "note"},
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        project_id=project1_id,
    )

    search_row2 = SearchIndexRow(
        id=2,
        type=SearchItemType.ENTITY.value,
        title="Test Note in Project 2",
        content_stems="project 2 content original",
        content_snippet="This is the original content in project 2",
        permalink=same_permalink,  # SAME permalink as project 1
        file_path="notes/test_note.md",
        entity_id=2,
        metadata={"entity_type": "note"},
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        project_id=project2_id,
    )

    # Index both items in their respective projects
    await repo1.index_item(search_row1)
    await repo2.index_item(search_row2)

    # Verify both projects can find their respective notes
    results1_before = await repo1.search(search_text="project 1 content")
    assert len(results1_before) == 1
    assert results1_before[0].title == "Test Note in Project 1"
    assert results1_before[0].project_id == project1_id

    results2_before = await repo2.search(search_text="project 2 content")
    assert len(results2_before) == 1
    assert results2_before[0].title == "Test Note in Project 2"
    assert results2_before[0].project_id == project2_id

    # Now simulate editing the note in project 1 (which re-indexes it)
    # This would trigger the bug where the DELETE query doesn't filter by project_id
    edited_search_row1 = SearchIndexRow(
        id=1,
        type=SearchItemType.ENTITY.value,
        title="Test Note in Project 1",
        content_stems="project 1 content EDITED",  # Changed content
        content_snippet="This is the EDITED content in project 1",
        permalink=same_permalink,
        file_path="notes/test_note.md",
        entity_id=1,
        metadata={"entity_type": "note"},
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        project_id=project1_id,
    )

    # Re-index the edited note in project 1
    # BEFORE THE FIX: This would delete the note from project 2 as well!
    await repo1.index_item(edited_search_row1)

    # Verify project 1 has the edited version
    results1_after = await repo1.search(search_text="project 1 content EDITED")
    assert len(results1_after) == 1
    assert results1_after[0].title == "Test Note in Project 1"
    assert "EDITED" in results1_after[0].content_snippet

    # CRITICAL TEST: Verify project 2's note is still there (the bug would delete it)
    results2_after = await repo2.search(search_text="project 2 content")
    assert len(results2_after) == 1, "Project 2's note disappeared after editing project 1's note!"
    assert results2_after[0].title == "Test Note in Project 2"
    assert results2_after[0].project_id == project2_id
    assert "original" in results2_after[0].content_snippet  # Should still be original

    # Double-check: project 1 should not be able to see project 2's note
    cross_search = await repo1.search(search_text="project 2 content")
    assert len(cross_search) == 0

    await engine.dispose()


@pytest.mark.asyncio
async def test_index_item_updates_existing_record_same_project():
    """Test that index_item() correctly updates existing records within the same project."""
    from basic_memory import db
    from basic_memory.models.base import Base
    from basic_memory.repository.sqlite_search_repository import SQLiteSearchRepository
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

    # Create a separate in-memory database for this test
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    # Create the database schema
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Create one project
    async with db.scoped_session(session_maker) as session:
        project = Project(
            name="Test Project",
            description="Test project",
            path="/test/path",
            is_active=True,
            is_default=True,
        )
        session.add(project)
        await session.flush()
        project_id = project.id
        await session.commit()

    # Create search repository
    repo = SQLiteSearchRepository(session_maker, project_id=project_id)
    await repo.init_search_index()

    permalink = "test/my-note"

    # Create initial note
    initial_row = SearchIndexRow(
        id=1,
        type=SearchItemType.ENTITY.value,
        title="My Test Note",
        content_stems="initial content here",
        content_snippet="This is the initial content",
        permalink=permalink,
        file_path="test/my_note.md",
        entity_id=1,
        metadata={"entity_type": "note"},
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        project_id=project_id,
    )

    # Index the initial version
    await repo.index_item(initial_row)

    # Verify it exists
    results_initial = await repo.search(search_text="initial content")
    assert len(results_initial) == 1
    assert results_initial[0].content_snippet == "This is the initial content"

    # Now update the note (simulate an edit)
    updated_row = SearchIndexRow(
        id=1,
        type=SearchItemType.ENTITY.value,
        title="My Test Note",
        content_stems="updated content here",  # Changed
        content_snippet="This is the UPDATED content",  # Changed
        permalink=permalink,  # Same permalink
        file_path="test/my_note.md",
        entity_id=1,
        metadata={"entity_type": "note"},
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        project_id=project_id,
    )

    # Re-index (should replace the old version)
    await repo.index_item(updated_row)

    # Verify the old version is gone
    results_old = await repo.search(search_text="initial content")
    assert len(results_old) == 0

    # Verify the new version exists
    results_new = await repo.search(search_text="updated content")
    assert len(results_new) == 1
    assert results_new[0].content_snippet == "This is the UPDATED content"

    # Verify we only have one record (not duplicated)
    all_results = await repo.search(search_text="My Test Note")
    assert len(all_results) == 1

    await engine.dispose()
