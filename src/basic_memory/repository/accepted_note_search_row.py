"""Repository-level values for accepted-note search persistence."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class AcceptedNoteSearchRow:
    """Entity-level search row for an accepted DB-first note snapshot."""

    id: int
    title: str
    content_stems: str
    content_snippet: str
    permalink: str | None
    file_path: str
    item_type: str
    note_type: str | None
    entity_id: int
    created_at: datetime
    updated_at: datetime
    project_id: int
