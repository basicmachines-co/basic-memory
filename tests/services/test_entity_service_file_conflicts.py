"""File path conflict detection against a real database (SQLite and Postgres).

Two different paths in a project name the same note when they are equal after case,
Unicode, and separator normalization, or when they generate the same permalink. Each
entity row stores both forms (file_path_key, file_path_permalink), so the check is one
indexed lookup. These tests pin the rule, show every write path keeps the stored keys in
step with file_path, and show the check never reads the whole project.
"""

import random
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from basic_memory import db
from basic_memory.indexing.project_index_maintenance import (
    ProjectIndexMoveBatch,
    ProjectIndexMoveTarget,
    RepositoryProjectIndexMaintenanceStore,
)
from basic_memory.models import Entity, Project
from basic_memory.repository import EntityRepository
from basic_memory.services import EntityService
from basic_memory.utils import (
    build_canonical_permalink,
    file_path_conflict_keys,
    generate_permalink,
    normalize_file_path_for_comparison,
)


def reference_detect_conflicts(file_path: str, existing_paths: list[str]) -> list[str]:
    """The rule as a full scan: regenerate both forms for every existing path."""
    normalized_input = normalize_file_path_for_comparison(file_path)
    input_permalink = generate_permalink(file_path)
    conflicts = []
    for existing_path in existing_paths:
        if existing_path == file_path:
            continue
        if normalize_file_path_for_comparison(existing_path) == normalized_input:
            conflicts.append(existing_path)
            continue
        if generate_permalink(existing_path) == input_permalink:
            conflicts.append(existing_path)
    return sorted(conflicts)


async def _add_file(
    entity_repository: EntityRepository,
    session_maker,
    *,
    file_path: str,
    permalink: str | None = None,
    content_type: str | None = None,
) -> Entity:
    now = datetime.now(timezone.utc)
    if content_type is None:
        content_type = "text/markdown" if file_path.endswith(".md") else "image/png"
    async with db.scoped_session(session_maker) as session:
        return await entity_repository.create(
            session,
            {
                "project_id": entity_repository.project_id,
                "title": file_path,
                "note_type": "note" if content_type == "text/markdown" else "file",
                "permalink": permalink,
                "file_path": file_path,
                "content_type": content_type,
                "created_at": now,
                "updated_at": now,
            },
        )


async def _assert_keys_match_path(session_maker, entity_id: int, file_path: str) -> None:
    async with db.scoped_session(session_maker) as session:
        row = (
            await session.execute(
                select(Entity.file_path, Entity.file_path_key, Entity.file_path_permalink).where(
                    Entity.id == entity_id
                )
            )
        ).one()
    keys = file_path_conflict_keys(file_path)
    assert tuple(row) == (file_path, keys.path_key, keys.permalink)


# --- The conflict rule ---


@pytest.mark.parametrize(
    ("file_path", "existing_path"),
    [
        ("notes/foo.md", "Notes/FOO.md"),  # case only
        ("notes/café.md", "notes/café.md"),  # NFC vs NFD
        ("notes/foo.md", "notes\\foo.md"),  # path separator
    ],
)
@pytest.mark.parametrize("permalink", [None, "unrelated/custom-permalink"])
@pytest.mark.asyncio
async def test_equivalent_paths_conflict_whatever_permalink_is_stored(
    entity_service: EntityService,
    entity_repository: EntityRepository,
    session_maker,
    file_path: str,
    existing_path: str,
    permalink: str | None,
) -> None:
    await _add_file(entity_repository, session_maker, file_path=existing_path, permalink=permalink)

    assert await entity_service.detect_file_path_conflicts(file_path) == [existing_path]


@pytest.mark.asyncio
async def test_identical_path_is_not_a_conflict(
    entity_service: EntityService, entity_repository: EntityRepository, session_maker
) -> None:
    await _add_file(entity_repository, session_maker, file_path="notes/foo.md")
    await _add_file(entity_repository, session_maker, file_path="files/a.png")

    assert await entity_service.detect_file_path_conflicts("notes/foo.md") == []
    assert await entity_service.detect_file_path_conflicts("files/a.png") == []


