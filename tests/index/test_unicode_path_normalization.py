"""NFC and NFD spellings of one file name must produce one index key (#1275).

macOS and Windows resolve both spellings to the same file but report whichever
form is stored on disk, and Syncthing keeps NFD names on macOS. Paths are
identity keys, so they are normalized to NFC where they enter the index:
scanner, watcher, title -> path, move destination and permalink generation.
"""

import sys
import unicodedata
from pathlib import Path

import pytest
from watchfiles import Change

from basic_memory.api.v2.routers.knowledge_router import _canonical_file_path
from basic_memory.index.filesystem import local_storage_events_from_watchfiles_changes
from basic_memory.index.local_project import scan_local_project_index_files
from basic_memory.runtime.note_move import normalize_note_move_destination_path
from basic_memory.schemas import Entity as EntitySchema
from basic_memory.utils import generate_permalink, nfc_path

TITLE = "Проверка нормализации — Saarbrücken й"
NFC = unicodedata.normalize("NFC", TITLE)
NFD = unicodedata.normalize("NFD", TITLE)

normalizing_platform = pytest.mark.skipif(
    sys.platform != "darwin",
    reason="Off macOS NFC and NFD names are two distinct files, so paths stay byte-exact",
)


def _write_nfd_note(project_root: Path) -> Path:
    notes = project_root / "notes"
    notes.mkdir(parents=True)
    note = notes / f"{NFD}.md"
    note.write_text("# Note\n", encoding="utf-8")
    return note


def test_spellings_differ() -> None:
    assert NFC != NFD


def test_nfc_path_normalizes_on_macos(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    assert nfc_path(f"notes/{NFD}.md") == f"notes/{NFC}.md"


@pytest.mark.parametrize("platform", ["linux", "win32"])
def test_nfc_path_keeps_paths_byte_exact_elsewhere(monkeypatch, platform: str) -> None:
    # Linux filesystems and NTFS store NFC and NFD names as two different files.
    monkeypatch.setattr(sys, "platform", platform)
    assert nfc_path(f"notes/{NFD}.md") == f"notes/{NFD}.md"


def test_generate_permalink_is_the_same_for_both_spellings() -> None:
    assert generate_permalink(f"notes/{NFD}.md") == generate_permalink(f"notes/{NFC}.md")


@normalizing_platform
def test_watcher_event_for_an_nfd_name_uses_the_nfc_key(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    note = _write_nfd_note(project_root)

    events = local_storage_events_from_watchfiles_changes(
        project_root=project_root,
        project_prefix="project",
        changes=((Change.modified, str(note)),),
        bucket_name="local-test",
        event_time="2026-10-14T12:00:00Z",
    )

    assert [event.relative_path for event in events] == [f"notes/{NFC}.md"]
    assert [event.object_key for event in events] == [f"project/notes/{NFC}.md"]


@normalizing_platform
def test_scanner_reports_an_nfd_name_under_the_nfc_key(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    _write_nfd_note(project_root)

    scan = scan_local_project_index_files(project_root, ignore_patterns=set())

    assert scan.file_paths == (f"notes/{NFC}.md",)


@normalizing_platform
def test_move_destination_is_normalized() -> None:
    assert normalize_note_move_destination_path(f"notes/{NFD}.md") == f"notes/{NFC}.md"


@normalizing_platform
def test_title_derived_path_is_normalized() -> None:
    entity = EntitySchema(title=NFD, directory="notes", note_type="note")
    assert entity.file_path == f"notes/{NFC}.md"


@normalizing_platform
def test_title_derived_path_normalizes_the_directory_too() -> None:
    from_nfd = EntitySchema(title="Plain", directory=f"notes/{NFD}", note_type="note")
    from_nfc = EntitySchema(title="Plain", directory=f"notes/{NFC}", note_type="note")
    # The sanitizer drops combining marks, so an NFD directory must be normalized
    # before sanitizing or it loses its accents instead of matching the NFC one.
    assert from_nfd.file_path == from_nfc.file_path
    assert unicodedata.normalize("NFC", from_nfd.file_path) == from_nfd.file_path
    assert "ü" in from_nfd.file_path and "й" in from_nfd.file_path


@normalizing_platform
def test_index_file_resolves_an_nfd_name_to_the_nfc_key(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    _write_nfd_note(project_root)

    for requested in (f"{NFC}.md", f"{NFD}.md"):
        assert _canonical_file_path(project_root, ["notes", requested]) == f"notes/{NFC}.md"
