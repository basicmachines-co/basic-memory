import asyncio
from dataclasses import dataclass, field

from accepted_snapshot import Entity, PreparedNoteSnapshot, create_note, move_note


@dataclass
class RecordingRepositories:
    operations: list[str] = field(default_factory=list)

    async def accept_content(
        self,
        session: object,
        entity: Entity,
        markdown: str,
    ) -> None:
        self.operations.append(f"content:{entity.id}:{markdown}")

    async def refresh_search(
        self,
        session: object,
        entity: Entity,
        search_text: str,
    ) -> None:
        self.operations.append(f"search:{entity.id}:{search_text}")

    async def replace_observations(
        self,
        session: object,
        entity: Entity,
        observations: tuple[str, ...],
    ) -> None:
        self.operations.append(f"observations:{entity.id}:{','.join(observations)}")

    async def replace_relations(
        self,
        session: object,
        entity: Entity,
        relations: tuple[str, ...],
    ) -> None:
        self.operations.append(f"relations:{entity.id}:{','.join(relations)}")


def prepared_note() -> PreparedNoteSnapshot:
    return PreparedNoteSnapshot(
        markdown="# Note\n",
        search_text="Note",
        observations=("fact",),
        relations=("links-to:target",),
    )


def test_create_persists_the_complete_snapshot() -> None:
    repositories = RecordingRepositories()

    asyncio.run(
        create_note(
            object(),
            entity=Entity(id=7, project_id=3),
            prepared=prepared_note(),
            repositories=repositories,
        )
    )

    assert [operation.split(":", 1)[0] for operation in repositories.operations] == [
        "content",
        "search",
        "observations",
        "relations",
    ]


def test_move_preserves_the_existing_graph() -> None:
    repositories = RecordingRepositories()

    asyncio.run(
        move_note(
            object(),
            entity=Entity(id=7, project_id=3),
            prepared=prepared_note(),
            repositories=repositories,
        )
    )

    assert [operation.split(":", 1)[0] for operation in repositories.operations] == [
        "content",
        "search",
    ]
