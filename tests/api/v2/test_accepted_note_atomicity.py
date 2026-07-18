"""Route regressions for indivisible accepted-note snapshots."""

from dataclasses import dataclass, field
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.deps.services import get_note_content_materialization_provider
from basic_memory.models import Entity, NoteContent, Observation, Project, Relation
from basic_memory.runtime.note_content import (
    RuntimeAcceptedNoteChange,
    RuntimeNoteContentResponsePayload,
)
from basic_memory.schemas.v2 import EntityResponseV2


@dataclass(slots=True)
class PausedNoteContentMaterializer:
    """Record accepted changes without writing their files."""

    accepted_changes: list[RuntimeAcceptedNoteChange[RuntimeNoteContentResponsePayload]] = field(
        default_factory=list
    )

    async def materialize_write_change(
        self,
        accepted: RuntimeAcceptedNoteChange[RuntimeNoteContentResponsePayload],
    ) -> RuntimeAcceptedNoteChange[RuntimeNoteContentResponsePayload]:
        self.accepted_changes.append(accepted)
        return accepted


@dataclass(frozen=True, slots=True)
class PersistedAcceptedSnapshot:
    entity: Entity
    note_content: NoteContent
    observations: tuple[Observation, ...]
    relations: tuple[Relation, ...]
    search_content: str


async def _load_persisted_snapshot(
    session_maker: async_sessionmaker[AsyncSession],
    *,
    project_id: int,
    entity_id: int,
) -> PersistedAcceptedSnapshot:
    async with db.scoped_session(session_maker) as session:
        entity = await session.get(Entity, entity_id)
        note_content = await session.get(NoteContent, entity_id)
        observations = tuple(
            (
                await session.scalars(
                    select(Observation)
                    .where(
                        Observation.project_id == project_id,
                        Observation.entity_id == entity_id,
                    )
                    .order_by(Observation.id)
                )
            ).all()
        )
        relations = tuple(
            (
                await session.scalars(
                    select(Relation)
                    .where(
                        Relation.project_id == project_id,
                        Relation.from_id == entity_id,
                    )
                    .order_by(Relation.id)
                )
            ).all()
        )
        search_content = (
            await session.execute(
                text("""
                    SELECT content_stems
                    FROM search_index
                    WHERE project_id = :project_id
                      AND entity_id = :entity_id
                      AND type = 'entity'
                """),
                {"project_id": project_id, "entity_id": entity_id},
            )
        ).scalar_one()

    assert entity is not None
    assert note_content is not None
    return PersistedAcceptedSnapshot(
        entity=entity,
        note_content=note_content,
        observations=observations,
        relations=relations,
        search_content=str(search_content),
    )


@pytest.mark.asyncio
async def test_create_and_update_persist_complete_snapshot_before_materialization(
    app: FastAPI,
    client: AsyncClient,
    db_backend: str,
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
    v2_project_url: str,
) -> None:
    """Create and replace commit content, graph, and search before the file write."""
    if db_backend != "sqlite":
        pytest.skip("This regression intentionally inspects the SQLite FTS row")

    materializer = PausedNoteContentMaterializer()
    app.dependency_overrides[get_note_content_materialization_provider] = lambda: materializer

    create_response = await client.post(
        f"{v2_project_url}/knowledge/entities",
        json={
            "title": "Atomic Snapshot",
            "directory": "accepted",
            "content": """
# Atomic Snapshot

## Facts
- [fact] Create snapshot observation
- "created link" [[Create Target]]
""",
        },
    )

    assert create_response.status_code == 202
    created = EntityResponseV2.model_validate(create_response.json())
    assert created.content is not None
    assert created.file_write_status == "pending"
    note_path = Path(test_project.path) / created.file_path
    assert not note_path.exists()

    created_snapshot = await _load_persisted_snapshot(
        session_maker,
        project_id=test_project.id,
        entity_id=created.id,
    )
    assert created_snapshot.entity.title == "Atomic Snapshot"
    assert created_snapshot.note_content.markdown_content == created.content
    assert created_snapshot.note_content.db_version == 1
    assert created_snapshot.note_content.file_write_status == "pending"
    assert [observation.content for observation in created_snapshot.observations] == [
        "Create snapshot observation"
    ]
    assert [
        (relation.relation_type, relation.to_name) for relation in created_snapshot.relations
    ] == [("created link", "Create Target")]
    assert "Create snapshot observation" in created_snapshot.search_content

    update_response = await client.put(
        f"{v2_project_url}/knowledge/entities/{created.external_id}",
        json={
            "title": "Atomic Snapshot",
            "directory": "accepted",
            "content": """
# Atomic Snapshot

## Replaced Facts
- [decision] Replacing update observation
- "updated link" [[Update Target]]
""",
        },
    )

    assert update_response.status_code == 202
    updated = EntityResponseV2.model_validate(update_response.json())
    assert updated.content is not None
    assert updated.file_write_status == "pending"
    assert not note_path.exists()

    updated_snapshot = await _load_persisted_snapshot(
        session_maker,
        project_id=test_project.id,
        entity_id=updated.id,
    )
    assert updated_snapshot.note_content.markdown_content == updated.content
    assert updated_snapshot.note_content.db_version == 2
    assert updated_snapshot.note_content.file_write_status == "pending"
    assert [observation.content for observation in updated_snapshot.observations] == [
        "Replacing update observation"
    ]
    assert [
        (relation.relation_type, relation.to_name) for relation in updated_snapshot.relations
    ] == [("updated link", "Update Target")]
    assert "Replacing update observation" in updated_snapshot.search_content
    assert "Create snapshot observation" not in updated_snapshot.search_content
    assert len(materializer.accepted_changes) == 2
