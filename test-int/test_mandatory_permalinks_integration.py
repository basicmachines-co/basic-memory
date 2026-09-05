"""Integration coverage for mandatory Markdown note permalinks."""

import pytest

from basic_memory import db
from basic_memory.file_utils import compute_checksum
from basic_memory.index.local_project import (
    LocalProjectIndexRuntimeFactory,
    run_local_project_index_for_project,
)
from basic_memory.models import Project
from basic_memory.repository.entity_repository import EntityRepository


@pytest.mark.asyncio
async def test_reindex_reserves_existing_malformed_identity_before_new_colliding_note(
    test_project: Project,
    project_config,
    engine_factory,
) -> None:
    malformed_path = project_config.home / "same-note.md"
    original_bytes = b"---\ntitle: [unclosed\n---\n\n# Existing note\n"
    malformed_path.write_bytes(original_bytes)
    await run_local_project_index_for_project(
        test_project,
        runtime_factory=LocalProjectIndexRuntimeFactory(batch_size=10),
        force_full=True,
    )
    _, session_maker = engine_factory
    repository = EntityRepository(project_id=test_project.id)
    async with db.scoped_session(session_maker) as session:
        original = await repository.get_by_file_path(session, "same-note.md")
    assert original is not None
    original_permalink = original.permalink
    assert original_permalink is not None

    # The new path sorts first but must not take the established semantic address.
    new_path = project_config.home / "same note.md"
    new_path.write_text("# New note\n", encoding="utf-8")
    original_bytes += b"\nAn edit to the existing note.\n"
    malformed_path.write_bytes(original_bytes)
    await run_local_project_index_for_project(
        test_project,
        runtime_factory=LocalProjectIndexRuntimeFactory(batch_size=10),
        force_full=True,
    )
    async with db.scoped_session(session_maker) as session:
        existing = await repository.get_by_file_path(session, "same-note.md")
        new = await repository.get_by_file_path(session, "same note.md")
    assert existing is not None
    assert new is not None
    assert existing.permalink == original_permalink
    assert new.permalink == f"{original_permalink}-1"
    assert malformed_path.read_bytes() == original_bytes
    assert f"permalink: {new.permalink}" in new_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_project_index_adds_permalink_when_optional_frontmatter_is_disabled(
    test_project: Project,
    project_config,
    engine_factory,
    app_config,
    config_manager,
) -> None:
    """The real index flow persists identity even when title/type injection is disabled."""
    app_config.ensure_frontmatter_on_sync = False
    config_manager.save_config(app_config)

    note_path = project_config.home / "legacy-note.md"
    note_path.write_text("# Legacy Note\n\nExisting body.\n", encoding="utf-8")

    result = await run_local_project_index_for_project(
        test_project,
        runtime_factory=LocalProjectIndexRuntimeFactory(batch_size=10),
        force_full=True,
    )

    expected_permalink = f"{test_project.permalink}/legacy-note"
    assert result.enqueued_files == 1
    indexed_content = note_path.read_text(encoding="utf-8")
    assert f"permalink: {expected_permalink}" in indexed_content
    assert "title:" not in indexed_content
    assert "type:" not in indexed_content

    _, session_maker = engine_factory
    entity_repository = EntityRepository(project_id=test_project.id)
    async with db.scoped_session(session_maker) as session:
        entity = await entity_repository.get_by_file_path(session, "legacy-note.md")

    assert entity is not None
    assert entity.permalink == expected_permalink


@pytest.mark.asyncio
async def test_project_index_reconciles_permalink_collisions_when_optional_frontmatter_is_disabled(
    test_project: Project,
    project_config,
    engine_factory,
    app_config,
    config_manager,
) -> None:
    """Conflict suffixes remain identical in canonical files and indexed entities."""
    app_config.ensure_frontmatter_on_sync = False
    config_manager.save_config(app_config)

    spaced_path = project_config.home / "same note.md"
    hyphen_path = project_config.home / "same-note.md"
    spaced_path.write_text("# Spaced Note\n", encoding="utf-8")
    hyphen_path.write_text("# Hyphen Note\n", encoding="utf-8")

    result = await run_local_project_index_for_project(
        test_project,
        runtime_factory=LocalProjectIndexRuntimeFactory(batch_size=10),
        force_full=True,
    )

    assert result.enqueued_files == 2
    _, session_maker = engine_factory
    entity_repository = EntityRepository(project_id=test_project.id)
    async with db.scoped_session(session_maker) as session:
        spaced_entity = await entity_repository.get_by_file_path(session, "same note.md")
        hyphen_entity = await entity_repository.get_by_file_path(session, "same-note.md")

    assert spaced_entity is not None
    assert hyphen_entity is not None
    assert spaced_entity.permalink != hyphen_entity.permalink
    assert {
        spaced_entity.permalink,
        hyphen_entity.permalink,
    } == {
        f"{test_project.permalink}/same-note",
        f"{test_project.permalink}/same-note-1",
    }
    assert (
        f"permalink: {spaced_entity.permalink}"
        in spaced_path.read_text(encoding="utf-8").splitlines()
    )
    assert (
        f"permalink: {hyphen_entity.permalink}"
        in hyphen_path.read_text(encoding="utf-8").splitlines()
    )


