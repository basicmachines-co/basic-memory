"""Condensed accepted-note persistence flow based on Basic Memory issue #1097."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class Entity:
    id: int
    project_id: int


@dataclass(frozen=True, slots=True)
class PreparedNoteSnapshot:
    markdown: str
    search_text: str
    observations: tuple[str, ...]
    relations: tuple[str, ...]


class AcceptedNoteRepositories(Protocol):
    async def accept_content(
        self,
        session: object,
        entity: Entity,
        markdown: str,
    ) -> None: ...

    async def refresh_search(
        self,
        session: object,
        entity: Entity,
        search_text: str,
    ) -> None: ...

    async def replace_observations(
        self,
        session: object,
        entity: Entity,
        observations: tuple[str, ...],
    ) -> None: ...

    async def replace_relations(
        self,
        session: object,
        entity: Entity,
        relations: tuple[str, ...],
    ) -> None: ...


async def persist_accepted_note_write(
    session: object,
    *,
    entity: Entity,
    prepared: PreparedNoteSnapshot,
    repositories: AcceptedNoteRepositories,
) -> None:
    """Persist accepted content and hot search state in the caller's transaction."""
    await repositories.accept_content(session, entity, prepared.markdown)
    await repositories.refresh_search(session, entity, prepared.search_text)


async def replace_accepted_note_graph(
    session: object,
    *,
    entity: Entity,
    prepared: PreparedNoteSnapshot,
    repositories: AcceptedNoteRepositories,
) -> None:
    """Replace observations and outgoing relations for parsed accepted Markdown."""
    await repositories.replace_observations(session, entity, prepared.observations)
    await repositories.replace_relations(session, entity, prepared.relations)


async def create_note(
    session: object,
    *,
    entity: Entity,
    prepared: PreparedNoteSnapshot,
    repositories: AcceptedNoteRepositories,
) -> None:
    await persist_accepted_note_write(
        session,
        entity=entity,
        prepared=prepared,
        repositories=repositories,
    )
    await replace_accepted_note_graph(
        session,
        entity=entity,
        prepared=prepared,
        repositories=repositories,
    )


async def edit_note(
    session: object,
    *,
    entity: Entity,
    prepared: PreparedNoteSnapshot,
    repositories: AcceptedNoteRepositories,
) -> None:
    await persist_accepted_note_write(
        session,
        entity=entity,
        prepared=prepared,
        repositories=repositories,
    )
    await replace_accepted_note_graph(
        session,
        entity=entity,
        prepared=prepared,
        repositories=repositories,
    )


async def move_note(
    session: object,
    *,
    entity: Entity,
    prepared: PreparedNoteSnapshot,
    repositories: AcceptedNoteRepositories,
) -> None:
    """A move changes content/search paths but preserves the parsed note graph."""
    await persist_accepted_note_write(
        session,
        entity=entity,
        prepared=prepared,
        repositories=repositories,
    )
