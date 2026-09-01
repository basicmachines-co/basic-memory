"""Tests for accepted note write persistence handoffs."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from basic_memory.indexing.accepted_note_search import AcceptedNoteSearchRow
from basic_memory.indexing.accepted_note_write_runner import (
    AcceptedNoteWriteRepositories,
    accept_note_content_write,
    accepted_note_content_write_from_markdown,
    accepted_note_search_row_from_entity,
    accepted_pending_entity_write_from_prepared,
    apply_accepted_prepared_entity_fields,
    create_accepted_pending_entity,
    delete_accepted_note,
    delete_accepted_note_entity,
    persist_accepted_note_move,
    persist_accepted_note_snapshot,
    prepare_accepted_note_create,
    prepare_accepted_note_edit,
    prepare_accepted_note_move,
    prepare_accepted_note_replace,
    refresh_accepted_note_search_index,
    delete_accepted_note_search_index,
)
from basic_memory.models import Entity, NoteContent
from basic_memory.markdown.schemas import (
    EntityFrontmatter,
    EntityMarkdown,
    Observation as MarkdownObservation,
    Relation as MarkdownRelation,
)
from basic_memory.repository import (
    AcceptedNoteContentWrite,
    AcceptedObservationWrite,
    AcceptedRelationWrite,
    AcceptedSectionWrite,
)
from basic_memory.repository.memory_time_index_repository import (
    AcceptedTemporalAssertion,
    TemporalGenerationWriteResult,
)
from basic_memory.repository.note_section_repository import SectionGenerationWriteResult
from basic_memory.repository.observation_repository import ObservationGenerationWriteResult
from basic_memory.repository.relation_repository import RelationGenerationWriteResult
from basic_memory.repository.entity_repository import AcceptedPendingEntityWrite
from basic_memory.schemas.base import Entity as EntitySchema
from basic_memory.services.note_preparation import (
    PreparedEntityFields,
    PreparedEntityMove,
    PreparedEntityWrite,
)


_PreparedFields = PreparedEntityFields
_PreparedWrite = PreparedEntityWrite
_PreparedMove = PreparedEntityMove

_PREPARED_CREATED_AT = datetime(2024, 1, 15, 10, 30, tzinfo=UTC)
_PREPARED_UPDATED_AT = datetime(2024, 1, 16, 11, 45, tzinfo=UTC)


class _FlushSession:
    def __init__(self) -> None:
        self.flush_count = 0

    async def flush(self) -> None:
        self.flush_count += 1


class _PendingEntityRepository:
    def __init__(self, entity: Entity) -> None:
        self.entity = entity
        self.calls: list[tuple[AsyncSession, AcceptedPendingEntityWrite]] = []

    async def create_pending_accepted_entity(
        self,
        session: AsyncSession,
        write: AcceptedPendingEntityWrite,
    ) -> Entity:
        self.calls.append((session, write))
        return self.entity


class _NoteContentRepository:
    def __init__(self, result: NoteContent) -> None:
        self.result = result
        self.calls: list[tuple[AsyncSession, AcceptedNoteContentWrite]] = []

    async def accept_write(
        self,
        session: AsyncSession,
        write: AcceptedNoteContentWrite,
    ) -> NoteContent:
        self.calls.append((session, write))
        return self.result


class _SearchRepository:
    def __init__(self, events: list[tuple[str, int]] | None = None) -> None:
        self.calls: list[AcceptedNoteSearchRow] = []
        self.deleted_entity_ids: list[int] = []
        self.deleted_vector_entity_ids: list[int] = []
        self.events = events

    async def refresh_entity(
        self,
        session: AsyncSession,
        row: AcceptedNoteSearchRow,
    ) -> None:
        self.calls.append(row)

    async def delete_entity(
        self,
        session: AsyncSession,
        entity_id: int,
    ) -> None:
        self.deleted_entity_ids.append(entity_id)
        if self.events is not None:
            self.events.append(("search", entity_id))

    async def delete_entity_vectors(
        self,
        session: AsyncSession,
        entity_id: int,
    ) -> None:
        self.deleted_vector_entity_ids.append(entity_id)
        if self.events is not None:
            self.events.append(("vectors", entity_id))


class _ObservationRepository:
    def __init__(self) -> None:
        self.calls: list[tuple[int, Sequence[AcceptedObservationWrite]]] = []

    async def replace_observations_for_generation(
        self,
        session: AsyncSession,
        *,
        entity_id: int,
        generation: int,
        observations: Sequence[AcceptedObservationWrite],
    ) -> ObservationGenerationWriteResult:
        self.calls.append((entity_id, observations))
        return ObservationGenerationWriteResult(generation_is_current=True)


class _SectionRepository:
    def __init__(self) -> None:
        self.calls: list[tuple[int, Sequence[AcceptedSectionWrite]]] = []

    async def replace_sections_for_generation(
        self,
        session: AsyncSession,
        *,
        entity_id: int,
        generation: int,
        sections: Sequence[AcceptedSectionWrite],
    ) -> SectionGenerationWriteResult:
        self.calls.append((entity_id, sections))
        return SectionGenerationWriteResult(generation_is_current=True)


class _TemporalRepository:
    def __init__(self) -> None:
        self.calls: list[tuple[int, Sequence[AcceptedTemporalAssertion]]] = []

    async def replace_assertions_for_generation(
        self,
        session: AsyncSession,
        *,
        entity_id: int,
        generation: int,
        assertions: Sequence[AcceptedTemporalAssertion],
    ) -> TemporalGenerationWriteResult:
        self.calls.append((entity_id, assertions))
        return TemporalGenerationWriteResult(generation_is_current=True)


class _RelationRepository:
    def __init__(self) -> None:
        self.calls: list[tuple[int, Sequence[AcceptedRelationWrite]]] = []

    async def begin_relation_generation_publication(
        self,
        session: AsyncSession,
        *,
        entity_id: int,
        generation: int,
    ) -> RelationGenerationWriteResult:
        raise AssertionError(
            "relation publication was not expected inside the accepted transaction"
        )

    async def upsert_relation_generation(
        self,
        session: AsyncSession,
        *,
        entity_id: int,
        generation: int,
        relations: Sequence[AcceptedRelationWrite],
    ) -> RelationGenerationWriteResult:
        raise AssertionError(
            "relation publication was not expected inside the accepted transaction"
        )

    async def cleanup_relation_generations(
        self,
        session: AsyncSession,
        *,
        entity_id: int,
        generation: int,
    ) -> RelationGenerationWriteResult:
        raise AssertionError("relation cleanup was not expected inside the accepted transaction")


def test_accepted_note_write_repositories_name_persistence_behavior() -> None:
    """Accepted-note persistence should be a behavior capability, not Callable aliases."""

    class _Repositories:
        def pending_entity_repository(self, project_id: int) -> _PendingEntityRepository:
            assert project_id == 7
            return _PendingEntityRepository(_entity())

        def note_content_repository(self, project_id: int) -> _NoteContentRepository:
            assert project_id == 7
            return _NoteContentRepository(_note_content())

        def search_repository(self, project_id: int) -> _SearchRepository:
            assert project_id == 7
            return _SearchRepository()

        def observation_repository(self, project_id: int) -> _ObservationRepository:
            assert project_id == 7
            return _ObservationRepository()

        def section_repository(self, project_id: int) -> _SectionRepository:
            assert project_id == 7
            return _SectionRepository()

        def temporal_repository(self, project_id: int) -> _TemporalRepository:
            assert project_id == 7
            return _TemporalRepository()

        def relation_repository(self, project_id: int) -> _RelationRepository:
            assert project_id == 7
            return _RelationRepository()

    repositories: AcceptedNoteWriteRepositories = _Repositories()

    assert isinstance(repositories.pending_entity_repository(7), _PendingEntityRepository)
    assert isinstance(repositories.note_content_repository(7), _NoteContentRepository)
    assert isinstance(repositories.search_repository(7), _SearchRepository)
    assert isinstance(repositories.observation_repository(7), _ObservationRepository)
    assert isinstance(repositories.section_repository(7), _SectionRepository)
    assert isinstance(repositories.temporal_repository(7), _TemporalRepository)
    assert isinstance(repositories.relation_repository(7), _RelationRepository)


class _RelationSourcesResult:
    def __init__(self, source_ids: tuple[int, ...]) -> None:
        self._source_ids = source_ids

    def scalars(self) -> tuple[int, ...]:
        return self._source_ids


class _DeleteSession:
    def __init__(
        self,
        events: list[tuple[str, int]] | None = None,
        *,
        relation_source_ids: tuple[int, ...] = (),
    ) -> None:
        self.deleted: list[object] = []
        self.scalar_count = 0
        self.execute_count = 0
        self.execute_count_at_entity_delete = 0
        self.events = events
        self.relation_source_ids = relation_source_ids

    async def scalar(self, statement: object) -> int:
        assert statement is not None
        self.scalar_count += 1
        return 42

    async def execute(self, statement: object) -> _RelationSourcesResult:
        assert statement is not None
        self.execute_count += 1
        return _RelationSourcesResult(self.relation_source_ids)

    async def delete(self, entity: object) -> None:
        self.deleted.append(entity)
        self.execute_count_at_entity_delete = self.execute_count
        if self.events is not None:
            self.events.append(("entity", cast(Entity, entity).id))


class _CreatePreparer:
    def __init__(self, prepared: PreparedEntityWrite) -> None:
        self.prepared = prepared
        self.calls: list[tuple[EntitySchema, bool, AsyncSession | None]] = []
        self.skip_conflict_checks: list[bool] = []

    async def prepare_create_entity_content(
        self,
        schema: EntitySchema,
        *,
        check_storage_exists: bool = True,
        skip_conflict_check: bool = False,
        session: AsyncSession | None = None,
    ) -> PreparedEntityWrite:
        self.calls.append((schema, check_storage_exists, session))
        self.skip_conflict_checks.append(skip_conflict_check)
        return self.prepared


class _ReplacePreparer:
    def __init__(self, prepared: PreparedEntityWrite) -> None:
        self.prepared = prepared
        self.calls: list[tuple[Entity, EntitySchema, str, AsyncSession | None]] = []

    async def prepare_update_entity_content(
        self,
        entity: Entity,
        schema: EntitySchema,
        existing_content: str,
        *,
        session: AsyncSession | None = None,
    ) -> PreparedEntityWrite:
        self.calls.append((entity, schema, existing_content, session))
        return self.prepared


class _EditPreparer:
    def __init__(self, prepared: PreparedEntityWrite) -> None:
        self.prepared = prepared
        self.calls: list[
            tuple[
                Entity,
                str,
                str,
                str,
                str | None,
                str | None,
                int,
                bool,
                dict[str, Any] | None,
                AsyncSession | None,
            ]
        ] = []

    async def prepare_edit_entity_content(
        self,
        entity: Entity,
        current_content: str,
        *,
        operation: str,
        content: str,
        section: str | None = None,
        find_text: str | None = None,
        expected_replacements: int = 1,
        replace_subsections: bool = True,
        metadata: dict[str, Any] | None = None,
        session: AsyncSession | None = None,
    ) -> PreparedEntityWrite:
        self.calls.append(
            (
                entity,
                current_content,
                operation,
                content,
                section,
                find_text,
                expected_replacements,
                replace_subsections,
                metadata,
                session,
            )
        )
        return self.prepared


class _MovePreparer:
    def __init__(self, prepared: PreparedEntityMove) -> None:
        self.prepared = prepared
        self.calls: list[tuple[Entity, str, str, bool, AsyncSession | None]] = []

    async def prepare_move_entity_content(
        self,
        entity: Entity,
        current_content: str,
        destination_path: str,
        *,
        should_update_permalink: bool,
        session: AsyncSession | None = None,
    ) -> PreparedEntityMove:
        self.calls.append(
            (entity, current_content, destination_path, should_update_permalink, session)
        )
        return self.prepared

    async def verify_move_destination_absent(
        self,
        *,
        source_file_path: str,
        destination_file_path: str,
    ) -> None:
        return None


@dataclass(slots=True)
class _SelfRelationResolver:
    """Resolve the exact self-link names selected by one focused test."""

    resolved_names: set[str] = field(default_factory=set)
    calls: list[tuple[str, Entity, AsyncSession | None]] = field(default_factory=list)

    async def resolve_deferred_self_relation(
        self,
        target: str,
        entity: Entity,
        session: AsyncSession | None = None,
    ) -> Entity | None:
        self.calls.append((target, entity, session))
        return entity if target in self.resolved_names else None


def _unexpected_pending_entity_repository(_project_id: int) -> _PendingEntityRepository:
    raise AssertionError("pending entity repository was not expected")


def _unexpected_note_content_repository(_project_id: int) -> _NoteContentRepository:
    raise AssertionError("note content repository was not expected")


def _unexpected_search_repository(_project_id: int) -> _SearchRepository:
    raise AssertionError("search repository was not expected")


def _unexpected_observation_repository(_project_id: int) -> _ObservationRepository:
    raise AssertionError("observation repository was not expected")


def _unexpected_relation_repository(_project_id: int) -> _RelationRepository:
    raise AssertionError("relation repository was not expected")


def _unexpected_section_repository(_project_id: int) -> _SectionRepository:
    raise AssertionError("section repository was not expected")


def _unexpected_temporal_repository(_project_id: int) -> _TemporalRepository:
    raise AssertionError("temporal repository was not expected")


@dataclass(frozen=True, slots=True)
class _RepositoryProvider:
    pending_entity_repository_result: _PendingEntityRepository | None = None
    note_content_repository_result: _NoteContentRepository | None = None
    search_repository_result: _SearchRepository | None = None
    observation_repository_result: _ObservationRepository | None = None
    section_repository_result: _SectionRepository | None = None
    temporal_repository_result: _TemporalRepository | None = None
    relation_repository_result: _RelationRepository | None = None

    def pending_entity_repository(self, project_id: int) -> _PendingEntityRepository:
        if self.pending_entity_repository_result is None:
            return _unexpected_pending_entity_repository(project_id)
        return self.pending_entity_repository_result

    def note_content_repository(self, project_id: int) -> _NoteContentRepository:
        if self.note_content_repository_result is None:
            return _unexpected_note_content_repository(project_id)
        return self.note_content_repository_result

    def search_repository(self, project_id: int) -> _SearchRepository:
        if self.search_repository_result is None:
            return _unexpected_search_repository(project_id)
        return self.search_repository_result

    def observation_repository(self, project_id: int) -> _ObservationRepository:
        if self.observation_repository_result is None:
            return _unexpected_observation_repository(project_id)
        return self.observation_repository_result

    def section_repository(self, project_id: int) -> _SectionRepository:
        if self.section_repository_result is None:
            return _unexpected_section_repository(project_id)
        return self.section_repository_result

    def temporal_repository(self, project_id: int) -> _TemporalRepository:
        if self.temporal_repository_result is None:
            return _unexpected_temporal_repository(project_id)
        return self.temporal_repository_result

    def relation_repository(self, project_id: int) -> _RelationRepository:
        if self.relation_repository_result is None:
            return _unexpected_relation_repository(project_id)
        return self.relation_repository_result


def _repository_provider(
    *,
    pending_entity_repository: _PendingEntityRepository | None = None,
    note_content_repository: _NoteContentRepository | None = None,
    search_repository: _SearchRepository | None = None,
    observation_repository: _ObservationRepository | None = None,
    section_repository: _SectionRepository | None = None,
    temporal_repository: _TemporalRepository | None = None,
    relation_repository: _RelationRepository | None = None,
) -> AcceptedNoteWriteRepositories:
    """Build a fail-fast fake repository provider for one focused test."""
    return _RepositoryProvider(
        pending_entity_repository_result=pending_entity_repository,
        observation_repository_result=observation_repository,
        section_repository_result=section_repository,
        temporal_repository_result=temporal_repository,
        relation_repository_result=relation_repository,
        note_content_repository_result=note_content_repository,
        search_repository_result=search_repository,
    )


def _prepared(
    *,
    markdown_content: str = "# Accepted\n",
    search_content: str = "Accepted",
    fields: PreparedEntityFields | None = None,
    observations: Sequence[AcceptedObservationWrite] = (),
    relations: Sequence[AcceptedRelationWrite] = (),
) -> PreparedEntityWrite:
    prepared_fields = fields or PreparedEntityFields(
        title="Accepted",
        note_type="note",
        entity_metadata={"status": "draft"},
        content_type="text/markdown",
        permalink="accepted",
        file_path="notes/accepted.md",
        created_at=_PREPARED_CREATED_AT,
        updated_at=_PREPARED_UPDATED_AT,
    )
    return PreparedEntityWrite(
        file_path=Path(prepared_fields.file_path),
        markdown_content=markdown_content,
        search_content=search_content,
        entity_fields=prepared_fields,
        entity_markdown=EntityMarkdown(
            frontmatter=EntityFrontmatter(
                metadata={
                    "title": prepared_fields.title,
                    "type": prepared_fields.note_type,
                    "permalink": prepared_fields.permalink,
                }
            ),
            content=search_content,
            observations=[
                MarkdownObservation(
                    content=observation.content,
                    category=observation.category,
                    context=observation.context,
                    tags=observation.tags,
                )
                for observation in observations
            ],
            relations=[
                MarkdownRelation(
                    type=relation.relation_type,
                    target=relation.target_name,
                    context=relation.context,
                )
                for relation in relations
            ],
        ),
    )


def _schema() -> EntitySchema:
    return EntitySchema(
        title="Accepted",
        directory="notes",
        note_type="note",
        content_type="text/markdown",
        content="# Accepted\n",
    )


def _entity() -> Entity:
    return Entity(
        id=42,
        project_id=7,
        title="Accepted",
        note_type="note",
        entity_metadata={"tags": ["core"]},
        content_type="text/markdown",
        permalink="accepted",
        file_path="notes/accepted.md",
        checksum=None,
        created_at=datetime(2026, 6, 19, 12, 0, tzinfo=UTC),
        updated_at=datetime(2026, 6, 19, 12, 5, tzinfo=UTC),
    )


def _note_content() -> NoteContent:
    return NoteContent(
        entity_id=42,
        project_id=7,
        external_id="note-1",
        file_path="notes/accepted.md",
        markdown_content="# Accepted\n",
        db_version=3,
        db_checksum="db-checksum",
        file_write_status="pending",
        last_source="api",
    )


@pytest.mark.asyncio
async def test_prepare_accepted_note_create_hashes_prepared_markdown() -> None:
    session = cast(AsyncSession, _FlushSession())
    schema = _schema()
    prepared = _prepared(markdown_content="# Created\n")
    preparer = _CreatePreparer(prepared)

    result = await prepare_accepted_note_create(
        preparer,
        schema,
        check_storage_exists=False,
        session=session,
    )

    assert result.prepared is prepared
    assert result.db_checksum == sha256(b"# Created\n").hexdigest()
    assert preparer.calls == [(schema, False, session)]
    assert preparer.skip_conflict_checks == [False]


@pytest.mark.asyncio
async def test_prepare_accepted_note_replace_applies_entity_fields() -> None:
    session = _FlushSession()
    entity = _entity()
    schema = _schema()
    fields = _PreparedFields(
        title="Replacement",
        note_type="decision",
        entity_metadata={"status": "accepted"},
        content_type="text/markdown",
        permalink="replacement",
        file_path="notes/replacement.md",
        created_at=_PREPARED_CREATED_AT,
        updated_at=_PREPARED_UPDATED_AT,
    )
    prepared = _prepared(markdown_content="# Replacement\n", fields=fields)
    preparer = _ReplacePreparer(prepared)

    result = await prepare_accepted_note_replace(
        preparer,
        cast(AsyncSession, session),
        entity=entity,
        data=schema,
        current_note_content=_note_content(),
        user_profile_value="user-2",
    )

    assert result.prepared is prepared
    assert result.db_checksum == sha256(b"# Replacement\n").hexdigest()
    assert preparer.calls == [
        (entity, schema, "# Accepted\n", cast(AsyncSession, session)),
    ]
    assert entity.title == "Replacement"
    assert entity.note_type == "decision"
    assert entity.entity_metadata == {"status": "accepted"}
    assert entity.file_path == "notes/replacement.md"
    assert entity.created_at == _PREPARED_CREATED_AT
    assert entity.updated_at == _PREPARED_UPDATED_AT
    assert entity.last_updated_by == "user-2"
    assert session.flush_count == 1


@pytest.mark.asyncio
async def test_prepare_accepted_note_edit_applies_entity_fields() -> None:
    session = _FlushSession()
    entity = _entity()
    fields = _PreparedFields(
        title="Edited",
        note_type="note",
        entity_metadata={"status": "edited"},
        content_type="text/markdown",
        permalink="edited",
        file_path="notes/edited.md",
        created_at=_PREPARED_CREATED_AT,
        updated_at=_PREPARED_UPDATED_AT,
    )
    prepared = _prepared(markdown_content="# Edited\n", fields=fields)
    preparer = _EditPreparer(prepared)

    result = await prepare_accepted_note_edit(
        preparer,
        cast(AsyncSession, session),
        entity=entity,
        current_note_content=_note_content(),
        operation="find_replace",
        content="# Edited",
        section=None,
        find_text="# Accepted",
        expected_replacements=1,
        replace_subsections=True,
        user_profile_value=None,
    )

    assert result.prepared is prepared
    assert result.db_checksum == sha256(b"# Edited\n").hexdigest()
    assert preparer.calls == [
        (
            entity,
            "# Accepted\n",
            "find_replace",
            "# Edited",
            None,
            "# Accepted",
            1,
            True,
            None,
            cast(AsyncSession, session),
        )
    ]
    assert entity.title == "Edited"
    assert entity.permalink == "edited"
    assert entity.file_path == "notes/edited.md"
    assert entity.created_at == _PREPARED_CREATED_AT
    assert entity.updated_at == _PREPARED_UPDATED_AT
    assert entity.last_updated_by is None
    assert session.flush_count == 1


@pytest.mark.asyncio
async def test_prepare_accepted_note_edit_threads_metadata_to_preparer() -> None:
    """`metadata` must reach the preparer so frontmatter merges apply independent of `operation`."""
    session = _FlushSession()
    entity = _entity()
    fields = _PreparedFields(
        title="Edited",
        note_type="note",
        entity_metadata={"status": "resolved"},
        content_type="text/markdown",
        permalink="edited",
        file_path="notes/edited.md",
        created_at=_PREPARED_CREATED_AT,
        updated_at=_PREPARED_UPDATED_AT,
    )
    prepared = _prepared(markdown_content="# Edited\n", fields=fields)
    preparer = _EditPreparer(prepared)

    await prepare_accepted_note_edit(
        preparer,
        cast(AsyncSession, session),
        entity=entity,
        current_note_content=_note_content(),
        operation="find_replace",
        content="# Edited",
        section=None,
        find_text="# Accepted",
        expected_replacements=1,
        replace_subsections=True,
        metadata={"status": "resolved"},
        user_profile_value=None,
    )

    assert preparer.calls == [
        (
            entity,
            "# Accepted\n",
            "find_replace",
            "# Edited",
            None,
            "# Accepted",
            1,
            True,
            {"status": "resolved"},
            cast(AsyncSession, session),
        )
    ]


def test_apply_accepted_prepared_entity_fields_updates_mutable_entity() -> None:
    entity = _entity()

    apply_accepted_prepared_entity_fields(
        entity,
        _PreparedFields(
            title="Applied",
            note_type="schema",
            entity_metadata={"type": "schema"},
            content_type="text/markdown",
            permalink="applied",
            file_path="schemas/applied.md",
            created_at=_PREPARED_CREATED_AT,
            updated_at=_PREPARED_UPDATED_AT,
        ),
        user_profile_value="user-3",
    )

    assert entity.title == "Applied"
    assert entity.note_type == "schema"
    assert entity.entity_metadata == {"type": "schema"}
    assert entity.content_type == "text/markdown"
    assert entity.permalink == "applied"
    assert entity.file_path == "schemas/applied.md"
    assert entity.created_at == _PREPARED_CREATED_AT
    assert entity.updated_at == _PREPARED_UPDATED_AT
    assert entity.last_updated_by == "user-3"


@pytest.mark.asyncio
async def test_prepare_accepted_note_move_without_permalink_update_keeps_current_markdown() -> None:
    session = _FlushSession()
    entity = _entity()
    original_created_at = entity.created_at
    original_updated_at = entity.updated_at
    current = _note_content()
    current.markdown_content = "---\ntitle: legacy\n\n# Body still matters\n"

    prepared = _PreparedMove(
        file_path=Path("archive/accepted.md"),
        markdown_content=str(current.markdown_content),
        search_content=str(current.markdown_content),
        permalink="accepted",
    )
    preparer = _MovePreparer(prepared)

    result = await prepare_accepted_note_move(
        preparer,
        cast(AsyncSession, session),
        entity=entity,
        current_note_content=current,
        accepted_file_path="archive/accepted.md",
        should_update_permalink=False,
        user_profile_value="user-4",
    )

    assert result.file_path == "archive/accepted.md"
    assert result.markdown_content == current.markdown_content
    assert result.search_content == current.markdown_content
    assert result.permalink == "accepted"
    assert result.db_checksum == sha256(str(current.markdown_content).encode()).hexdigest()
    assert entity.file_path == "archive/accepted.md"
    assert entity.permalink == "accepted"
    assert entity.created_at == original_created_at
    assert entity.updated_at == original_updated_at
    assert entity.last_updated_by == "user-4"
    assert session.flush_count == 1
    assert preparer.calls == [
        (
            entity,
            str(current.markdown_content),
            "archive/accepted.md",
            False,
            cast(AsyncSession, session),
        )
    ]


@pytest.mark.asyncio
async def test_prepare_accepted_note_move_with_permalink_update_uses_preparer() -> None:
    session = _FlushSession()
    entity = _entity()
    original_created_at = entity.created_at
    original_updated_at = entity.updated_at
    prepared = _PreparedMove(
        file_path=Path("archive/prepared.md"),
        markdown_content="# Prepared\n",
        search_content="Prepared",
        permalink="archive/prepared",
    )
    preparer = _MovePreparer(prepared)

    result = await prepare_accepted_note_move(
        preparer,
        cast(AsyncSession, session),
        entity=entity,
        current_note_content=_note_content(),
        accepted_file_path="archive/accepted.md",
        should_update_permalink=True,
        user_profile_value=None,
    )

    assert preparer.calls == [
        (entity, "# Accepted\n", "archive/accepted.md", True, cast(AsyncSession, session)),
    ]
    assert result.file_path == "archive/prepared.md"
    assert result.markdown_content == "# Prepared\n"
    assert result.search_content == "Prepared"
    assert result.permalink == "archive/prepared"
    assert result.db_checksum == sha256(b"# Prepared\n").hexdigest()
    assert entity.file_path == "archive/prepared.md"
    assert entity.permalink == "archive/prepared"
    assert entity.created_at == original_created_at
    assert entity.updated_at == original_updated_at
    assert entity.last_updated_by is None
    assert session.flush_count == 1


def test_accepted_pending_entity_write_from_prepared_maps_core_fields() -> None:
    write = accepted_pending_entity_write_from_prepared(
        _prepared(),
        user_profile_value="user-1",
        external_id="note-1",
    )

    assert write == AcceptedPendingEntityWrite(
        title="Accepted",
        note_type="note",
        entity_metadata={"status": "draft"},
        content_type="text/markdown",
        permalink="accepted",
        file_path="notes/accepted.md",
        created_at=_PREPARED_CREATED_AT,
        updated_at=_PREPARED_UPDATED_AT,
        created_by="user-1",
        last_updated_by="user-1",
        external_id="note-1",
    )


@pytest.mark.asyncio
async def test_create_accepted_pending_entity_uses_repository_protocol() -> None:
    session = cast(AsyncSession, object())
    entity = _entity()
    repository = _PendingEntityRepository(entity)

    result = await create_accepted_pending_entity(
        session,
        prepared=_prepared(),
        project_id=7,
        user_profile_value=None,
        repositories=_repository_provider(pending_entity_repository=repository),
    )

    assert result is entity
    assert len(repository.calls) == 1
    repository_session, write = repository.calls[0]
    assert repository_session is session
    assert write.file_path == "notes/accepted.md"
    assert write.created_by is None


def test_accepted_note_content_write_from_markdown_maps_versioned_snapshot() -> None:
    updated_at = datetime(2026, 6, 19, 12, 5, tzinfo=UTC)

    write = accepted_note_content_write_from_markdown(
        entity_id=42,
        markdown_content="# Accepted\n",
        db_version=3,
        db_checksum="db-checksum",
        last_source="mcp",
        updated_at=updated_at,
    )

    assert write == AcceptedNoteContentWrite(
        entity_id=42,
        markdown_content="# Accepted\n",
        db_version=3,
        db_checksum="db-checksum",
        last_source="mcp",
        updated_at=updated_at,
    )


@pytest.mark.asyncio
async def test_accept_note_content_write_uses_repository_protocol() -> None:
    session = cast(AsyncSession, object())
    entity = _entity()
    note_content = _note_content()
    repository = _NoteContentRepository(note_content)
    updated_at = datetime(2026, 6, 19, 12, 5, tzinfo=UTC)

    result = await accept_note_content_write(
        session,
        entity=entity,
        markdown_content="# Accepted\n",
        db_version=3,
        db_checksum="db-checksum",
        last_source="api",
        updated_at=updated_at,
        repositories=_repository_provider(note_content_repository=repository),
    )

    assert result is note_content
    assert repository.calls == [
        (
            session,
            AcceptedNoteContentWrite(
                entity_id=42,
                markdown_content="# Accepted\n",
                db_version=3,
                db_checksum="db-checksum",
                last_source="api",
                updated_at=updated_at,
            ),
        )
    ]


def test_accepted_note_search_row_from_entity_builds_hot_search_row() -> None:
    entity = _entity()

    row = accepted_note_search_row_from_entity(entity, search_content="Accepted body")

    assert row.entity_id == 42
    assert row.project_id == 7
    assert row.title == "Accepted"
    assert row.file_path == "notes/accepted.md"
    assert row.content_snippet == "Accepted body"
    assert "core" in row.content_stems


@pytest.mark.asyncio
async def test_refresh_accepted_note_search_index_uses_repository_protocol() -> None:
    session = cast(AsyncSession, object())
    entity = _entity()
    repository = _SearchRepository()

    await refresh_accepted_note_search_index(
        session,
        entity=entity,
        search_content="Accepted body",
        repositories=_repository_provider(search_repository=repository),
    )

    assert len(repository.calls) == 1
    row = repository.calls[0]
    assert row.entity_id == 42
    assert row.project_id == 7


@pytest.mark.asyncio
async def test_delete_accepted_note_search_index_uses_repository_protocol() -> None:
    session = cast(AsyncSession, object())
    repository = _SearchRepository()

    await delete_accepted_note_search_index(
        session,
        project_id=7,
        entity_id=42,
        repositories=_repository_provider(search_repository=repository),
    )

    assert repository.deleted_entity_ids == [42]


@pytest.mark.asyncio
async def test_persist_accepted_note_snapshot_emits_relation_generation() -> None:
    session = cast(AsyncSession, object())
    entity = _entity()
    entity.file_path = "notes/new.md"
    updated_at = datetime(2026, 6, 19, 14, 0, tzinfo=UTC)
    current_note_content = _note_content()
    current_note_content.file_path = "notes/old.md"
    current_note_content.db_version = 4
    current_note_content.file_version = 3
    current_note_content.file_checksum = "old-file-checksum"
    persisted_note_content = _note_content()
    persisted_note_content.db_version = 5
    content_repository = _NoteContentRepository(persisted_note_content)
    search_repository = _SearchRepository()
    observation_repository = _ObservationRepository()
    relation_repository = _RelationRepository()
    observation = AcceptedObservationWrite(
        content="Snapshot is complete",
        category="status",
        context=None,
        tags=None,
    )
    relation = AcceptedRelationWrite(
        relation_type="documents",
        target_name="Another Note",
        context=None,
    )
    prepared = _prepared(
        markdown_content="# New\n",
        search_content="New body",
        observations=(observation,),
        relations=(relation,),
    )
    self_relation_resolver = _SelfRelationResolver()

    result = await persist_accepted_note_snapshot(
        session,
        entity=entity,
        prepared=prepared,
        db_checksum="new-db-checksum",
        last_source="api",
        updated_at=updated_at,
        current_note_content=current_note_content,
        existing_file_path="notes/old.md",
        accepted_file_path="notes/new.md",
        source_file_checksum="db-checksum",
        self_relation_resolver=self_relation_resolver,
        repositories=_repository_provider(
            note_content_repository=content_repository,
            search_repository=search_repository,
            observation_repository=observation_repository,
        ),
    )

    assert result.note_content is persisted_note_content
    assert result.previous_file_delete is not None
    assert result.previous_file_delete.project_id == entity.project_id
    assert result.previous_file_delete.entity_id == entity.id
    assert result.previous_file_delete.file_path == "notes/old.md"
    # The accepted DB version is ahead of the published file version, so the old file checksum is
    # stale. Cleanup follows the accepted checksum that may already have reached storage.
    assert result.previous_file_delete.file_checksum == "db-checksum"
    assert content_repository.calls == [
        (
            session,
            AcceptedNoteContentWrite(
                entity_id=42,
                markdown_content="# New\n",
                db_version=5,
                db_checksum="new-db-checksum",
                last_source="api",
                updated_at=updated_at,
            ),
        )
    ]
    assert len(search_repository.calls) == 1
    assert search_repository.calls[0].entity_id == entity.id
    assert search_repository.calls[0].content_snippet == "New body"
    assert observation_repository.calls == []
    assert relation_repository.calls == []
    assert result.relation_publication is not None
    assert result.relation_publication.project_id == entity.project_id
    assert result.relation_publication.entity_id == entity.id
    assert result.relation_publication.generation == 5
    assert result.relation_publication.observations[0].content == "Snapshot is complete"
    assert result.relation_publication.observations[0].category == "status"
    assert result.relation_publication.relations[0].relation_type == "documents"
    assert result.relation_publication.relations[0].target_name == "Another Note"
    assert result.relation_publication.relations[0].target_id is None
    assert self_relation_resolver.calls == [("Another Note", entity, session)]


@pytest.mark.asyncio
async def test_persist_accepted_note_move_emits_relation_generation() -> None:
    session = cast(AsyncSession, _FlushSession())
    entity = _entity()
    entity.file_path = "notes/new.md"
    current_note_content = _note_content()
    current_note_content.file_path = "notes/old.md"
    content_repository = _NoteContentRepository(_note_content())
    search_repository = _SearchRepository()
    move_preparer = _MovePreparer(
        _PreparedMove(
            file_path=Path("notes/new.md"),
            markdown_content=str(current_note_content.markdown_content),
            search_content=str(current_note_content.markdown_content),
            permalink=entity.permalink,
            observations=(
                AcceptedObservationWrite(
                    content="Move keeps this",
                    category="fact",
                    context=None,
                    tags=["move"],
                ),
            ),
        )
    )
    prepared = await prepare_accepted_note_move(
        move_preparer,
        session,
        entity=entity,
        current_note_content=current_note_content,
        accepted_file_path="notes/new.md",
        should_update_permalink=False,
        user_profile_value=None,
    )

    result = await persist_accepted_note_move(
        session,
        entity=entity,
        prepared=prepared,
        last_source="api",
        updated_at=datetime(2026, 6, 19, 14, 0, tzinfo=UTC),
        current_note_content=current_note_content,
        existing_file_path="notes/old.md",
        self_relation_resolver=_SelfRelationResolver(),
        repositories=_repository_provider(
            note_content_repository=content_repository,
            search_repository=search_repository,
        ),
    )

    assert len(content_repository.calls) == 1
    assert len(search_repository.calls) == 1
    assert result.relation_publication is not None
    assert result.relation_publication.generation == result.note_content.db_version
    assert result.relation_publication.observations[0].content == "Move keeps this"
    assert result.relation_publication.observations[0].tags == ["move"]
    assert result.relation_publication.relations == ()


@pytest.mark.asyncio
async def test_delete_accepted_note_entity_deletes_via_session() -> None:
    session = _DeleteSession()
    entity = _entity()

    await delete_accepted_note_entity(cast(AsyncSession, session), entity=entity)

    assert session.deleted == [entity]


@pytest.mark.asyncio
async def test_delete_accepted_note_plans_missing_response_without_deleting() -> None:
    session = _DeleteSession()

    # The fail-fast provider proves a missing entity touches no repository.
    accepted = await delete_accepted_note(
        cast(AsyncSession, session),
        project_id=7,
        entity=None,
        repositories=_repository_provider(),
    )

    assert session.deleted == []
    assert session.execute_count == 0, "a missing entity has no relation sources to capture"
    assert accepted.status_code == 200
    assert accepted.payload == {"deleted": False}
    assert accepted.file_delete is None
    assert accepted.relation_cleanup_entity_ids == frozenset()


@pytest.mark.asyncio
async def test_delete_accepted_note_plans_cleanup_and_deletes_entity() -> None:
    events: list[tuple[str, int]] = []
    # Two surviving notes still link to the deleted target; their ids must ride
    # the accepted change so the runtime can refresh their search rows (#1351).
    session = _DeleteSession(events, relation_source_ids=(11, 5))
    entity = _entity()
    entity.external_id = "entity-42"
    entity.checksum = "entity-file-checksum"
    note_content = _note_content()
    note_content.file_checksum = "note-file-checksum"
    search_repository = _SearchRepository(events)

    accepted = await delete_accepted_note(
        cast(AsyncSession, session),
        project_id=entity.project_id,
        entity=entity,
        note_content=note_content,
        repositories=_repository_provider(search_repository=search_repository),
    )

    assert search_repository.deleted_entity_ids == [entity.id]
    assert search_repository.deleted_vector_entity_ids == [entity.id]
    assert session.scalar_count == 1
    assert session.deleted == [entity]
    assert accepted.relation_cleanup_entity_ids == frozenset({5, 11})
    # The capture must precede the entity delete: the SET NULL cascade erases the
    # to_id evidence that identifies which sources need search repair.
    assert session.execute_count_at_entity_delete == 1
    assert events == [
        ("search", entity.id),
        ("vectors", entity.id),
        ("entity", entity.id),
    ]
    assert accepted.status_code == 200
    assert accepted.payload == {
        "deleted": True,
        "external_id": "entity-42",
        "title": "Accepted",
        "permalink": "accepted",
        "file_path": "notes/accepted.md",
        "file_delete_status": "pending",
    }
    assert accepted.file_delete is not None
    assert accepted.file_delete.project_id == entity.project_id
    assert accepted.file_delete.entity_id == entity.id
    assert accepted.file_delete.file_path == entity.file_path
    assert accepted.file_delete.file_checksum == "note-file-checksum"


@pytest.mark.asyncio
async def test_persist_accepted_note_snapshot_pre_resolves_unambiguous_self_relation() -> None:
    """Accepted publication keeps the authored alias and safe self target together."""
    entity = _entity()
    prepared = _prepared(
        markdown_content="# Accepted\n",
        search_content="Accepted",
        fields=_PreparedFields(
            title="Accepted",
            note_type="note",
            entity_metadata=None,
            content_type="text/markdown",
            permalink="accepted",
            file_path="notes/accepted.md",
            created_at=_PREPARED_CREATED_AT,
            updated_at=_PREPARED_UPDATED_AT,
        ),
        relations=[
            AcceptedRelationWrite(
                relation_type="documents",
                target_name="notes/accepted",
                context=None,
            )
        ],
    )
    session = cast(AsyncSession, object())
    resolver = _SelfRelationResolver(resolved_names={"notes/accepted"})
    result = await persist_accepted_note_snapshot(
        session,
        entity=entity,
        prepared=prepared,
        db_checksum="snapshot-checksum",
        last_source="api",
        updated_at=entity.updated_at,
        self_relation_resolver=resolver,
        repositories=_repository_provider(
            note_content_repository=_NoteContentRepository(_note_content()),
            search_repository=_SearchRepository(),
            observation_repository=_ObservationRepository(),
        ),
    )

    assert result.relation_publication is not None
    assert result.relation_publication.relations[0].target_name == "notes/accepted"
    assert result.relation_publication.relations[0].target_id == entity.id
    assert resolver.calls == [("notes/accepted", entity, session)]


@pytest.mark.asyncio
async def test_persist_accepted_note_snapshot_emits_empty_relation_generation() -> None:
    """An empty relation set still emits a publication so cleanup can remove stale rows."""
    observation_repository = _ObservationRepository()
    relation_repository = _RelationRepository()
    repositories = _repository_provider(
        note_content_repository=_NoteContentRepository(_note_content()),
        search_repository=_SearchRepository(),
        observation_repository=observation_repository,
    )
    prepared = _prepared()

    entity = _entity()
    result = await persist_accepted_note_snapshot(
        cast(AsyncSession, object()),
        entity=entity,
        prepared=prepared,
        db_checksum="snapshot-checksum",
        last_source="api",
        updated_at=entity.updated_at,
        self_relation_resolver=_SelfRelationResolver(),
        repositories=repositories,
    )

    assert observation_repository.calls == []
    assert relation_repository.calls == []
    assert result.relation_publication is not None
    assert result.relation_publication.observations == ()
    assert result.relation_publication.relations == ()
