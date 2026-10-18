"""Reading note identifiers and placing missing notes (#1775)."""

import pytest

from basic_memory.services.note_placement import (
    AmbiguousNotePlacement,
    NoteCreatePlacement,
    note_path_readings,
    place_missing_note,
    split_note_path,
)


@pytest.mark.parametrize(
    ("identifier", "project_permalink", "workspace_permalink", "expected"),
    [
        # A plain path below this project's prefix also reads as a folder named 'demo'.
        ("demo/scratch/note", "demo", None, ("demo/scratch/note", "scratch/note")),
        # Prefixes match by permalink, as routing does.
        ("Demo/scratch/note", "demo", None, ("Demo/scratch/note", "scratch/note")),
        ("My Project/notes/x", "my-project", None, ("My Project/notes/x", "notes/x")),
        # The workspace-qualified spelling cloud permalinks carry.
        ("team/demo/scratch/note", "demo", "team", ("team/demo/scratch/note", "scratch/note")),
        # A memory URL's leading segment routed, so it names only the path below it.
        ("memory://demo/scratch/note", "demo", None, ("scratch/note",)),
        ("memory://team/demo/scratch/note", "demo", "team", ("scratch/note",)),
        # No prefix, another project's prefix, or no project: the path as written.
        ("scratch/note", "demo", None, ("scratch/note",)),
        ("other/scratch/note", "demo", None, ("other/scratch/note",)),
        ("scratch/note", None, None, ("scratch/note",)),
    ],
)
def test_note_path_readings(identifier, project_permalink, workspace_permalink, expected):
    assert (
        note_path_readings(
            identifier,
            project_permalink=project_permalink,
            workspace_permalink=workspace_permalink,
        )
        == expected
    )


@pytest.mark.parametrize(
    ("path", "expected"),
    [("a/b/note", ("a/b", "note")), ("note", ("", "note"))],
)
def test_split_note_path(path, expected):
    assert split_note_path(path) == expected


def _folders(*existing: str):
    async def directory_has_files(folder: str) -> bool:
        return folder in existing

    return directory_has_files


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("existing", "prefix_is_routing", "expected"),
    [
        # Folders storage holds decide, whatever the permalink convention says.
        (("demo/scratch",), True, NoteCreatePlacement(directory="demo/scratch", title="note")),
        (("scratch",), False, NoteCreatePlacement(directory="scratch", title="note")),
        # No evidence: the permalink convention decides.
        ((), True, NoteCreatePlacement(directory="scratch", title="note")),
        ((), False, NoteCreatePlacement(directory="demo/scratch", title="note")),
        # Both folders hold files, so nothing says which one the caller meant.
        (
            ("demo/scratch", "scratch"),
            True,
            AmbiguousNotePlacement(literal="demo/scratch/note", below_prefix="scratch/note"),
        ),
    ],
)
async def test_place_missing_note(existing, prefix_is_routing, expected):
    placement = await place_missing_note(
        ("demo/scratch/note", "scratch/note"),
        directory_has_files=_folders(*existing),
        prefix_is_routing=prefix_is_routing,
    )
    assert placement == expected


@pytest.mark.asyncio
async def test_place_missing_note_with_one_reading_creates_there():
    placement = await place_missing_note(
        ("scratch/note",),
        directory_has_files=_folders(),
        prefix_is_routing=True,
    )
    assert placement == NoteCreatePlacement(directory="scratch", title="note")


@pytest.mark.asyncio
async def test_place_missing_note_never_counts_the_project_root_as_evidence():
    """'demo/note' below the prefix is the root, which always exists, so it cannot
    outweigh a folder that does not exist yet."""

    async def root_is_never_asked(folder: str) -> bool:
        assert folder, "the project root is not evidence"
        return False

    placement = await place_missing_note(
        ("demo/note", "note"),
        directory_has_files=root_is_never_asked,
        prefix_is_routing=False,
    )
    assert placement == NoteCreatePlacement(directory="demo", title="note")
