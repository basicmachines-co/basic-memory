"""Local filesystem execution for deterministic Wiki projections."""

from datetime import UTC, datetime
from hashlib import sha256

import pytest

from basic_memory import db
from basic_memory.index.local_wiki_projection import (
    LocalWikiState,
    LocalWikiWriteConflict,
    apply_local_wiki_projection,
    inspect_local_wiki_projection,
)
from basic_memory.index.local_project import LocalProjectIndexScan
import basic_memory.index.local_wiki_projection as local_wiki_projection
from basic_memory.models import AcceptedProjectNoteChange, Project


@pytest.mark.asyncio
async def test_local_wiki_rebuild_is_idempotent(config_home, session_maker, test_project):
    guide = config_home / "guides" / "getting-started.md"
    guide.parent.mkdir()
    guide.write_text(
        "---\ntitle: Getting Started\npermalink: guides/getting-started\n---\n\n# Start\n",
        encoding="utf-8",
    )
    (config_home / "attachment.txt").write_text("not a note\n", encoding="utf-8")

    first = await inspect_local_wiki_projection(test_project, session_maker=session_maker)

    assert first.state is LocalWikiState.uninitialized
    assert [write.path for write in first.plan.writes] == [
        "guides/index.md",
        "guides/log.md",
        "index.md",
        "log.md",
    ]
    await apply_local_wiki_projection(first, session_maker=session_maker)
    initial_checksums = {
        path: sha256((config_home / path).read_bytes()).hexdigest()
        for path in ("guides/index.md", "guides/log.md", "index.md", "log.md")
    }

    second = await inspect_local_wiki_projection(test_project, session_maker=session_maker)

    assert second.state is LocalWikiState.current
    assert second.plan.writes == ()
    assert second.plan.result.unchanged == 4
    assert {
        path: sha256((config_home / path).read_bytes()).hexdigest() for path in initial_checksums
    } == initial_checksums
    assert "[[guides/index|Guides]]" in (config_home / "index.md").read_text()
    assert (
        "[[guides/getting-started|Getting Started]]"
        in (config_home / "guides" / "index.md").read_text()
    )


@pytest.mark.asyncio
async def test_local_wiki_includes_long_markdown_suffix(config_home, session_maker, test_project):
    note = config_home / "reference.markdown"
    note.write_text("---\ntitle: Reference\n---\n\n# Reference\n", encoding="utf-8")

    inspection = await inspect_local_wiki_projection(test_project, session_maker=session_maker)

    root_index = next(write for write in inspection.plan.writes if write.path == "index.md")
    assert "[[reference|Reference]]" in root_index.content.decode("utf-8")


@pytest.mark.asyncio
async def test_local_wiki_rejects_ignored_reserved_destination(
    config_home,
    session_maker,
    test_project,
):
    (config_home / ".gitignore").write_text("index.md\n", encoding="utf-8")
    (config_home / "note.md").write_text("# Note\n", encoding="utf-8")

    with pytest.raises(ValueError, match="reserved paths are ignored.*index.md"):
        await inspect_local_wiki_projection(test_project, session_maker=session_maker)

    assert not (config_home / "index.md").exists()


@pytest.mark.asyncio
async def test_local_wiki_rejects_incomplete_filesystem_scan(
    config_home,
    session_maker,
    test_project,
    monkeypatch,
):
    (config_home / "note.md").write_text("# Note\n", encoding="utf-8")
    monkeypatch.setattr(
        local_wiki_projection,
        "scan_local_project_index_files",
        lambda *_args, **_kwargs: LocalProjectIndexScan(
            file_paths=("note.md",),
            unreadable_directories=("private",),
        ),
    )

    with pytest.raises(OSError, match="scan is incomplete.*private"):
        await inspect_local_wiki_projection(test_project, session_maker=session_maker)

    assert not (config_home / "index.md").exists()


@pytest.mark.asyncio
async def test_local_wiki_reports_outdated_after_source_metadata_changes(
    config_home,
    session_maker,
    test_project,
):
    note = config_home / "note.md"
    note.write_text("---\ntitle: First\n---\n\n# Note\n", encoding="utf-8")
    initial = await inspect_local_wiki_projection(test_project, session_maker=session_maker)
    await apply_local_wiki_projection(initial, session_maker=session_maker)
    note.write_text("---\ntitle: Second\n---\n\n# Note\n", encoding="utf-8")

    inspection = await inspect_local_wiki_projection(test_project, session_maker=session_maker)

    assert inspection.state is LocalWikiState.outdated
    assert [write.path for write in inspection.plan.writes] == ["index.md"]


