"""Which cached reads an accepted note change or an index pass retires."""

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from basic_memory.indexing.models import FileIndexOperation, FileIndexResult
from basic_memory.read_cache import ReadCacheChange
from basic_memory.runtime.project_partition import (
    RuntimeAcceptedProjectNoteChange,
    RuntimeProjectNoteOperation,
)
from basic_memory.services.read_cache_changes import accepted_note_read_cache_change

NOTE_ID = str(uuid4())
DETACHED_ID = str(uuid4())
LINKED_ID = str(uuid4())


def _accepted(operation: RuntimeProjectNoteOperation) -> RuntimeAcceptedProjectNoteChange:
    return RuntimeAcceptedProjectNoteChange(
        project_id=1,
        project_external_id=str(uuid4()),
        partition_position=1,
        entity_id=1,
        note_external_id=NOTE_ID,
        permalink="notes/note",
        title="Note",
        operation=operation,
        file_path="notes/note.md",
        accepted_at=datetime.now(timezone.utc),
        source="api",
        previous_file_path=(
            "old/note.md" if operation is RuntimeProjectNoteOperation.moved else None
        ),
    )


def test_a_create_retires_names_and_content_but_no_other_note() -> None:
    change = accepted_note_read_cache_change(
        _accepted(RuntimeProjectNoteOperation.created),
        identity_changed=True,
        detached_note_ids={DETACHED_ID},
    )

    assert change == ReadCacheChange.note_identity({NOTE_ID, DETACHED_ID})
    assert not change.project


def test_a_body_edit_retires_only_the_note_and_content() -> None:
    change = accepted_note_read_cache_change(
        _accepted(RuntimeProjectNoteOperation.updated),
        identity_changed=False,
        linked_note_ids=None,
    )

    assert change == ReadCacheChange.note_content({NOTE_ID})


@pytest.mark.parametrize(
    ("operation", "identity_changed"),
    [
        (RuntimeProjectNoteOperation.moved, False),
        (RuntimeProjectNoteOperation.deleted, False),
        (RuntimeProjectNoteOperation.updated, True),
    ],
)
def test_a_name_change_retires_its_linked_notes(
    operation: RuntimeProjectNoteOperation, identity_changed: bool
) -> None:
    change = accepted_note_read_cache_change(
        _accepted(operation),
        identity_changed=identity_changed,
        detached_note_ids={DETACHED_ID},
        linked_note_ids={LINKED_ID},
    )

    assert change == ReadCacheChange.note_identity({NOTE_ID, DETACHED_ID, LINKED_ID})


@pytest.mark.parametrize(
    ("operation", "identity_changed"),
    [
        (RuntimeProjectNoteOperation.moved, False),
        (RuntimeProjectNoteOperation.deleted, False),
        (RuntimeProjectNoteOperation.updated, True),
    ],
)
def test_a_name_change_with_unknown_links_retires_the_whole_project(
    operation: RuntimeProjectNoteOperation, identity_changed: bool
) -> None:
    change = accepted_note_read_cache_change(
        _accepted(operation), identity_changed=identity_changed, linked_note_ids=None
    )

    assert change == ReadCacheChange.whole_project()


def test_an_unrecorded_mutation_retires_the_whole_project() -> None:
    change = accepted_note_read_cache_change(
        None, identity_changed=False, linked_note_ids=frozenset()
    )

    assert change == ReadCacheChange.whole_project()


def _indexed(operation: FileIndexOperation, *, identity_changed: bool) -> FileIndexResult:
    return FileIndexResult(
        file_path="notes/note.md",
        entity_id=1,
        external_id=NOTE_ID,
        title="Note",
        permalink="notes/note",
        checksum="checksum",
        content_checksum=None,
        operation=operation,
        indexed_bytes=10,
        identity_changed=identity_changed,
        detached_note_ids=frozenset({DETACHED_ID}),
    )


def test_an_indexed_new_file_retires_names_but_no_other_note() -> None:
    result = _indexed(FileIndexOperation.created, identity_changed=True)

    assert result.read_cache_change == ReadCacheChange.note_identity({NOTE_ID, DETACHED_ID})


def test_an_indexed_rename_retires_the_whole_project() -> None:
    result = _indexed(FileIndexOperation.updated, identity_changed=True)

    assert result.read_cache_change == ReadCacheChange.whole_project()


def test_an_indexed_body_change_retires_the_note_and_content() -> None:
    result = _indexed(FileIndexOperation.updated, identity_changed=False)

    assert result.read_cache_change == ReadCacheChange.note_content({NOTE_ID, DETACHED_ID})


def test_a_name_change_bumps_names_content_and_notes_but_not_the_project_epoch() -> None:
    change = ReadCacheChange.note_identity({NOTE_ID})

    assert [group.name for group in change.groups] == ["names", "content", f"note:{NOTE_ID}"]
    assert (change | ReadCacheChange.whole_project()).groups == (
        ReadCacheChange.whole_project().groups
    )
