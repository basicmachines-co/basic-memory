"""Tests for local filesystem event adapters."""

from pathlib import Path

from watchfiles import Change

from basic_memory.index import local_storage_events_from_watchfiles_changes


def test_watchfiles_changes_normalize_to_storage_event_payloads(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    notes_dir = project_root / "notes"
    notes_dir.mkdir(parents=True)
    added = notes_dir / "added.md"
    modified = notes_dir / "modified.md"
    deleted = notes_dir / "deleted.md"
    added.write_text("# Added\n", encoding="utf-8")
    modified.write_text("# Modified\n", encoding="utf-8")

    events = local_storage_events_from_watchfiles_changes(
        project_root=project_root,
        project_prefix="project",
        changes=(
            (Change.added, str(added)),
            (Change.modified, str(modified)),
            (Change.deleted, str(deleted)),
        ),
        bucket_name="local-test",
        event_time="2026-06-20T12:00:00Z",
    )

    assert [(event.event_name, event.object_key) for event in events] == [
        ("OBJECT_DELETED", "project/notes/deleted.md"),
        ("OBJECT_CREATED_PUT", "project/notes/added.md"),
        ("OBJECT_CREATED_PUT", "project/notes/modified.md"),
    ]
    assert {event.bucket_name for event in events} == {"local-test"}
    assert {event.project_path for event in events} == {"project"}
    assert [event.relative_path for event in events] == [
        "notes/deleted.md",
        "notes/added.md",
        "notes/modified.md",
    ]
    assert events[0].size is None
    assert events[1].size == added.stat().st_size


def test_watchfiles_move_batches_emit_deletes_before_creates(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    notes_dir = project_root / "notes"
    archive_dir = project_root / "archive"
    archive_dir.mkdir(parents=True)
    notes_dir.mkdir(parents=True)
    old_path = notes_dir / "move-me.md"
    new_path = archive_dir / "move-me.md"
    new_path.write_text("# Move Me\n", encoding="utf-8")

    events = local_storage_events_from_watchfiles_changes(
        project_root=project_root,
        project_prefix="project",
        changes=(
            (Change.added, str(new_path)),
            (Change.deleted, str(old_path)),
        ),
        event_time="2026-06-20T12:00:00Z",
    )

    assert [(event.event_name, event.object_key) for event in events] == [
        ("OBJECT_DELETED", "project/notes/move-me.md"),
        ("OBJECT_CREATED_PUT", "project/archive/move-me.md"),
    ]


def test_existing_file_delete_event_is_treated_as_atomic_write_update(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    note = project_root / "note.md"
    note.write_text("# Still here\n", encoding="utf-8")

    events = local_storage_events_from_watchfiles_changes(
        project_root=project_root,
        project_prefix="project",
        changes=((Change.deleted, str(note)),),
        event_time="2026-06-20T12:00:00Z",
    )

    assert len(events) == 1
    assert events[0].event_name == "OBJECT_CREATED_PUT"
    assert events[0].object_key == "project/note.md"
    assert events[0].size == note.stat().st_size


def test_temp_hidden_and_directory_changes_are_filtered_before_indexing(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    valid = project_root / "valid.md"
    temp = project_root / "scratch.tmp"
    hidden = project_root / ".draft.md"
    directory = project_root / "notes"
    valid.write_text("# Valid\n", encoding="utf-8")
    temp.write_text("tmp", encoding="utf-8")
    hidden.write_text("# Hidden\n", encoding="utf-8")
    directory.mkdir()

    events = local_storage_events_from_watchfiles_changes(
        project_root=project_root,
        project_prefix="project",
        changes=(
            (Change.added, str(temp)),
            (Change.added, str(hidden)),
            (Change.added, str(directory)),
            (Change.added, str(valid)),
        ),
        event_time="2026-06-20T12:00:00Z",
    )

    assert [event.object_key for event in events] == ["project/valid.md"]


def test_outside_project_changes_are_filtered_before_indexing(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    outside_root = tmp_path / "outside"
    project_root.mkdir()
    outside_root.mkdir()
    valid = project_root / "valid.md"
    outside = outside_root / "outside.md"
    valid.write_text("# Valid\n", encoding="utf-8")
    outside.write_text("# Outside\n", encoding="utf-8")

    events = local_storage_events_from_watchfiles_changes(
        project_root=project_root,
        project_prefix="project",
        changes=(
            (Change.added, str(outside)),
            (Change.added, str(valid)),
        ),
        event_time="2026-06-20T12:00:00Z",
    )

    assert [event.object_key for event in events] == ["project/valid.md"]


def test_editor_swap_and_backup_changes_are_filtered_before_indexing(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    valid = project_root / "valid.md"
    swap = project_root / "valid.md.swp"
    backup = project_root / "valid.md~"
    valid.write_text("# Valid\n", encoding="utf-8")
    swap.write_text("swap", encoding="utf-8")
    backup.write_text("backup", encoding="utf-8")

    events = local_storage_events_from_watchfiles_changes(
        project_root=project_root,
        project_prefix="project",
        changes=(
            (Change.added, str(swap)),
            (Change.added, str(backup)),
            (Change.added, str(valid)),
        ),
        event_time="2026-06-20T12:00:00Z",
    )

    assert [event.object_key for event in events] == ["project/valid.md"]