@pytest.mark.asyncio
async def test_local_wiki_waits_for_pending_accepted_change(
    config_home,
    session_maker,
    test_project,
):
    note = config_home / "note.md"
    note.write_text("# Note\n", encoding="utf-8")
    async with db.scoped_session(session_maker) as session:
        project = await session.get(Project, test_project.id)
        assert project is not None
        project.partition_position = 1
        session.add(
            AcceptedProjectNoteChange(
                project_id=test_project.id,
                project_external_id=test_project.external_id,
                partition_position=1,
                entity_id=1,
                note_external_id="note-1",
                permalink="note",
                title="Note",
                operation="created",
                file_path="note.md",
                accepted_at=datetime.now(UTC),
                source="write_note",
            )
        )

    inspection = await inspect_local_wiki_projection(test_project, session_maker=session_maker)

    assert inspection.state is LocalWikiState.partial
    assert inspection.plan.result.pending_materialization == (1,)
    with pytest.raises(LocalWikiWriteConflict, match="materialized changes: 1"):
        await apply_local_wiki_projection(inspection, session_maker=session_maker)


@pytest.mark.asyncio
async def test_local_wiki_refuses_user_owned_reserved_document(
    config_home,
    session_maker,
    test_project,
):
    existing = config_home / "index.md"
    existing.write_text("# My hand-written index\n", encoding="utf-8")

    inspection = await inspect_local_wiki_projection(test_project, session_maker=session_maker)

    assert inspection.state is LocalWikiState.conflicted
    assert inspection.plan.writes == ()
    assert [conflict.path for conflict in inspection.plan.result.conflicts] == ["index.md"]
    with pytest.raises(LocalWikiWriteConflict, match="reserved-document conflicts"):
        await apply_local_wiki_projection(inspection, session_maker=session_maker)
    assert existing.read_text(encoding="utf-8") == "# My hand-written index\n"


@pytest.mark.asyncio
async def test_local_wiki_checks_every_reserved_path_before_writing(
    config_home,
    session_maker,
    test_project,
):
    note = config_home / "note.md"
    note.write_text("# Note\n", encoding="utf-8")
    inspection = await inspect_local_wiki_projection(test_project, session_maker=session_maker)
    (config_home / "log.md").write_text("concurrent edit\n", encoding="utf-8")

    with pytest.raises(LocalWikiWriteConflict, match="projection inputs changed"):
        await apply_local_wiki_projection(inspection, session_maker=session_maker)

    # The preflight sees the later log.md conflict before creating index.md.
    assert not (config_home / "index.md").exists()
    assert (config_home / "log.md").read_text(encoding="utf-8") == "concurrent edit\n"


@pytest.mark.asyncio
@pytest.mark.parametrize("concurrent_change", ["changed", "missing"])
async def test_local_wiki_refuses_changed_existing_reserved_document(
    config_home,
    session_maker,
    test_project,
    concurrent_change,
):
    note = config_home / "note.md"
    note.write_text("---\ntitle: First\n---\n\n# Note\n", encoding="utf-8")
    initial = await inspect_local_wiki_projection(test_project, session_maker=session_maker)
    await apply_local_wiki_projection(initial, session_maker=session_maker)
    note.write_text("---\ntitle: Second\n---\n\n# Note\n", encoding="utf-8")
    update = await inspect_local_wiki_projection(test_project, session_maker=session_maker)
    index_path = config_home / "index.md"
    if concurrent_change == "changed":
        index_path.write_text("concurrent edit\n", encoding="utf-8")
    else:
        index_path.unlink()

    with pytest.raises(LocalWikiWriteConflict, match="projection inputs changed"):
        await apply_local_wiki_projection(update, session_maker=session_maker)