@pytest.mark.asyncio
async def test_mixed_project_conflicts(
    entity_service: EntityService,
    entity_repository: EntityRepository,
    session_maker,
    test_project: Project,
) -> None:
    project_slug = test_project.permalink
    stored = [
        ("notes/Foo Bar.md", f"{project_slug}/notes/foo-bar"),  # canonical, project prefixed
        ("legacy/Old Note.md", "legacy/old-note"),  # stored before project prefixes
        ("custom/Renamed.md", f"{project_slug}/custom/chosen-slug"),  # frontmatter permalink
        ("moved/SKILL.md", f"{project_slug}/moved/song-sketching"),  # kept after a move
        ("files/My Image.png", None),  # non-markdown, no stored permalink
    ]
    for file_path, permalink in stored:
        await _add_file(entity_repository, session_maker, file_path=file_path, permalink=permalink)

    async def check(file_path: str) -> list[str]:
        return await entity_service.detect_file_path_conflicts(file_path)

    # Paths that generate the same permalink conflict, whatever permalink is stored.
    assert await check("notes/foo_bar.md") == ["notes/Foo Bar.md"]
    assert await check("legacy/old_note.md") == ["legacy/Old Note.md"]
    assert await check("files/my_image.png") == ["files/My Image.png"]
    # Case-only differences conflict through the path key.
    assert await check("NOTES/foo bar.md") == ["notes/Foo Bar.md"]
    assert await check("notes/unrelated.md") == []
    # A stored permalink that is not generated from its own path (frontmatter, or kept
    # after a move) does not block that name: the new note gets a suffixed permalink.
    assert await check("custom/chosen_slug.md") == []
    assert await check("moved/song-sketching.md") == []


@pytest.mark.asyncio
async def test_frontmatter_permalink_does_not_hide_a_path_that_generates_the_same_slug(
    entity_service: EntityService, entity_repository: EntityRepository, session_maker
) -> None:
    """The rule compares generated permalinks, never stored ones.

    #2010 skipped this pair (a file with a frontmatter permalink and a new path that
    generates its path's slug). The indexed check keeps the earlier rule and flags it.
    """
    await _add_file(
        entity_repository,
        session_maker,
        file_path="notes/Foo Bar.md",
        permalink="custom/chosen-slug",
    )

    assert await entity_service.detect_file_path_conflicts("notes/foo_bar.md") == [
        "notes/Foo Bar.md"
    ]


@pytest.mark.asyncio
async def test_path_inside_project_slug_is_not_a_conflict_with_its_unprefixed_twin(
    entity_service: EntityService,
    entity_repository: EntityRepository,
    session_maker,
    test_project: Project,
) -> None:
    """Both paths build the canonical permalink <project>/notes/foo.

    Their generated permalinks differ, so the second note is created with a suffixed
    permalink rather than rejected.
    """
    project_slug = test_project.permalink
    await _add_file(
        entity_repository,
        session_maker,
        file_path="notes/foo.md",
        permalink=build_canonical_permalink(project_slug, "notes/foo.md"),
    )

    assert await entity_service.detect_file_path_conflicts(f"{project_slug}/notes/foo.md") == []


