"""Repository operations for DB-accepted note search rows."""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from basic_memory.indexing.accepted_note_search import AcceptedNoteSearchRow

type SearchIndexSqlValue = str | int | datetime | None
type SearchIndexSqlParams = dict[str, SearchIndexSqlValue]


DELETE_ACCEPTED_NOTE_SEARCH_SQL = text(
    """
    DELETE FROM search_index
    WHERE entity_id = :entity_id AND project_id = :project_id
    """
)

INSERT_ACCEPTED_NOTE_SEARCH_SQL = text(
    """
    INSERT INTO search_index (
        id, title, content_stems, content_snippet, permalink, file_path, type, metadata,
        from_id, to_id, relation_type,
        entity_id, category,
        created_at, updated_at,
        project_id
    ) VALUES (
        :id, :title, :content_stems, :content_snippet, :permalink, :file_path, :type,
        CAST(:metadata AS jsonb),
        NULL, NULL, NULL,
        :entity_id, NULL,
        :created_at, :updated_at,
        :project_id
    )
    """
)

UPSERT_ACCEPTED_NOTE_SEARCH_SQL = text(
    """
    INSERT INTO search_index (
        id, title, content_stems, content_snippet, permalink, file_path, type, metadata,
        from_id, to_id, relation_type,
        entity_id, category,
        created_at, updated_at,
        project_id
    ) VALUES (
        :id, :title, :content_stems, :content_snippet, :permalink, :file_path, :type,
        CAST(:metadata AS jsonb),
        NULL, NULL, NULL,
        :entity_id, NULL,
        :created_at, :updated_at,
        :project_id
    )
    ON CONFLICT (permalink, project_id) WHERE permalink IS NOT NULL DO UPDATE SET
        id = EXCLUDED.id,
        title = EXCLUDED.title,
        content_stems = EXCLUDED.content_stems,
        content_snippet = EXCLUDED.content_snippet,
        file_path = EXCLUDED.file_path,
        type = EXCLUDED.type,
        metadata = EXCLUDED.metadata,
        from_id = EXCLUDED.from_id,
        to_id = EXCLUDED.to_id,
        relation_type = EXCLUDED.relation_type,
        entity_id = EXCLUDED.entity_id,
        category = EXCLUDED.category,
        created_at = EXCLUDED.created_at,
        updated_at = EXCLUDED.updated_at
    """
)


def accepted_note_search_insert_statement(session: AsyncSession):
    """Return the insert statement supported by the active search table backend."""
    if session.get_bind().dialect.name == "sqlite":
        return INSERT_ACCEPTED_NOTE_SEARCH_SQL
    return UPSERT_ACCEPTED_NOTE_SEARCH_SQL


def accepted_note_search_insert_params(
    row: AcceptedNoteSearchRow,
) -> SearchIndexSqlParams:
    """Build SQL parameters for one accepted-note search row."""
    return {
        "id": row.id,
        "title": row.title,
        "content_stems": row.content_stems,
        "content_snippet": row.content_snippet,
        "permalink": row.permalink,
        "file_path": row.file_path,
        "type": row.item_type,
        "metadata": json.dumps({"note_type": row.note_type}),
        "entity_id": row.entity_id,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "project_id": row.project_id,
    }


class AcceptedNoteSearchRepository:
    """Explicit-session repository for accepted-note hot search refreshes."""

    def __init__(self, *, project_id: int) -> None:
        self.project_id = project_id

    async def refresh_entity(
        self,
        session: AsyncSession,
        row: AcceptedNoteSearchRow,
    ) -> None:
        """Replace one accepted entity search row inside the caller's transaction."""
        if row.project_id != self.project_id:
            raise ValueError(
                f"Accepted note search row project_id {row.project_id} "
                f"does not match repository project_id {self.project_id}"
            )

        await session.execute(
            DELETE_ACCEPTED_NOTE_SEARCH_SQL,
            {"entity_id": row.entity_id, "project_id": row.project_id},
        )
        await session.execute(
            accepted_note_search_insert_statement(session),
            accepted_note_search_insert_params(row),
        )

    async def delete_entity(
        self,
        session: AsyncSession,
        entity_id: int,
    ) -> None:
        """Delete all accepted-note search rows for one entity."""
        await session.execute(
            DELETE_ACCEPTED_NOTE_SEARCH_SQL,
            {"entity_id": entity_id, "project_id": self.project_id},
        )