@pytest.mark.asyncio
async def test_local_wiki_preflights_unchanged_reserved_documents(
    config_home,
    session_maker,
    test_project,
):
    note = config_home / "note.md"
    note.write_text("---\ntitle: First\n---\n\n# Note\n", encoding="utf-8")
    initial = await inspect_local_wiki_projection(test_project, session_maker=session_maker)
    await apply_local_wiki_projection(initial, session_maker=session_maker)
    original_index = (config_home / "index.md").read_bytes()

    note.write_text("---\ntitle: Second\n---\n\n# Note\n", encoding="utf-8")
    update = await inspect_local_wiki_projection(test_project, session_maker=session_maker)
    assert [write.path for write in update.plan.writes] == ["index.md"]
    (config_home / "log.md").write_text("concurrent edit\n", encoding="utf-8")

    with pytest.raises(LocalWikiWriteConflict, match="projection inputs changed"):
        await apply_local_wiki_projection(update, session_maker=session_maker)

    assert (config_home / "index.md").read_bytes() == original_index


@pytest.mark.asyncio
async def test_local_wiki_refuses_source_note_changed_after_planning(
    config_home,
    session_maker,
    test_project,
):
    note = config_home / "note.md"
    note.write_text("# First\n", encoding="utf-8")
    inspection = await inspect_local_wiki_projection(test_project, session_maker=session_maker)
    note.write_text("# Second\n", encoding="utf-8")

    with pytest.raises(LocalWikiWriteConflict, match="projection inputs changed"):
        await apply_local_wiki_projection(inspection, session_maker=session_maker)

    assert not (config_home / "index.md").exists()


@pytest.mark.asyncio
async def test_local_wiki_refuses_ignore_rules_changed_after_planning(
    config_home,
    session_maker,
    test_project,
):
    (config_home / "note.md").write_text("# Note\n", encoding="utf-8")
    inspection = await inspect_local_wiki_projection(test_project, session_maker=session_maker)
    (config_home / ".gitignore").write_text("index.md\n", encoding="utf-8")

    with pytest.raises(LocalWikiWriteConflict, match="projection inputs changed"):
        await apply_local_wiki_projection(inspection, session_maker=session_maker)

    assert not (config_home / "index.md").exists()


@pytest.mark.asyncio
async def test_local_wiki_refuses_journal_advanced_after_planning(
    config_home,
    session_maker,
    test_project,
):
    note = config_home / "note.md"
    note.write_text("# Note\n", encoding="utf-8")
    inspection = await inspect_local_wiki_projection(test_project, session_maker=session_maker)
    async with db.scoped_session(session_maker) as session:
        project = await session.get(Project, test_project.id)
        assert project is not None
        project.partition_position = 1
        session.add(
            AcceptedProjectNoteChange(
                project_id=test_project.id,
                project_external_id=test_project.external_id,
                partition_position=1,
                entity_id=1,
                note_external_id="note-1",
                permalink="note",
                title="Note",
                operation="updated",
                file_path="note.md",
                accepted_at=datetime.now(UTC),
                source="write_note",
            )
        )

    with pytest.raises(LocalWikiWriteConflict, match="projection inputs changed"):
        await apply_local_wiki_projection(inspection, session_maker=session_maker)

    assert not (config_home / "index.md").exists()


@pytest.mark.asyncio
async def test_local_wiki_rejects_noncanonical_reserved_filename(
    config_home,
    session_maker,
    test_project,
):
    (config_home / "Index.md").write_text(
        """---
generated:
  by: Basic Memory Wiki Projector
bm:
  profile: wiki/1
---
# Index
""",
        encoding="utf-8",
    )

    inspection = await inspect_local_wiki_projection(test_project, session_maker=session_maker)

    assert inspection.state is LocalWikiState.conflicted


@pytest.mark.asyncio
async def test_local_wiki_treats_malformed_reserved_frontmatter_as_unowned(
    config_home,
    session_maker,
    test_project,
):
    (config_home / "index.md").write_text("---\ngenerated: [\n---\n", encoding="utf-8")

    inspection = await inspect_local_wiki_projection(test_project, session_maker=session_maker)

    assert inspection.state is LocalWikiState.conflicted


@pytest.mark.asyncio
async def test_local_wiki_requires_existing_project_directory(
    config_home,
    session_maker,
    test_project,
):
    async with db.scoped_session(session_maker) as session:
        project = await session.get(Project, test_project.id)
        assert project is not None
        project.path = str(config_home / "missing")

    with pytest.raises(ValueError, match="Local project directory does not exist"):
        await inspect_local_wiki_projection(test_project, session_maker=session_maker)