@pytest.mark.asyncio
async def test_conflicts_are_detected_in_both_directions(
    entity_service: EntityService, entity_repository: EntityRepository, session_maker
) -> None:
    await _add_file(entity_repository, session_maker, file_path="notes/Foo Bar.md")
    await _add_file(entity_repository, session_maker, file_path="files/My Image.png")
    await _add_file(entity_repository, session_maker, file_path="notes/foo_bar.md")

    assert await entity_service.detect_file_path_conflicts("notes/foo_bar.md") == [
        "notes/Foo Bar.md"
    ]
    assert await entity_service.detect_file_path_conflicts("notes/Foo Bar.md") == [
        "notes/foo_bar.md"
    ]
    # Non-markdown files take part too: the generated permalink drops the extension, so a
    # note named like an existing image conflicts with it, as it did under the full scan.
    assert await entity_service.detect_file_path_conflicts("files/my_image.png") == [
        "files/My Image.png"
    ]
    assert await entity_service.detect_file_path_conflicts("files/My Image.md") == [
        "files/My Image.png"
    ]
    assert reference_detect_conflicts("files/My Image.md", ["files/My Image.png"]) == [
        "files/My Image.png"
    ]


@pytest.mark.asyncio
async def test_matches_the_full_scan_rule_over_random_paths(
    entity_service: EntityService, entity_repository: EntityRepository, session_maker
) -> None:
    segments = ["notes", "Notes", "a b", "a_b", "a-b", "Café", "café", "中文", "v2.0"]
    segments += ["x", "test-project"]
    names = ["Foo Bar", "foo_bar", "foo-bar", "FOO", "测试文档", "Résumé", "API (v2)", "1.0.0"]
    extensions = [".md", ".MD", ".png", ""]
    randomizer = random.Random(20261101)
    paths = sorted(
        {
            "/".join(randomizer.sample(segments, randomizer.randint(0, 2)))
            + "/"
            + randomizer.choice(names)
            + randomizer.choice(extensions)
            for _ in range(300)
        }
    )
    for path in paths:
        await _add_file(entity_repository, session_maker, file_path=path, content_type="text/plain")

    for file_path in paths:
        assert await entity_service.detect_file_path_conflicts(
            file_path
        ) == reference_detect_conflicts(file_path, paths), file_path


# --- The check is one indexed lookup ---


@pytest.mark.asyncio
async def test_conflict_check_is_one_indexed_query(
    entity_service: EntityService,
    entity_repository: EntityRepository,
    session_maker,
    engine_factory,
    db_backend: str,
) -> None:
    for index in range(200):
        await _add_file(entity_repository, session_maker, file_path=f"notes/Note {index}.md")
    await _add_file(entity_repository, session_maker, file_path="notes/Target Note.md")

    engine: AsyncEngine = engine_factory[0]
    statements: list[tuple[str, Sequence[Any]]] = []

    def record(conn, cursor, statement, parameters, context, executemany) -> None:
        statements.append((statement, parameters))

    event.listen(engine.sync_engine, "before_cursor_execute", record)
    try:
        conflicts = await entity_service.detect_file_path_conflicts("notes/target_note.md")
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", record)

    assert conflicts == ["notes/Target Note.md"]
    # One statement, returning only the matching rows; nothing loads every path.
    entity_statements = [entry for entry in statements if "entity" in entry[0]]
    assert len(entity_statements) == 1, statements
    statement, parameters = entity_statements[0]

    # The plan reads each key through its (project_id, key) index, never the whole table.
    async with engine.connect() as connection:
        if db_backend == "postgres":
            # Without statistics on the just-filled table, the planner chose among the
            # project indexes arbitrarily (ix_entity_project_id, or the key index with
            # only a project_id condition) and the assertions below flaked. ANALYZE gives
            # it the row counts that make each per-key index the selective choice.
            await connection.exec_driver_sql("ANALYZE entity")
            # Tiny test tables make a sequential scan the cheapest plan; turning it off
            # shows whether the indexes can serve the query at all.
            await connection.exec_driver_sql("SET enable_seqscan = off")
            plan_rows = await connection.exec_driver_sql(f"EXPLAIN {statement}", parameters)
        else:
            plan_rows = await connection.exec_driver_sql(
                f"EXPLAIN QUERY PLAN {statement}", parameters
            )
        plan = "\n".join(str(row[-1]) for row in plan_rows.all())
        if db_backend == "postgres":
            await connection.exec_driver_sql("RESET enable_seqscan")

    assert "ix_entity_project_file_path_key" in plan, plan
    assert "ix_entity_project_file_path_permalink" in plan, plan
    if db_backend == "postgres":
        assert "Seq Scan" not in plan, plan
    else:
        assert "file_path_key=?" in plan, plan
        assert "file_path_permalink=?" in plan, plan
        assert "SCAN entity" not in plan, plan


