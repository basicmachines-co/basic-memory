from dataclasses import dataclass
from datetime import UTC, datetime

from basic_memory.runtime import NoteHistoryPage, NoteHistoryVersion


@dataclass(frozen=True, slots=True)
class _HistoryVersionSource:
    version_id: str
    key: str
    is_latest: bool
    last_modified: datetime
    size: int
    etag: str


@dataclass(frozen=True, slots=True)
class _HistoryPageSource:
    versions: tuple[_HistoryVersionSource, ...]
    next_key_marker: str | None
    next_version_id_marker: str | None


def test_note_history_version_maps_from_storage_source() -> None:
    modified_at = datetime(2026, 6, 19, 12, 0, tzinfo=UTC)

    version = NoteHistoryVersion.from_source(
        _HistoryVersionSource(
            version_id="12345",
            key="project/note.md",
            is_latest=True,
            last_modified=modified_at,
            size=42,
            etag="67890",
        )
    )

    assert version == NoteHistoryVersion(
        version_id="12345",
        key="project/note.md",
        is_latest=True,
        last_modified=modified_at,
        size=42,
        etag="67890",
    )


def test_note_history_page_maps_from_storage_source() -> None:
    modified_at = datetime(2026, 6, 19, 12, 30, tzinfo=UTC)
    page = NoteHistoryPage.from_source(
        _HistoryPageSource(
            versions=(
                _HistoryVersionSource(
                    version_id="version-1",
                    key="project/note.md",
                    is_latest=False,
                    last_modified=modified_at,
                    size=10,
                    etag="etag-1",
                ),
            ),
            next_key_marker="123",
            next_version_id_marker="456",
        )
    )

    assert page == NoteHistoryPage(
        versions=(
            NoteHistoryVersion(
                version_id="version-1",
                key="project/note.md",
                is_latest=False,
                last_modified=modified_at,
                size=10,
                etag="etag-1",
            ),
        ),
        next_key_marker="123",
        next_version_id_marker="456",
    )
