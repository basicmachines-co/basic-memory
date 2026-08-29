"""Prune index entries for files the ignore patterns now exclude (#1254)."""

from pathlib import Path

import pytest
from sqlalchemy import select, text

from basic_memory import db
from basic_memory.index.local_project import (
    LocalProjectIndexRuntimeFactory,
    run_local_project_index_for_project,
)
from basic_memory.index.local_prune import (
    list_ignored_indexed_paths,
    plan_prune,
    prune_ignored_entities,
)
from basic_memory.models.knowledge import Entity, Relation


def test_plan_prune_matches_scan_ignore_semantics(tmp_path: Path):
    """The plan uses should_ignore_path, so directory and glob patterns behave as in a scan."""
    indexed = ["notes/keep.md", "scratch/tmp.md", "notes/draft.tmp.md", "scratch/deep/x.md"]

    assert plan_prune(indexed, tmp_path, {"scratch/"}) == ("scratch/deep/x.md", "scratch/tmp.md")
    assert plan_prune(indexed, tmp_path, {"*.tmp.md"}) == ("notes/draft.tmp.md",)
    assert plan_prune(indexed, tmp_path, set()) == ()


async def _index_project(test_project, project_config, files: dict[str, str]) -> None:
    for relative, content in files.items():
        path = project_config.home / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    await run_local_project_index_for_project(
        test_project,
        runtime_factory=LocalProjectIndexRuntimeFactory(batch_size=10),
        force_full=True,
    )


@pytest.mark.asyncio
async def test_prune_removes_ignored_entries_and_repairs_linking_notes(
    test_project, project_config, session_maker
):
    """An entry indexed before its pattern existed is removed; its file and linking note stay."""
    await _index_project(
        test_project,
        project_config,
        {
            "keep.md": "# Keep\n\n- links_to [[Scratch Note]]\n",
            "scratch-note.md": "# Scratch Note\n",
        },
    )
    dependencies = await LocalProjectIndexRuntimeFactory().dependencies_for_project(test_project)
    async with db.scoped_session(session_maker) as session:
        keep = await dependencies.entity_repository.get_by_file_path(session, "keep.md")
        tmp = await dependencies.entity_repository.get_by_file_path(session, "scratch-note.md")
        assert keep is not None and tmp is not None
        assert keep.outgoing_relations[0].to_id == tmp.id
        keep_id, tmp_id = keep.id, tmp.id

    planned = await list_ignored_indexed_paths(dependencies, ignore_patterns={"scratch-*"})
    assert planned == ("scratch-note.md",)

    result = await prune_ignored_entities(dependencies, planned)

    assert result.deleted_entities == 1
    assert result.refreshed_entity_ids == frozenset({keep_id})
    assert (project_config.home / "scratch-note.md").exists(), "files are never touched"
    async with db.scoped_session(session_maker) as session:
        assert await session.get(Entity, tmp_id) is None
        relations = (
            (await session.execute(select(Relation).where(Relation.from_id == keep_id)))
            .scalars()
            .all()
        )
        assert [(r.to_id, r.to_name) for r in relations] == [(None, "Scratch Note")]
        search_rows = (
            await session.execute(
                text("SELECT type, title FROM search_index WHERE entity_id = :id"),
                {"id": tmp_id},
            )
        ).all()
        assert search_rows == [], "the pruned entity leaves no search rows behind"
        keep_relation_rows = (
            await session.execute(
                text("SELECT title FROM search_index WHERE entity_id = :id AND type = 'relation'"),
                {"id": keep_id},
            )
        ).all()
        assert keep_relation_rows == [("Keep",)], (
            "the linking note's search row no longer names a target"
        )


@pytest.mark.asyncio
async def test_prune_with_nothing_planned_is_a_no_op(test_project, project_config, session_maker):
    await _index_project(test_project, project_config, {"keep.md": "# Keep\n"})
    dependencies = await LocalProjectIndexRuntimeFactory().dependencies_for_project(test_project)

    result = await prune_ignored_entities(dependencies, ())

    assert result.deleted_entities == 0
    async with db.scoped_session(session_maker) as session:
        assert await dependencies.entity_repository.get_by_file_path(session, "keep.md")