# --- Every write path keeps the stored keys in step with file_path ---


@pytest.mark.asyncio
async def test_orm_writes_keep_conflict_keys_in_step(
    entity_repository: EntityRepository, session_maker
) -> None:
    entity = await _add_file(entity_repository, session_maker, file_path="notes/First Name.md")
    await _assert_keys_match_path(session_maker, entity.id, "notes/First Name.md")

    # Repository.update sets attributes on the loaded row.
    async with db.scoped_session(session_maker) as session:
        await entity_repository.update(session, entity.id, {"file_path": "notes/Second Name.md"})
    await _assert_keys_match_path(session_maker, entity.id, "notes/Second Name.md")

    # Plain attribute assignment on a loaded entity.
    async with db.scoped_session(session_maker) as session:
        loaded = await entity_repository.get_by_id(session, entity.id, load_relations=False)
        assert loaded is not None
        loaded.file_path = "notes/Third Name.md"
    await _assert_keys_match_path(session_maker, entity.id, "notes/Third Name.md")


@pytest.mark.asyncio
async def test_update_fields_keeps_conflict_keys_in_step(
    entity_repository: EntityRepository, session_maker
) -> None:
    entity = await _add_file(entity_repository, session_maker, file_path="files/Old Name.png")

    async with db.scoped_session(session_maker) as session:
        assert await entity_repository.update_fields(
            session, entity.id, {"file_path": "files/New Name.png", "size": 10}
        )
    await _assert_keys_match_path(session_maker, entity.id, "files/New Name.png")


@pytest.mark.asyncio
async def test_bulk_index_move_keeps_conflict_keys_in_step(
    entity_service: EntityService,
    entity_repository: EntityRepository,
    session_maker,
    test_project: Project,
) -> None:
    moved = await _add_file(entity_repository, session_maker, file_path="notes/Alpha Note.md")
    stays = await _add_file(entity_repository, session_maker, file_path="notes/Other Note.md")

    store = RepositoryProjectIndexMaintenanceStore(
        session_maker=session_maker, project_id=test_project.id
    )
    result = await store.apply_project_index_move_batch(
        ProjectIndexMoveBatch(
            completed_batches=1,
            targets=(ProjectIndexMoveTarget("notes/Alpha Note.md", "archive/Beta Note.md"),),
        )
    )

    assert result.moved_entity_ids == frozenset({moved.id})
    await _assert_keys_match_path(session_maker, moved.id, "archive/Beta Note.md")
    await _assert_keys_match_path(session_maker, stays.id, "notes/Other Note.md")
    # The renamed file no longer conflicts at its old path, and does at its new one.
    assert await entity_service.detect_file_path_conflicts("notes/alpha_note.md") == []
    assert await entity_service.detect_file_path_conflicts("archive/beta_note.md") == [
        "archive/Beta Note.md"
    ]


@pytest.mark.asyncio
async def test_insert_without_conflict_keys_is_rejected(session_maker, test_project) -> None:
    """The columns are NOT NULL, so a writer that forgets them fails instead of hiding a row."""
    async with db.scoped_session(session_maker) as session:
        with pytest.raises(Exception, match="(?i)not null|null value"):
            await session.execute(
                text(
                    "INSERT INTO entity (external_id, title, note_type, content_type, "
                    "project_id, file_path, created_at, updated_at) VALUES ('x', 'X', 'note', "
                    "'text/markdown', :project_id, 'x.md', :now, :now)"
                ),
                {"project_id": test_project.id, "now": datetime.now(timezone.utc)},
            )
