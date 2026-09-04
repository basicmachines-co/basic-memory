"""Observation publication regressions for file-first entity updates."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from basic_memory import db
from basic_memory.markdown.schemas import (
    EntityFrontmatter,
    EntityMarkdown,
    Observation as MarkdownObservation,
)
from basic_memory.repository.observation_repository import (
    AcceptedObservationWrite,
    ObservationGenerationWriteResult,
    ObservationRepository,
)
from basic_memory.schemas import Entity as EntitySchema
from basic_memory.services.entity_service import EntityService


def _indexed_markdown(*, title: str, permalink: str, created: datetime) -> EntityMarkdown:
    """Build the file snapshot shared by the publication regressions."""
    return EntityMarkdown(
        frontmatter=EntityFrontmatter(
            metadata={"title": title, "type": "note", "permalink": permalink}
        ),
        observations=[
            MarkdownObservation(content="Initial observation", category="fact"),
            MarkdownObservation(content="Accepted edit observation", category="fact"),
        ],
        relations=[],
        created=created,
        modified=datetime.now(tz=UTC),
    )


@pytest.mark.asyncio
async def test_file_first_update_publishes_entity_and_observations(
    entity_service: EntityService,
) -> None:
    """The post-commit graph publisher preserves file-first update semantics."""
    created = await entity_service.create_entity(
        EntitySchema(
            title="Observation publication semantics",
            directory="notes",
            content="# Observation publication semantics\n\n- [fact] Initial observation",
        )
    )

    result = await entity_service.update_entity_with_content(
        created,
        EntitySchema(
            title="Updated observation publication semantics",
            directory="notes",
            content=(
                "# Updated observation publication semantics\n\n"
                "- [fact] Initial observation\n"
                "- [fact] Accepted edit observation"
            ),
        ),
    )

    assert result.entity.title == "Updated observation publication semantics"
    async with db.scoped_session(entity_service.session_maker) as session:
        observations = await entity_service.observation_repository.find_by_entity(
            session,
            result.entity.id,
        )
    assert [observation.content for observation in observations] == [
        "Initial observation",
        "Accepted edit observation",
    ]


@pytest.mark.asyncio
async def test_markdown_update_defers_observations_to_generation_publication(
    entity_service: EntityService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The entity transaction cannot delete or insert observation rows."""
    created = await entity_service.create_entity(
        EntitySchema(
            title="Deferred observation publication",
            directory="notes",
            content="# Deferred observation publication\n\n- [fact] Initial observation",
        )
    )
    assert created.permalink is not None
    anchor = await entity_service._capture_note_content_anchor(created.id)
    markdown = _indexed_markdown(
        title="Deferred observation publication",
        permalink=created.permalink,
        created=created.created_at,
    )
    publication_calls: list[tuple[int, int, tuple[str, ...]]] = []
    original_replace = ObservationRepository.replace_observations_for_generation

    async def record_publication(
        repository: ObservationRepository,
        session: AsyncSession,
        *,
        entity_id: int,
        generation: int,
        observations: Sequence[AcceptedObservationWrite],
    ) -> ObservationGenerationWriteResult:
        publication_calls.append(
            (entity_id, generation, tuple(observation.content for observation in observations))
        )
        return await original_replace(
            repository,
            session,
            entity_id=entity_id,
            generation=generation,
            observations=observations,
        )

    monkeypatch.setattr(
        ObservationRepository,
        "replace_observations_for_generation",
        record_publication,
    )

    updated = await entity_service.update_markdown_entity_fields(
        Path(created.file_path),
        markdown,
        existing_entity=created,
    )

    assert publication_calls == []
    published = await entity_service._publish_markdown_graph(
        entity=updated,
        markdown=markdown,
        markdown_content=(
            "# Deferred observation publication\n\n"
            "- [fact] Initial observation\n"
            "- [fact] Accepted edit observation"
        ),
        anchor=anchor,
    )

    assert published.id == created.id
    assert len(publication_calls) == 1
    assert publication_calls[0][0] == created.id
    assert publication_calls[0][2] == (
        "Initial observation",
        "Accepted edit observation",
    )