@pytest.mark.asyncio
async def test_normal_project_index_backfills_an_unchanged_legacy_note(
    test_project: Project,
    project_config,
    engine_factory,
    app_config,
    config_manager,
) -> None:
    """A regular startup scan repairs a checksum-current row with legacy null identity."""
    app_config.ensure_frontmatter_on_sync = False
    config_manager.save_config(app_config)
    note_path = project_config.home / "legacy-null.md"
    note_path.write_text("# Legacy Null\n\nExisting body.\n", encoding="utf-8")

    await run_local_project_index_for_project(
        test_project,
        runtime_factory=LocalProjectIndexRuntimeFactory(batch_size=10),
        force_full=True,
    )

    legacy_content = "# Legacy Null\n\nExisting body.\n"
    note_path.write_text(legacy_content, encoding="utf-8")
    _, session_maker = engine_factory
    entity_repository = EntityRepository(project_id=test_project.id)
    async with db.scoped_session(session_maker) as session:
        entity = await entity_repository.get_by_file_path(session, "legacy-null.md")
        assert entity is not None
        entity.permalink = None
        entity.checksum = await compute_checksum(legacy_content)

    result = await run_local_project_index_for_project(
        test_project,
        runtime_factory=LocalProjectIndexRuntimeFactory(batch_size=10),
    )

    assert result.enqueued_files == 1
    expected_permalink = f"{test_project.permalink}/legacy-null"
    assert f"permalink: {expected_permalink}" in note_path.read_text(encoding="utf-8").splitlines()
    async with db.scoped_session(session_maker) as session:
        repaired = await entity_repository.get_by_file_path(session, "legacy-null.md")
    assert repaired is not None
    assert repaired.permalink == expected_permalink


@pytest.mark.asyncio
async def test_forced_index_preserves_moved_malformed_note_permalink(
    test_project: Project,
    project_config,
    engine_factory,
    app_config,
    config_manager,
) -> None:
    """An unrewritable note keeps its semantic address when move updates are disabled."""
    app_config.update_permalinks_on_move = False
    config_manager.save_config(app_config)
    original_path = project_config.home / "malformed.md"
    moved_path = project_config.home / "archive" / "malformed.md"
    original_content = "---\ntitle: [unclosed\n---\n\n# Malformed\n"
    original_path.write_text(original_content, encoding="utf-8")

    await run_local_project_index_for_project(
        test_project,
        runtime_factory=LocalProjectIndexRuntimeFactory(batch_size=10),
        force_full=True,
    )
    _, session_maker = engine_factory
    entity_repository = EntityRepository(project_id=test_project.id)
    async with db.scoped_session(session_maker) as session:
        original_entity = await entity_repository.get_by_file_path(session, "malformed.md")
    assert original_entity is not None
    original_permalink = original_entity.permalink
    assert original_permalink is not None

    moved_path.parent.mkdir()
    original_path.rename(moved_path)
    move_result = await run_local_project_index_for_project(
        test_project,
        runtime_factory=LocalProjectIndexRuntimeFactory(batch_size=10),
    )
    assert move_result.moved_files == 1

    await run_local_project_index_for_project(
        test_project,
        runtime_factory=LocalProjectIndexRuntimeFactory(batch_size=10),
        force_full=True,
    )

    assert moved_path.read_text(encoding="utf-8") == original_content
    async with db.scoped_session(session_maker) as session:
        moved_entity = await entity_repository.get_by_file_path(session, "archive/malformed.md")
    assert moved_entity is not None
    assert moved_entity.permalink == original_permalink


@pytest.mark.asyncio
async def test_db_first_move_preserves_malformed_note_bytes_and_permalink(
    test_project: Project,
    project_config,
    engine_factory,
    client,
    app_config,
    config_manager,
) -> None:
    """The real accepted-write API move preserves an indexed malformed fence."""
    app_config.update_permalinks_on_move = False
    config_manager.save_config(app_config)
    malformed_content = "---\ntitle: [unclosed\n---\n\n# Malformed\n"
    source_path = project_config.home / "malformed-db-move.md"
    destination_path = project_config.home / "archive" / "malformed-db-move.md"
    source_path.write_text(malformed_content, encoding="utf-8")

    await run_local_project_index_for_project(
        test_project,
        runtime_factory=LocalProjectIndexRuntimeFactory(batch_size=10),
        force_full=True,
    )
    _, session_maker = engine_factory
    entity_repository = EntityRepository(project_id=test_project.id)
    async with db.scoped_session(session_maker) as session:
        created = await entity_repository.get_by_file_path(session, "malformed-db-move.md")
    assert created is not None
    assert created.permalink is not None

    response = await client.put(
        f"/v2/projects/{test_project.external_id}/knowledge/entities/{created.external_id}/move",
        json={"destination_path": "archive/malformed-db-move.md"},
    )

    assert response.status_code == 202
    assert response.json()["permalink"] == created.permalink
    assert not source_path.exists()
    assert destination_path.read_text(encoding="utf-8") == malformed_content
