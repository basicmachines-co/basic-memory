"""Publication fault injection and read-only journal boundaries."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from basic_memory import db
from basic_memory.config import BasicMemoryConfig, ProjectEntry
from basic_memory.models.project import AcceptedProjectNoteChange
from basic_memory.okf.export import export_project, recorded_history, snapshot_files
from basic_memory.okf.render import ExportFile, ExportSnapshot, render_bundle
from basic_memory.okf.validation import check_bundle


@pytest.fixture
def source_config(config_home):
    root = config_home / "source"
    root.mkdir()
    (root / "a.md").write_text("---\ntype: note\n---\nA")
    return BasicMemoryConfig(projects={"export": ProjectEntry(path=str(root))})


@pytest.mark.asyncio
async def test_source_and_destination_changes_abort(source_config, tmp_path, monkeypatch):
    import basic_memory.okf.export as exporting

    root = Path(source_config.projects["export"].path)
    destination = tmp_path / "bundle"
    original_check = exporting.check_bundle

    def mutate_source(staging):
        (root / "a.md").write_text("changed")
        return original_check(staging)

    monkeypatch.setattr(exporting, "check_bundle", mutate_source)
    with pytest.raises(ValueError, match="Project changed"):
        await export_project(source_config, "export", destination)
    assert not destination.exists()

    def create_destination(staging):
        destination.mkdir()
        (destination / "keep").write_text("keep")
        return original_check(staging)

    monkeypatch.setattr(exporting, "check_bundle", create_destination)
    with pytest.raises(ValueError, match="appeared"):
        await export_project(source_config, "export", destination)
    assert (destination / "keep").read_text() == "keep"


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_restore", [False, True])
async def test_publish_failure_preserves_previous_bundle(
    source_config, tmp_path, monkeypatch, fail_restore
):
    destination = tmp_path / "bundle"
    destination.mkdir()
    (destination / "keep").write_text("keep")
    rename = Path.rename

    def fail_publish(path, target):
        if path.name == "bundle" and path != destination:
            raise OSError("publish failed")
        if fail_restore and ".bm-okf-backup-" in path.name:
            raise OSError("restore failed")
        return rename(path, target)

    monkeypatch.setattr(Path, "rename", fail_publish)
    with pytest.raises(OSError, match="failed"):
        await export_project(source_config, "export", destination, replace=True)
    if fail_restore:
        backups = list(tmp_path.glob(".bundle.bm-okf-backup-*"))
        assert len(backups) == 1
        assert (backups[0] / "keep").read_text() == "keep"
    else:
        assert (destination / "keep").read_text() == "keep"
    assert not list(tmp_path.glob(".bm-okf-*"))


@pytest.mark.asyncio
async def test_symlink_and_missing_source_are_rejected(source_config, tmp_path, monkeypatch):
    root = Path(source_config.projects["export"].path)
    destination = tmp_path / "bundle"
    other = tmp_path / "other"
    other.mkdir()
    destination.symlink_to(other, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        await export_project(source_config, "export", destination)
    destination.unlink()
    import basic_memory.okf.export as exporting

    original_check = exporting.check_bundle

    def create_symlink(staging):
        destination.symlink_to(other, target_is_directory=True)
        return original_check(staging)

    monkeypatch.setattr(exporting, "check_bundle", create_symlink)
    with pytest.raises(ValueError, match="became a symlink"):
        await export_project(source_config, "export", destination, replace=True)
    (root / "a.md").unlink()
    root.rmdir()
    with pytest.raises(ValueError, match="does not exist"):
        await export_project(source_config, "export", destination)
    source_config.projects["export"].path = "relative"
    with pytest.raises(ValueError, match="absolute"):
        await export_project(source_config, "export", destination)


def test_unreadable_subtree_is_a_failure(tmp_path, monkeypatch):
    import os

    def failed_walk(root, *, onerror, **kwargs):
        onerror(PermissionError(13, "denied", str(root / "sub")))
        yield str(root), [], []

    monkeypatch.setattr(os, "walk", failed_walk)
    assert check_bundle(tmp_path).diagnostics[0].rule == "filesystem.read"
    with pytest.raises(OSError, match="Incomplete project scan"):
        snapshot_files(tmp_path)


@pytest.mark.parametrize("bm", ["broken", "{okf_export: {version: 1}}"])
def test_extension_collision_is_not_overwritten(bm):
    snapshot = ExportSnapshot("p", (ExportFile("a.md", f"---\nbm: {bm}\n---\n".encode()),))
    with pytest.raises(ValueError, match="extension collision"):
        render_bundle(snapshot)


def test_ambiguous_alias_and_semantic_opt_out():
    snapshot = ExportSnapshot(
        "p",
        (
            ExportFile("one/a.md", b"---\ntitle: Same\n---\n"),
            ExportFile("two/a.md", b"---\ntitle: Same\n---\n"),
            ExportFile("source.md", b"---\nbm_parse_semantics: false\n---\n[[Same]]"),
        ),
    )
    output = {file.path: file.content for file in render_bundle(snapshot)}
    assert b"[Same](/Same)" in output["source.md"]
    assert b"relations: []" in output["source.md"]
    assert b"[one](one/index.md)" in output["index.md"]


@pytest.mark.asyncio
async def test_journal_materialization_and_identity(app_config, test_project, engine_factory):
    _, session_maker = engine_factory
    root = Path(test_project.path).resolve()
    assert await recorded_history(app_config, "missing", root) == ()
    with pytest.raises(ValueError, match="differs"):
        await recorded_history(app_config, test_project.name, root / "wrong")
    accepted_at = datetime(2026, 9, 14, tzinfo=UTC)
    async with db.scoped_session(session_maker) as session:
        project = await session.get(type(test_project), test_project.id)
        assert project is not None
        project.partition_position = 1
        session.add(
            AcceptedProjectNoteChange(
                project_id=project.id,
                project_external_id=project.external_id,
                partition_position=1,
                entity_id=1,
                note_external_id="note",
                permalink="a",
                title="A",
                operation="create",
                file_path="a.md",
                accepted_at=accepted_at,
                source="cli",
            )
        )
    # Journal acceptance remains useful even when its materialization marker lags.
    assert len(await recorded_history(app_config, test_project.name, root)) == 1
    async with db.scoped_session(session_maker) as session:
        from sqlalchemy import select

        change = (await session.execute(select(AcceptedProjectNoteChange))).scalar_one()
        change.materialized_at = accepted_at
    history = await recorded_history(app_config, test_project.name, root)
    assert len(history) == 1
    assert history[0].path == "a.md"
    assert history[0].accepted_at.date() == accepted_at.date()


@pytest.mark.asyncio
@pytest.mark.parametrize("partial_journal", [False, True])
async def test_pre_journal_database_exports_without_migration(
    source_config, tmp_path, monkeypatch, partial_journal
):
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from basic_memory.config import APP_DATABASE_NAME, DatabaseBackend

    source_config.database_backend = DatabaseBackend.SQLITE
    database_path = source_config.data_dir_path / APP_DATABASE_NAME
    database_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    try:
        async with engine.begin() as connection:
            await connection.execute(text("CREATE TABLE project (id INTEGER PRIMARY KEY)"))
            if partial_journal:
                await connection.execute(
                    text("CREATE TABLE accepted_project_note_change (id INTEGER PRIMARY KEY)")
                )

        async def existing_db(**kwargs):
            assert kwargs["ensure_migrations"] is False
            return engine, async_sessionmaker(engine)

        monkeypatch.setattr(db, "get_or_create_db", existing_db)
        before = database_path.read_bytes()
        destination = tmp_path / "bundle"
        report = await export_project(source_config, "export", destination)
        assert report.success and report.concepts == 1
        assert database_path.read_bytes() == before
        assert "\n## " not in (destination / "log.md").read_text()
    finally:
        await engine.dispose()
