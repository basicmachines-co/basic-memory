"""Accepted note mutations against a real database and the production mutation dependencies.

Every test drives a runner entry point (create, update, edit, move, delete) through the same
dependency graph the local API builds, inside the same transaction boundary the note routes
use, and asserts the state that lands in the database plus the follow-up work the runner hands
back. Seeding writes rows or files directly to put a note in a known materialization state;
the only monkeypatches inject a race the test is reproducing, and each says so.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy import update as sql_update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

import basic_memory.indexing.accepted_note_mutation_runner as accepted_note_mutation_runner
from basic_memory import db, file_utils
from basic_memory.config import BasicMemoryConfig
from basic_memory.index.local_notes import LocalAcceptedNotePreparerFactory
from basic_memory.indexing.accepted_note_mutation_runner import (
    AcceptedNoteBaseChecksumConflict,
    AcceptedNoteCreateMutation,
    AcceptedNoteDeleteMutation,
    AcceptedNoteEditMutation,
    AcceptedNoteMutationActor,
    AcceptedNoteMutationDependencies,
    AcceptedNoteMutationMovePolicy,
    AcceptedNoteMutationRejectKind,
    AcceptedNoteMutationRejected,
    AcceptedNoteMoveMutation,
    AcceptedNoteMutationResult,
    AcceptedNoteUpdateMutation,
    run_accepted_note_create,
    run_accepted_note_delete,
    run_accepted_note_edit,
    run_accepted_note_move,
    run_accepted_note_update,
)
from basic_memory.models import (
    AcceptedProjectNoteChange,
    Entity,
    NoteContent,
    NoteFileVacate,
    Project,
    Relation,
    RelationSearchRefresh,
)
from basic_memory.repository import ProjectRepository
from basic_memory.repository.accepted_note_repositories import AcceptedNoteRepositories
from basic_memory.repository.entity_repository import EntityRepository
from basic_memory.repository.search_repository import create_search_repository
from basic_memory.runtime.note_content import RuntimeAcceptedNoteResponse
from basic_memory.runtime.project_partition import RuntimeProjectNoteOperation
from basic_memory.runtime.storage import RuntimeNoteChangeSource
from basic_memory.schemas.base import Entity as EntitySchema
from basic_memory.schemas.request import EditEntityRequest
from basic_memory.services.note_content_writes import accepted_note_transaction

type SessionMaker = async_sessionmaker[AsyncSession]

ACTOR_ID = uuid4()
ANONYMOUS = AcceptedNoteMutationActor(user_profile_id=None)


@pytest.fixture
def session_maker(engine_factory: tuple[AsyncEngine, SessionMaker]) -> SessionMaker:
    return engine_factory[1]


@pytest.fixture
def dependencies(
    session_maker: SessionMaker,
    app_config: BasicMemoryConfig,
) -> AcceptedNoteMutationDependencies:
    """The same dependency graph the local API builds for its note routes."""
    repositories = AcceptedNoteRepositories(
        external_vector_cleaner_factory=lambda project_id: create_search_repository(
            session_maker=session_maker,
            project_id=project_id,
            app_config=app_config,
        )
    )
    return AcceptedNoteMutationDependencies(
        project_repository=ProjectRepository(),
        lookup_repositories=repositories,
        preparer_factory=LocalAcceptedNotePreparerFactory(
            session_maker=session_maker,
            app_config=app_config,
        ),
        write_repositories=repositories,
        move_policy=AcceptedNoteMutationMovePolicy(
            update_permalinks_on_move=app_config.update_permalinks_on_move,
        ),
        verify_storage_absent_on_create=True,
    )


# --- Mutation Helpers ---


async def create(
    session_maker: SessionMaker,
    dependencies: AcceptedNoteMutationDependencies,
    project: Project,
    *,
    title: str = "Accepted",
    directory: str = "notes",
    content: str = "# Accepted\n",
    publish_graph_facts: bool = True,
) -> AcceptedNoteMutationResult:
    async with accepted_note_transaction(session_maker) as session:
        return await run_accepted_note_create(
            session,
            request=AcceptedNoteCreateMutation(
                project_external_id=project.external_id,
                data=EntitySchema(title=title, directory=directory, content=content),
                actor=AcceptedNoteMutationActor(user_profile_id=ACTOR_ID, kind="user", name="Ada"),
                source="api",
                publish_graph_facts=publish_graph_facts,
            ),
            dependencies=dependencies,
        )


async def update(
    session_maker: SessionMaker,
    dependencies: AcceptedNoteMutationDependencies,
    project: Project,
    entity_external_id: str,
    *,
    title: str = "Accepted",
    directory: str = "notes",
    content: str = "# Accepted\n\nReplaced body\n",
    source: RuntimeNoteChangeSource = "api",
    actor: AcceptedNoteMutationActor | None = None,
    base_checksum: str | None = None,
    publish_graph_facts: bool = True,
) -> AcceptedNoteMutationResult:
    async with accepted_note_transaction(session_maker) as session:
        return await run_accepted_note_update(
            session,
            request=AcceptedNoteUpdateMutation(
                project_external_id=project.external_id,
                entity_external_id=entity_external_id,
                data=EntitySchema(title=title, directory=directory, content=content),
                actor=actor or AcceptedNoteMutationActor(user_profile_id=ACTOR_ID),
                source=source,
                base_checksum=base_checksum,
                publish_graph_facts=publish_graph_facts,
            ),
            dependencies=dependencies,
        )


async def edit(
    session_maker: SessionMaker,
    dependencies: AcceptedNoteMutationDependencies,
    project: Project,
    entity_external_id: str,
    data: EditEntityRequest,
) -> AcceptedNoteMutationResult:
    async with accepted_note_transaction(session_maker) as session:
        return await run_accepted_note_edit(
            session,
            request=AcceptedNoteEditMutation(
                project_external_id=project.external_id,
                entity_external_id=entity_external_id,
                data=data,
                actor=ANONYMOUS,
                source="mcp",
            ),
            dependencies=dependencies,
        )


async def move(
    session_maker: SessionMaker,
    dependencies: AcceptedNoteMutationDependencies,
    project: Project,
    entity_external_id: str,
    destination_path: str,
    *,
    actor: AcceptedNoteMutationActor = ANONYMOUS,
    source: RuntimeNoteChangeSource = "mcp",
) -> AcceptedNoteMutationResult:
    async with accepted_note_transaction(session_maker) as session:
        return await run_accepted_note_move(
            session,
            request=AcceptedNoteMoveMutation(
                project_external_id=project.external_id,
                entity_external_id=entity_external_id,
                destination_path=destination_path,
                actor=actor,
                source=source,
            ),
            dependencies=dependencies,
        )


async def delete_note(
    session_maker: SessionMaker,
    dependencies: AcceptedNoteMutationDependencies,
    project: Project,
    entity_external_id: str,
    *,
    actor: AcceptedNoteMutationActor | None = None,
) -> AcceptedNoteMutationResult:
    async with accepted_note_transaction(session_maker) as session:
        return await run_accepted_note_delete(
            session,
            request=AcceptedNoteDeleteMutation(
                project_external_id=project.external_id,
                entity_external_id=entity_external_id,
                actor=actor,
            ),
            dependencies=dependencies,
        )


# --- Seeding and Reading State ---


@dataclass(frozen=True, slots=True)
class AcceptedNote:
    """Identity and accepted revision of a note a test created through the runner."""

    external_id: str
    entity_id: int
    db_checksum: str
    file_path: str


async def accepted_note(
    session_maker: SessionMaker,
    dependencies: AcceptedNoteMutationDependencies,
    project: Project,
    *,
    title: str = "Accepted",
    directory: str = "notes",
    content: str = "# Accepted\n",
) -> AcceptedNote:
    result = await create(
        session_maker,
        dependencies,
        project,
        title=title,
        directory=directory,
        content=content,
    )
    payload = result.change.payload
    assert isinstance(payload, RuntimeAcceptedNoteResponse)
    return AcceptedNote(
        external_id=payload.external_id,
        entity_id=payload.entity_id,
        db_checksum=payload.db_checksum,
        file_path=payload.file_path,
    )


async def add_entity(
    session_maker: SessionMaker,
    project: Project,
    *,
    file_path: str,
    title: str,
    content_type: str = "text/markdown",
) -> str:
    """Seed an indexed row the mutation must account for; return its external id."""
    now = datetime.now(UTC)
    external_id = str(uuid4())
    async with db.scoped_session(session_maker) as session:
        session.add(
            Entity(
                project_id=project.id,
                external_id=external_id,
                title=title,
                note_type="note",
                content_type=content_type,
                file_path=file_path,
                permalink=file_path.rsplit(".", 1)[0].lower(),
                created_at=now,
                updated_at=now,
            )
        )
    return external_id


async def load_note_content(session_maker: SessionMaker, entity_id: int) -> NoteContent | None:
    async with db.scoped_session(session_maker) as session:
        return await session.scalar(select(NoteContent).where(NoteContent.entity_id == entity_id))


async def required_note_content(session_maker: SessionMaker, entity_id: int) -> NoteContent:
    note_content = await load_note_content(session_maker, entity_id)
    assert note_content is not None
    return note_content


async def load_entity(session_maker: SessionMaker, external_id: str) -> Entity | None:
    async with db.scoped_session(session_maker) as session:
        return await session.scalar(select(Entity).where(Entity.external_id == external_id))


async def seed_note_content(session_maker: SessionMaker, entity_id: int, **values: object) -> None:
    """Set note_content columns the way materialization or another writer would record them."""
    async with db.scoped_session(session_maker) as session:
        await session.execute(
            sql_update(NoteContent).where(NoteContent.entity_id == entity_id).values(**values)
        )


def write_project_file(project: Project, file_path: str, content: str) -> None:
    path = Path(project.path) / file_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


async def materialize(session_maker: SessionMaker, project: Project, entity_id: int) -> str:
    """Write the accepted Markdown to disk and record it as synced; return the file checksum."""
    note_content = await required_note_content(session_maker, entity_id)
    write_project_file(project, note_content.file_path, note_content.markdown_content)
    file_checksum = await file_utils.compute_checksum(note_content.markdown_content)
    await seed_note_content(
        session_maker,
        entity_id,
        file_version=note_content.db_version,
        file_checksum=file_checksum,
        file_write_status="synced",
    )
    return file_checksum


async def pending_generations(session_maker: SessionMaker, entity_id: int) -> list[int | None]:
    async with db.scoped_session(session_maker) as session:
        return list(
            await session.scalars(
                select(RelationSearchRefresh.publication_generation)
                .where(RelationSearchRefresh.entity_id == entity_id)
                .order_by(RelationSearchRefresh.id)
            )
        )


async def vacated_paths(
    session_maker: SessionMaker, project: Project
) -> list[tuple[str, str | None]]:
    async with db.scoped_session(session_maker) as session:
        rows = await session.execute(
            select(NoteFileVacate.file_path, NoteFileVacate.file_checksum).where(
                NoteFileVacate.project_id == project.id
            )
        )
        return [(file_path, file_checksum) for file_path, file_checksum in rows.tuples()]


async def search_row_count(session_maker: SessionMaker, entity_id: int) -> int:
    async with db.scoped_session(session_maker) as session:
        count = await session.scalar(
            text("SELECT count(*) FROM search_index WHERE entity_id = :entity_id"),
            {"entity_id": entity_id},
        )
        return int(count or 0)


async def project_change_rows(
    session_maker: SessionMaker, entity_id: int
) -> list[AcceptedProjectNoteChange]:
    async with db.scoped_session(session_maker) as session:
        return list(
            await session.scalars(
                select(AcceptedProjectNoteChange)
                .where(AcceptedProjectNoteChange.entity_id == entity_id)
                .order_by(AcceptedProjectNoteChange.partition_position)
            )
        )


type NoteLock = Callable[..., Awaitable[None]]


def inject_before_note_lock(
    monkeypatch: pytest.MonkeyPatch,
    concurrent_commit: Callable[[AsyncSession], Awaitable[None]],
) -> None:
    """Injects a race: another writer commits while this mutation waits on the note lock.

    The runner loads the note identity, then claims the note_content lock. Two real
    transactions cannot interleave deterministically on SQLite, so the concurrent writer's
    effect is applied in this transaction just before the real lock is taken.
    """
    real_lock: NoteLock = (
        accepted_note_mutation_runner.lock_accepted_note_content_for_entity_mutation
    )

    async def lock_after_concurrent_commit(
        session: AsyncSession, *, project_id: int, entity_id: int
    ) -> None:
        await concurrent_commit(session)
        await real_lock(session, project_id=project_id, entity_id=entity_id)

    monkeypatch.setattr(
        accepted_note_mutation_runner,
        "lock_accepted_note_content_for_entity_mutation",
        lock_after_concurrent_commit,
    )


def concurrent_move(entity_id: int, moved_to: str) -> Callable[[AsyncSession], Awaitable[None]]:
    async def apply(session: AsyncSession) -> None:
        # synchronize_session=False leaves the identity-mapped Entity stale, exactly as a
        # commit from another connection would; only the runner's refresh can observe it.
        for model in (Entity, NoteContent):
            id_column = Entity.id if model is Entity else NoteContent.entity_id
            await session.execute(
                sql_update(model)
                .where(id_column == entity_id)
                .values(file_path=moved_to)
                .execution_options(synchronize_session=False)
            )

    return apply


# --- Create ---


async def test_create_persists_the_accepted_note_and_its_project_change(
    session_maker: SessionMaker,
    dependencies: AcceptedNoteMutationDependencies,
    test_project: Project,
) -> None:
    result = await create(session_maker, dependencies, test_project)

    change = result.change
    assert change.status_code == 201
    assert isinstance(change.payload, RuntimeAcceptedNoteResponse)
    assert change.payload.file_path == "notes/Accepted.md"
    assert change.materialization is not None
    assert (
        change.materialization.actor_user_profile_id,
        change.materialization.actor_kind,
        change.materialization.actor_name,
        change.materialization.previous_file_path,
    ) == (ACTOR_ID, "user", "Ada", None)
    project_change = change.project_change
    assert project_change is not None
    assert project_change.operation is RuntimeProjectNoteOperation.created
    assert (project_change.partition_position, project_change.db_version) == (1, 1)
    assert project_change.file_path == "notes/Accepted.md"
    assert project_change.previous_file_path is None
    assert project_change.source == "api"
    assert change.materialization.project_change is project_change

    async with db.scoped_session(session_maker) as session:
        entity = await session.scalar(
            select(Entity).where(Entity.external_id == change.payload.external_id)
        )
        assert entity is not None
        assert entity.created_by == str(ACTOR_ID)
        note_content = await session.scalar(
            select(NoteContent).where(NoteContent.entity_id == entity.id)
        )
        assert note_content is not None
        assert (note_content.db_version, note_content.file_write_status) == (1, "pending")
        assert "# Accepted" in note_content.markdown_content
        recorded = await session.scalar(
            select(AcceptedProjectNoteChange).where(
                AcceptedProjectNoteChange.entity_id == entity.id
            )
        )
        assert recorded is not None and recorded.db_checksum == note_content.db_checksum


async def test_create_records_its_generation_as_pending_publication(
    session_maker: SessionMaker,
    dependencies: AcceptedNoteMutationDependencies,
    test_project: Project,
) -> None:
    """The graph is published after commit; the accept transaction marks it pending first."""
    result = await create(
        session_maker,
        dependencies,
        test_project,
        content="# Accepted\n\n- [name] Ada\n- works_at [[Target]]\n",
    )

    publication = result.relation_publication
    assert publication is not None
    assert [observation.content for observation in publication.observations] == ["Ada"]
    assert [relation.target_name for relation in publication.relations] == ["Target"]
    assert [section.heading_path for section in publication.sections] == ["Accepted"]
    async with db.scoped_session(session_maker) as session:
        markers = list(
            await session.scalars(
                select(RelationSearchRefresh.publication_generation).where(
                    RelationSearchRefresh.entity_id == publication.entity_id
                )
            )
        )
        # Nothing graph-shaped is written before commit; the marker carries the intent.
        relations = list(
            await session.scalars(
                select(Entity.id)
                .join(Entity.outgoing_relations)
                .where(Entity.id == publication.entity_id)
            )
        )
    assert markers == [publication.generation]
    assert relations == []


async def test_graph_silent_create_keeps_sections_only(
    session_maker: SessionMaker,
    dependencies: AcceptedNoteMutationDependencies,
    test_project: Project,
) -> None:
    """Derived documents keep their Markdown without recursively expanding the graph."""
    result = await create(
        session_maker,
        dependencies,
        test_project,
        content="# Accepted\n\n- [note] Generated list item\n- links_to [[Source Note]]\n",
        publish_graph_facts=False,
    )

    publication = result.relation_publication
    assert publication is not None
    assert (publication.observations, publication.relations) == ((), ())
    # Sections are structural, not semantic, so the section index still publishes.
    assert [section.heading_path for section in publication.sections] == ["Accepted"]


async def test_create_pre_resolves_only_unambiguous_self_links(
    session_maker: SessionMaker,
    dependencies: AcceptedNoteMutationDependencies,
    test_project: Project,
) -> None:
    """A path self link resolves inline; a title another note shares stays deferred."""
    await add_entity(session_maker, test_project, file_path="other/Accepted.md", title="Accepted")

    result = await create(
        session_maker,
        dependencies,
        test_project,
        content="# Accepted\n\n- documents [[notes/Accepted]]\n- mentions [[Accepted]]\n",
    )

    publication = result.relation_publication
    assert publication is not None
    targets = {relation.target_name: relation.target_id for relation in publication.relations}
    assert targets == {"notes/Accepted": publication.entity_id, "Accepted": None}


async def test_create_rejects_a_case_equivalent_markdown_path(
    session_maker: SessionMaker,
    dependencies: AcceptedNoteMutationDependencies,
    test_project: Project,
) -> None:
    await add_entity(session_maker, test_project, file_path="notes/accepted.md", title="accepted")

    with pytest.raises(AcceptedNoteMutationRejected) as rejected:
        await create(session_maker, dependencies, test_project)

    assert rejected.value.rejection.kind is AcceptedNoteMutationRejectKind.conflict
    assert "notes/accepted.md" in str(rejected.value.rejection.detail)


async def test_create_allows_a_case_equivalent_non_markdown_resource(
    session_maker: SessionMaker,
    dependencies: AcceptedNoteMutationDependencies,
    test_project: Project,
) -> None:
    await add_entity(
        session_maker,
        test_project,
        file_path="notes/accepted.png",
        title="accepted.png",
        content_type="image/png",
    )

    result = await create(session_maker, dependencies, test_project)

    assert result.change.status_code == 201


@pytest.mark.parametrize(
    ("existing_directories", "expected_path"),
    [
        pytest.param(["Notes", "specs"], "Notes/Accepted.md", id="unique-match-adopts-casing"),
        pytest.param(["Notes", "NOTES"], "notes/Accepted.md", id="ambiguous-keeps-request"),
    ],
)
async def test_create_resolves_directory_casing_only_when_unambiguous(
    session_maker: SessionMaker,
    dependencies: AcceptedNoteMutationDependencies,
    test_project: Project,
    existing_directories: list[str],
    expected_path: str,
) -> None:
    """A unique case-insensitive folder match redirects the create (#1326)."""
    for index, directory in enumerate(existing_directories):
        await add_entity(
            session_maker,
            test_project,
            file_path=f"{directory}/seed-{index}.md",
            title=f"seed-{index}",
        )

    result = await create(session_maker, dependencies, test_project)

    assert isinstance(result.change.payload, RuntimeAcceptedNoteResponse)
    assert result.change.payload.file_path == expected_path


async def test_an_empty_graph_still_publishes_so_cleanup_removes_stale_rows(
    session_maker: SessionMaker,
    dependencies: AcceptedNoteMutationDependencies,
    test_project: Project,
) -> None:
    result = await create(session_maker, dependencies, test_project, content="Plain prose\n")

    publication = result.relation_publication
    assert publication is not None
    assert (publication.observations, publication.relations) == ((), ())
    assert await pending_generations(session_maker, publication.entity_id) == [1]


# --- Update (PUT create-or-replace) ---


async def test_update_publishes_each_accepted_generation(
    session_maker: SessionMaker,
    dependencies: AcceptedNoteMutationDependencies,
    test_project: Project,
) -> None:
    """A PUT returns the note's full replacement graph for fenced publication."""
    note = await accepted_note(session_maker, dependencies, test_project)

    renamed = await update(
        session_maker,
        dependencies,
        test_project,
        note.external_id,
        title="Renamed",
        content="# Renamed\n\n- [status] Snapshot is complete\n- documents [[Another Note]]\n",
    )

    publication = renamed.relation_publication
    assert publication is not None
    assert publication.generation == 2
    assert [observation.content for observation in publication.observations] == [
        "Snapshot is complete"
    ]
    assert [(relation.target_name, relation.target_id) for relation in publication.relations] == [
        ("Another Note", None)
    ]
    assert [section.heading_path for section in publication.sections] == ["Renamed"]
    assert await pending_generations(session_maker, publication.entity_id) == [1, 2]


async def test_update_replaces_content_and_records_a_rename_as_a_move(
    session_maker: SessionMaker,
    dependencies: AcceptedNoteMutationDependencies,
    test_project: Project,
) -> None:
    note = await accepted_note(session_maker, dependencies, test_project)

    result = await update(
        session_maker,
        dependencies,
        test_project,
        note.external_id,
        title="Replacement",
        content="# Replacement\n",
    )

    change = result.change
    assert change.status_code == 200
    assert isinstance(change.payload, RuntimeAcceptedNoteResponse)
    assert change.payload.title == "Replacement"
    assert change.materialization is not None
    assert change.materialization.db_version == 2
    # The PUT carries the rename on its project change; the materialization job for a
    # PUT keeps no previous path of its own.
    assert change.materialization.previous_file_path is None
    project_change = change.project_change
    assert project_change is not None
    assert project_change.operation is RuntimeProjectNoteOperation.moved
    assert project_change.previous_file_path == "notes/Accepted.md"
    assert project_change.file_path == "notes/Replacement.md"
    assert project_change.db_version == 2
    assert change.materialization.project_change is project_change

    note_content = await required_note_content(session_maker, note.entity_id)
    assert note_content.db_version == 2
    assert "# Replacement" in note_content.markdown_content
    entity = await load_entity(session_maker, note.external_id)
    assert entity is not None and entity.file_path == "notes/Replacement.md"
    assert result.relation_publication is not None
    assert result.relation_publication.generation == 2
    assert await pending_generations(session_maker, note.entity_id) == [1, 2]


async def test_update_refreshes_the_source_path_after_taking_the_note_lock(
    session_maker: SessionMaker,
    dependencies: AcceptedNoteMutationDependencies,
    test_project: Project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A move committed while the PUT waited is the predecessor the PUT replaces."""
    note = await accepted_note(session_maker, dependencies, test_project)
    inject_before_note_lock(monkeypatch, concurrent_move(note.entity_id, "archive/Accepted.md"))

    result = await update(session_maker, dependencies, test_project, note.external_id)

    project_change = result.change.project_change
    assert project_change is not None
    assert project_change.operation is RuntimeProjectNoteOperation.moved
    assert project_change.previous_file_path == "archive/Accepted.md"
    assert project_change.file_path == "notes/Accepted.md"
    assert result.relation_publication is not None
    assert await pending_generations(session_maker, note.entity_id) == [
        1,
        result.relation_publication.generation,
    ]


async def test_update_accepts_a_matching_base_checksum(
    session_maker: SessionMaker,
    dependencies: AcceptedNoteMutationDependencies,
    test_project: Project,
) -> None:
    note = await accepted_note(session_maker, dependencies, test_project)

    result = await update(
        session_maker,
        dependencies,
        test_project,
        note.external_id,
        base_checksum=note.db_checksum,
    )

    assert result.change.status_code == 200
    note_content = await required_note_content(session_maker, note.entity_id)
    assert note_content.db_version == 2
    assert "Replaced body" in note_content.markdown_content
    assert await pending_generations(session_maker, note.entity_id) == [1, 2]


async def test_update_rejects_a_stale_base_checksum(
    session_maker: SessionMaker,
    dependencies: AcceptedNoteMutationDependencies,
    test_project: Project,
) -> None:
    """The client rebases instead of clobbering the newer write (#1445)."""
    note = await accepted_note(session_maker, dependencies, test_project)

    with pytest.raises(AcceptedNoteMutationRejected) as rejected:
        await update(
            session_maker,
            dependencies,
            test_project,
            note.external_id,
            base_checksum="stale-checksum",
        )

    rejection = rejected.value.rejection
    assert rejection.kind is AcceptedNoteMutationRejectKind.conflict
    assert rejection.kind.http_status_code == 409
    assert isinstance(rejection.detail, AcceptedNoteBaseChecksumConflict)
    assert rejection.detail.as_json_dict() == {
        "message": "Note changed since your last sync",
        "db_checksum": note.db_checksum,
    }
    note_content = await required_note_content(session_maker, note.entity_id)
    assert (note_content.db_version, note_content.db_checksum) == (1, note.db_checksum)
    assert "Replaced body" not in note_content.markdown_content
    assert await pending_generations(session_maker, note.entity_id) == [1]


async def test_relay_supersedes_its_own_head_despite_a_stale_base(
    session_maker: SessionMaker,
    dependencies: AcceptedNoteMutationDependencies,
    test_project: Project,
) -> None:
    """Lost-ack wedge (#1589): the relay's committed write is never a conflict for the relay."""
    note = await accepted_note(session_maker, dependencies, test_project)
    await seed_note_content(session_maker, note.entity_id, last_source="collaboration_relay")

    result = await update(
        session_maker,
        dependencies,
        test_project,
        note.external_id,
        source="collaboration_relay",
        base_checksum="stale-checksum",
    )

    assert result.change.status_code == 200
    note_content = await required_note_content(session_maker, note.entity_id)
    assert note_content.db_version == 2
    assert "Replaced body" in note_content.markdown_content
    assert await pending_generations(session_maker, note.entity_id) == [1, 2]


async def test_relay_supersedes_a_foreign_head_once_it_is_in_storage(
    session_maker: SessionMaker,
    dependencies: AcceptedNoteMutationDependencies,
    test_project: Project,
) -> None:
    """Hot-doc canonical (#1589 Phase G): a synced foreign head survives as file history."""
    note = await accepted_note(session_maker, dependencies, test_project)
    await materialize(session_maker, test_project, note.entity_id)
    await seed_note_content(session_maker, note.entity_id, last_source="mcp")

    result = await update(
        session_maker,
        dependencies,
        test_project,
        note.external_id,
        source="collaboration_relay",
        base_checksum="stale-checksum",
    )

    assert result.change.status_code == 200
    note_content = await required_note_content(session_maker, note.entity_id)
    assert (note_content.db_version, note_content.last_source) == (2, "collaboration_relay")
    assert await pending_generations(session_maker, note.entity_id) == [1, 2]


@pytest.mark.parametrize(
    "file_write_status",
    ["pending", "writing", "failed", "external_change_detected"],
)
async def test_relay_keeps_rejecting_a_foreign_head_not_yet_in_storage(
    session_maker: SessionMaker,
    dependencies: AcceptedNoteMutationDependencies,
    test_project: Project,
    file_write_status: str,
) -> None:
    """Only 'synced' proves the foreign head exists in storage (Codex, PR #1146).

    Superseding a pending, writing, failed or external-change head would erase its only copy.
    """
    note = await accepted_note(session_maker, dependencies, test_project)
    await seed_note_content(
        session_maker,
        note.entity_id,
        last_source="mcp",
        file_write_status=file_write_status,
    )

    with pytest.raises(AcceptedNoteMutationRejected) as rejected:
        await update(
            session_maker,
            dependencies,
            test_project,
            note.external_id,
            source="collaboration_relay",
            base_checksum="stale-checksum",
        )

    assert rejected.value.rejection.kind is AcceptedNoteMutationRejectKind.conflict
    note_content = await required_note_content(session_maker, note.entity_id)
    assert (note_content.db_version, note_content.last_source) == (1, "mcp")
    assert await pending_generations(session_maker, note.entity_id) == [1]


async def test_non_relay_writers_keep_the_stale_base_conflict(
    session_maker: SessionMaker,
    dependencies: AcceptedNoteMutationDependencies,
    test_project: Project,
) -> None:
    """The unconditional export is scoped to the relay writer only."""
    note = await accepted_note(session_maker, dependencies, test_project)
    await seed_note_content(session_maker, note.entity_id, last_source="collaboration_relay")

    with pytest.raises(AcceptedNoteMutationRejected) as rejected:
        await update(
            session_maker,
            dependencies,
            test_project,
            note.external_id,
            source="api",
            base_checksum="stale-checksum",
        )

    assert rejected.value.rejection.kind is AcceptedNoteMutationRejectKind.conflict
    note_content = await required_note_content(session_maker, note.entity_id)
    assert note_content.db_version == 1


async def test_update_with_a_base_checksum_does_not_resurrect_a_deleted_note(
    session_maker: SessionMaker,
    dependencies: AcceptedNoteMutationDependencies,
    test_project: Project,
) -> None:
    """A pinned revision of a note that no longer exists rejects with db_checksum None (#1445)."""
    missing_external_id = str(uuid4())

    with pytest.raises(AcceptedNoteMutationRejected) as rejected:
        await update(
            session_maker,
            dependencies,
            test_project,
            missing_external_id,
            base_checksum="synced-checksum",
        )

    rejection = rejected.value.rejection
    assert rejection.kind is AcceptedNoteMutationRejectKind.conflict
    assert isinstance(rejection.detail, AcceptedNoteBaseChecksumConflict)
    assert rejection.detail.as_json_dict() == {
        "message": "Note changed since your last sync",
        "db_checksum": None,
    }
    assert await load_entity(session_maker, missing_external_id) is None


async def test_update_without_a_base_checksum_creates_a_missing_note(
    session_maker: SessionMaker,
    dependencies: AcceptedNoteMutationDependencies,
    test_project: Project,
) -> None:
    """Without a precondition the PUT keeps its upsert contract."""
    external_id = str(uuid4())

    result = await update(session_maker, dependencies, test_project, external_id)

    assert result.change.status_code == 201
    assert result.change.project_change is not None
    assert result.change.project_change.operation is RuntimeProjectNoteOperation.created
    entity = await load_entity(session_maker, external_id)
    assert entity is not None and entity.file_path == "notes/Accepted.md"
    note_content = await required_note_content(session_maker, entity.id)
    assert note_content.db_version == 1
    assert await pending_generations(session_maker, entity.id) == [1]


async def test_update_rejects_a_rename_onto_an_unindexed_file(
    session_maker: SessionMaker,
    dependencies: AcceptedNoteMutationDependencies,
    test_project: Project,
) -> None:
    """Local storage is the source of truth: an unindexed file at the new path is not overwritten."""
    note = await accepted_note(session_maker, dependencies, test_project, title="original")
    write_project_file(test_project, "notes/Accepted.md", "# Someone else's file\n")

    with pytest.raises(AcceptedNoteMutationRejected) as rejected:
        await update(session_maker, dependencies, test_project, note.external_id)

    assert rejected.value.rejection.kind is AcceptedNoteMutationRejectKind.conflict
    assert "notes/Accepted.md" in str(rejected.value.rejection.detail)
    entity = await load_entity(session_maker, note.external_id)
    assert entity is not None and entity.file_path == "notes/original.md"
    assert (await required_note_content(session_maker, note.entity_id)).db_version == 1
    assert await pending_generations(session_maker, note.entity_id) == [1]


async def test_update_rejects_markdown_over_a_non_markdown_entity(
    session_maker: SessionMaker,
    dependencies: AcceptedNoteMutationDependencies,
    test_project: Project,
) -> None:
    """A binary resource has no note_content to replace: 415, not a backfill-retry 409."""
    external_id = await add_entity(
        session_maker,
        test_project,
        file_path="notes/image.png",
        title="image.png",
        content_type="image/png",
    )

    with pytest.raises(AcceptedNoteMutationRejected) as rejected:
        await update(session_maker, dependencies, test_project, external_id)

    rejection = rejected.value.rejection
    assert rejection.kind is AcceptedNoteMutationRejectKind.unsupported_media_type
    assert rejection.kind.http_status_code == 415
    entity = await load_entity(session_maker, external_id)
    assert entity is not None
    assert (entity.file_path, entity.content_type) == ("notes/image.png", "image/png")
    assert await load_note_content(session_maker, entity.id) is None


async def test_update_resolves_a_case_variant_directory_in_place(
    session_maker: SessionMaker,
    dependencies: AcceptedNoteMutationDependencies,
    test_project: Project,
) -> None:
    """A PUT with a case-variant directory replaces in place instead of renaming (#1326)."""
    note = await accepted_note(session_maker, dependencies, test_project, directory="Notes")

    result = await update(
        session_maker, dependencies, test_project, note.external_id, directory="notes"
    )

    change = result.change
    assert change.project_change is not None
    assert change.project_change.operation is RuntimeProjectNoteOperation.updated
    assert change.project_change.previous_file_path is None
    assert change.project_change.file_path == "Notes/Accepted.md"
    assert change.materialization is not None
    assert change.materialization.cleanup_after_write is None
    assert await vacated_paths(session_maker, test_project) == []
    entity = await load_entity(session_maker, note.external_id)
    assert entity is not None and entity.file_path == "Notes/Accepted.md"


async def test_content_only_update_skips_the_project_directory_scan(
    session_maker: SessionMaker,
    dependencies: AcceptedNoteMutationDependencies,
    test_project: Project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Content-only saves are the hot path and must not scan every project folder (PR #1329).

    The cost is not visible in resulting state, so this test spies on the real repository
    scan; the spy delegates to the real method and only counts calls.
    """
    note = await accepted_note(session_maker, dependencies, test_project)
    scans: list[EntityRepository] = []
    real_scan = EntityRepository.get_distinct_directories

    async def counting_scan(self: EntityRepository, session: AsyncSession) -> list[str]:
        scans.append(self)
        return await real_scan(self, session)

    monkeypatch.setattr(EntityRepository, "get_distinct_directories", counting_scan)

    await update(session_maker, dependencies, test_project, note.external_id)
    assert scans == []

    # A case-variant directory is the one PUT that must pay for the scan.
    await update(session_maker, dependencies, test_project, note.external_id, directory="NOTES")
    assert [repository.project_id for repository in scans] == [test_project.id]


async def test_graph_silent_update_clears_earlier_graph_facts(
    session_maker: SessionMaker,
    dependencies: AcceptedNoteMutationDependencies,
    test_project: Project,
) -> None:
    """A graph-silent replacement publishes empty sets so earlier facts are removed."""
    graph = "# Accepted\n\n- [note] Generated list item\n- links_to [[Source Note]]\n"
    note = await accepted_note(session_maker, dependencies, test_project, content=graph)

    result = await update(
        session_maker,
        dependencies,
        test_project,
        note.external_id,
        content=graph,
        source="wiki_projector",
        actor=AcceptedNoteMutationActor(user_profile_id=None, kind="system"),
        publish_graph_facts=False,
    )

    assert isinstance(result.change.payload, RuntimeAcceptedNoteResponse)
    assert "- links_to [[Source Note]]" in result.change.payload.markdown_content
    publication = result.relation_publication
    assert publication is not None
    assert (publication.observations, publication.relations) == ((), ())
    assert await pending_generations(session_maker, note.entity_id) == [1, 2]


# --- Edit ---


async def test_edit_applies_its_patch_to_the_accepted_db_content(
    session_maker: SessionMaker,
    dependencies: AcceptedNoteMutationDependencies,
    test_project: Project,
) -> None:
    note = await accepted_note(
        session_maker, dependencies, test_project, content="# Accepted\n\nOld line\n"
    )
    # The file on disk lags the accepted DB revision; the edit must patch the DB content.
    write_project_file(test_project, note.file_path, "# Accepted\n\nStale disk line\n")

    result = await edit(
        session_maker,
        dependencies,
        test_project,
        note.external_id,
        EditEntityRequest(operation="find_replace", content="New line", find_text="Old line"),
    )

    change = result.change
    assert change.status_code == 200
    assert change.materialization is not None
    assert change.materialization.source == "mcp"
    project_change = change.project_change
    assert project_change is not None
    assert project_change.operation is RuntimeProjectNoteOperation.updated
    assert project_change.previous_file_path is None
    assert project_change.actor_user_profile_id is None
    assert change.materialization.project_change is project_change
    note_content = await required_note_content(session_maker, note.entity_id)
    assert (note_content.db_version, note_content.last_source) == (2, "mcp")
    assert "New line" in note_content.markdown_content
    assert "Old line" not in note_content.markdown_content
    assert await pending_generations(session_maker, note.entity_id) == [1, 2]


async def test_edit_merges_metadata_into_frontmatter(
    session_maker: SessionMaker,
    dependencies: AcceptedNoteMutationDependencies,
    test_project: Project,
) -> None:
    """`metadata` merges frontmatter independent of `operation` (#1011)."""
    note = await accepted_note(
        session_maker, dependencies, test_project, content="# Accepted\n\nOld line\n"
    )

    await edit(
        session_maker,
        dependencies,
        test_project,
        note.external_id,
        EditEntityRequest(
            operation="find_replace",
            content="New line",
            find_text="Old line",
            metadata={"status": "resolved"},
        ),
    )

    note_content = await required_note_content(session_maker, note.entity_id)
    assert "status: resolved" in note_content.markdown_content
    entity = await load_entity(session_maker, note.external_id)
    assert entity is not None and entity.entity_metadata is not None
    assert entity.entity_metadata["status"] == "resolved"


async def test_edit_that_drops_the_graph_publishes_empty_sets(
    session_maker: SessionMaker,
    dependencies: AcceptedNoteMutationDependencies,
    test_project: Project,
) -> None:
    """An edit that removes every fact returns empty sets for fenced cleanup."""
    note = await accepted_note(
        session_maker,
        dependencies,
        test_project,
        content="# Accepted\n\n- [fact] Gone soon\n- relates_to [[Other]]\n",
    )

    result = await edit(
        session_maker,
        dependencies,
        test_project,
        note.external_id,
        EditEntityRequest(
            operation="find_replace",
            content="Plain prose\n",
            find_text="- [fact] Gone soon\n- relates_to [[Other]]\n",
        ),
    )

    assert result.change.status_code == 200
    publication = result.relation_publication
    assert publication is not None
    assert (publication.observations, publication.relations) == ((), ())
    assert await pending_generations(session_maker, note.entity_id) == [1, 2]


# --- Move ---


async def test_move_republishes_the_note_graph_at_its_new_generation(
    session_maker: SessionMaker,
    dependencies: AcceptedNoteMutationDependencies,
    test_project: Project,
) -> None:
    note = await accepted_note(
        session_maker,
        dependencies,
        test_project,
        content="# Accepted\n\n- [fact] Move keeps this #move\n",
    )

    moved = await move(
        session_maker,
        dependencies,
        test_project,
        note.external_id,
        "archive/Accepted.md",
        source="api",
    )

    publication = moved.relation_publication
    assert publication is not None
    assert publication.generation == 2
    assert [(o.content, o.tags) for o in publication.observations] == [
        ("Move keeps this #move", ["move"])
    ]
    assert publication.relations == ()
    assert [section.heading_path for section in publication.sections] == ["Accepted"]
    assert await pending_generations(session_maker, publication.entity_id) == [1, 2]


@pytest.mark.parametrize(
    ("update_permalinks_on_move", "moved_permalink_suffix"),
    [
        pytest.param(True, "archive/accepted", id="permalink-follows-the-file"),
        pytest.param(False, "notes/accepted", id="permalink-stays"),
    ],
)
async def test_move_follows_the_permalink_policy_and_versions_the_accepted_markdown(
    session_maker: SessionMaker,
    dependencies: AcceptedNoteMutationDependencies,
    test_project: Project,
    update_permalinks_on_move: bool,
    moved_permalink_suffix: str,
) -> None:
    note = await accepted_note(session_maker, dependencies, test_project)
    policy_dependencies = replace(
        dependencies,
        move_policy=AcceptedNoteMutationMovePolicy(
            update_permalinks_on_move=update_permalinks_on_move
        ),
    )

    moved = await move(
        session_maker,
        policy_dependencies,
        test_project,
        note.external_id,
        "archive/Accepted.md",
        source="api",
    )

    assert isinstance(moved.change.payload, RuntimeAcceptedNoteResponse)
    assert moved.change.payload.file_path == "archive/Accepted.md"
    async with db.scoped_session(session_maker) as session:
        entity = await session.scalar(select(Entity).where(Entity.external_id == note.external_id))
        note_content = await session.scalar(
            select(NoteContent).where(NoteContent.external_id == note.external_id)
        )
    assert entity is not None and note_content is not None
    assert entity.permalink is not None and entity.permalink.endswith(moved_permalink_suffix)
    # The accepted revision is identified by the hash of exactly the markdown it stores.
    assert note_content.db_checksum == await file_utils.compute_checksum(
        note_content.markdown_content
    )


type SourceState = Literal[
    "synced",
    "synced-source-absent",
    "checksum-missing",
    "pending-before-write",
    "failed-before-write",
    "published-checksum-stale",
]


@pytest.mark.parametrize(
    ("source_state", "expected_cleanup"),
    [
        pytest.param("synced", "published-file", id="synced"),
        pytest.param("synced-source-absent", None, id="synced-source-absent"),
        pytest.param("checksum-missing", None, id="checksum-missing"),
        pytest.param("pending-before-write", "published-file", id="pending-before-write"),
        pytest.param("failed-before-write", "published-file", id="failed-before-write"),
        pytest.param("published-checksum-stale", "accepted-db", id="published-checksum-stale"),
    ],
)
async def test_move_carries_its_previous_path_and_claims_only_proven_source_bytes(
    session_maker: SessionMaker,
    dependencies: AcceptedNoteMutationDependencies,
    test_project: Project,
    source_state: SourceState,
    expected_cleanup: Literal["published-file", "accepted-db"] | None,
) -> None:
    """Cleanup of the vacated source is claimed only when storage proves which bytes it holds."""
    note = await accepted_note(session_maker, dependencies, test_project)
    published_checksum: str | None = None
    match source_state:
        case "synced":
            published_checksum = await materialize(session_maker, test_project, note.entity_id)
        case "synced-source-absent":
            # The DB says synced, but the file was deleted locally since.
            published_checksum = await materialize(session_maker, test_project, note.entity_id)
            (Path(test_project.path) / note.file_path).unlink()
        case "checksum-missing":
            pass  # Never materialized: no file and no recorded file checksum.
        case "pending-before-write" | "failed-before-write" | "published-checksum-stale":
            # v1 is on disk; v2 is accepted and its materialization has not been recorded.
            published_checksum = await materialize(session_maker, test_project, note.entity_id)
            await update(session_maker, dependencies, test_project, note.external_id)
            if source_state == "failed-before-write":
                await seed_note_content(session_maker, note.entity_id, file_write_status="failed")
            if source_state == "published-checksum-stale":
                # v2 reached disk, but materialization crashed before recording it.
                await seed_note_content(session_maker, note.entity_id, file_write_status="writing")
                accepted = await required_note_content(session_maker, note.entity_id)
                write_project_file(test_project, note.file_path, accepted.markdown_content)
    before_move = await required_note_content(session_maker, note.entity_id)
    expected_checksum = {
        "published-file": published_checksum,
        "accepted-db": before_move.db_checksum,
        None: None,
    }[expected_cleanup]

    result = await move(
        session_maker,
        dependencies,
        test_project,
        note.external_id,
        "archive/Accepted.md",
        actor=AcceptedNoteMutationActor(user_profile_id=ACTOR_ID, kind="mcp", name="Claude"),
    )

    change = result.change
    assert change.status_code == 200
    entity = await load_entity(session_maker, note.external_id)
    assert entity is not None
    # update_permalinks_on_move is on in the integration config.
    assert entity.file_path == "archive/Accepted.md"
    assert entity.permalink is not None and entity.permalink.endswith("archive/accepted")
    assert change.materialization is not None
    assert change.materialization.previous_file_path == "notes/Accepted.md"
    project_change = change.project_change
    assert project_change is not None
    assert project_change.operation is RuntimeProjectNoteOperation.moved
    assert (project_change.previous_file_path, project_change.file_path) == (
        "notes/Accepted.md",
        "archive/Accepted.md",
    )
    assert (
        project_change.actor_user_profile_id,
        project_change.actor_kind,
        project_change.actor_name,
    ) == (ACTOR_ID, "mcp", "Claude")
    assert change.materialization.project_change is project_change

    cleanup = change.materialization.cleanup_after_write
    if expected_checksum is None:
        assert cleanup is None
        assert await vacated_paths(session_maker, test_project) == []
    else:
        assert cleanup is not None
        assert (cleanup.file_path, cleanup.file_checksum) == (
            "notes/Accepted.md",
            expected_checksum,
        )
        assert await vacated_paths(session_maker, test_project) == [
            ("notes/Accepted.md", expected_checksum)
        ]

    publication = result.relation_publication
    assert publication is not None
    assert publication.generation == before_move.db_version + 1
    assert [section.heading_path for section in publication.sections] == ["Accepted"]
    assert (await pending_generations(session_maker, note.entity_id))[-1] == publication.generation


async def test_move_rejects_the_same_file_path(
    session_maker: SessionMaker,
    dependencies: AcceptedNoteMutationDependencies,
    test_project: Project,
) -> None:
    note = await accepted_note(session_maker, dependencies, test_project)

    with pytest.raises(AcceptedNoteMutationRejected) as rejected:
        await move(session_maker, dependencies, test_project, note.external_id, note.file_path)

    assert rejected.value.rejection.kind is AcceptedNoteMutationRejectKind.bad_request
    assert rejected.value.rejection.detail == "Source and destination paths are the same."
    assert await pending_generations(session_maker, note.entity_id) == [1]


async def test_move_refreshes_the_source_path_after_taking_the_note_lock(
    session_maker: SessionMaker,
    dependencies: AcceptedNoteMutationDependencies,
    test_project: Project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An overlapping move records the committed source path it actually replaces."""
    note = await accepted_note(session_maker, dependencies, test_project, title="original")
    inject_before_note_lock(monkeypatch, concurrent_move(note.entity_id, "notes/intermediate.md"))

    result = await move(
        session_maker, dependencies, test_project, note.external_id, "archive/accepted.md"
    )

    assert result.change.project_change is not None
    assert result.change.project_change.previous_file_path == "notes/intermediate.md"
    assert result.change.materialization is not None
    assert result.change.materialization.previous_file_path == "notes/intermediate.md"
    entity = await load_entity(session_maker, note.external_id)
    assert entity is not None and entity.file_path == "archive/accepted.md"


async def test_move_resolves_the_destination_directory_casing(
    session_maker: SessionMaker,
    dependencies: AcceptedNoteMutationDependencies,
    test_project: Project,
) -> None:
    """A move destination parent adopts the unique existing folder casing (#1326)."""
    await add_entity(session_maker, test_project, file_path="Archive/seed.md", title="seed")
    note = await accepted_note(session_maker, dependencies, test_project)

    result = await move(
        session_maker, dependencies, test_project, note.external_id, "archive/Accepted.md"
    )

    entity = await load_entity(session_maker, note.external_id)
    assert entity is not None and entity.file_path == "Archive/Accepted.md"
    assert result.change.materialization is not None
    assert result.change.materialization.previous_file_path == "notes/Accepted.md"


async def test_move_rejects_a_case_variant_of_its_current_path(
    session_maker: SessionMaker,
    dependencies: AcceptedNoteMutationDependencies,
    test_project: Project,
) -> None:
    """A destination resolving onto the note's own path is a same-path move."""
    note = await accepted_note(
        session_maker, dependencies, test_project, title="accepted", directory="Notes"
    )

    with pytest.raises(AcceptedNoteMutationRejected) as rejected:
        await move(session_maker, dependencies, test_project, note.external_id, "notes/accepted.md")

    assert rejected.value.rejection.kind is AcceptedNoteMutationRejectKind.bad_request
    assert rejected.value.rejection.detail == "Source and destination paths are the same."
    entity = await load_entity(session_maker, note.external_id)
    assert entity is not None and entity.file_path == "Notes/accepted.md"


# --- Delete ---


async def test_delete_removes_the_note_and_returns_guarded_cleanup(
    session_maker: SessionMaker,
    dependencies: AcceptedNoteMutationDependencies,
    test_project: Project,
) -> None:
    note = await accepted_note(session_maker, dependencies, test_project)
    file_checksum = await materialize(session_maker, test_project, note.entity_id)
    assert await search_row_count(session_maker, note.entity_id) > 0

    result = await delete_note(session_maker, dependencies, test_project, note.external_id)

    change = result.change
    assert change.status_code == 200
    assert isinstance(change.payload, dict) and change.payload["deleted"] is True
    file_delete = change.file_delete
    assert file_delete is not None
    assert (file_delete.file_path, file_delete.file_checksum) == (
        "notes/Accepted.md",
        file_checksum,
    )
    project_change = change.project_change
    assert project_change is not None
    assert project_change.operation is RuntimeProjectNoteOperation.deleted
    assert project_change.file_path == "notes/Accepted.md"
    assert project_change.source == "delete_note"
    assert (project_change.db_version, project_change.db_checksum) == (1, note.db_checksum)
    assert (
        project_change.actor_user_profile_id,
        project_change.actor_kind,
        project_change.actor_name,
    ) == (None, None, None)
    assert file_delete.project_change is project_change
    assert change.relation_cleanup_entity_ids == frozenset()
    assert result.relation_publication is None

    assert await load_entity(session_maker, note.external_id) is None
    assert await load_note_content(session_maker, note.entity_id) is None
    assert await search_row_count(session_maker, note.entity_id) == 0
    journal = await project_change_rows(session_maker, note.entity_id)
    assert [row.operation for row in journal] == ["created", "deleted"]


async def test_delete_returns_the_notes_that_linked_to_it(
    session_maker: SessionMaker,
    dependencies: AcceptedNoteMutationDependencies,
    test_project: Project,
) -> None:
    """Surviving notes still show the deleted target in search until they are re-indexed (#1351)."""
    target = await accepted_note(session_maker, dependencies, test_project)
    sources = [
        await accepted_note(session_maker, dependencies, test_project, title=title)
        for title in ("First Source", "Second Source")
    ]
    # Resolved links as relation resolution would leave them.
    async with db.scoped_session(session_maker) as session:
        for source in sources:
            session.add(
                Relation(
                    project_id=test_project.id,
                    from_id=source.entity_id,
                    to_id=target.entity_id,
                    to_name="Accepted",
                    relation_type="links_to",
                    generation=1,
                )
            )

    result = await delete_note(session_maker, dependencies, test_project, target.external_id)

    assert result.change.relation_cleanup_entity_ids == frozenset(
        source.entity_id for source in sources
    )


async def test_delete_records_its_actor_on_the_project_change(
    session_maker: SessionMaker,
    dependencies: AcceptedNoteMutationDependencies,
    test_project: Project,
) -> None:
    """A delete is the change people most want to trace back to its author."""
    note = await accepted_note(session_maker, dependencies, test_project)

    result = await delete_note(
        session_maker,
        dependencies,
        test_project,
        note.external_id,
        actor=AcceptedNoteMutationActor(user_profile_id=ACTOR_ID, kind="user", name="Ada"),
    )

    project_change = result.change.project_change
    assert project_change is not None
    assert project_change.operation is RuntimeProjectNoteOperation.deleted
    assert project_change.source == "delete_note"
    assert (
        project_change.actor_user_profile_id,
        project_change.actor_kind,
        project_change.actor_name,
    ) == (ACTOR_ID, "user", "Ada")
    journal = await project_change_rows(session_maker, note.entity_id)
    deleted = journal[-1]
    assert (
        deleted.operation,
        deleted.actor_user_profile_id,
        deleted.actor_kind,
        deleted.actor_name,
    ) == (
        "deleted",
        str(ACTOR_ID),
        "user",
        "Ada",
    )


async def test_delete_stays_idempotent_after_a_concurrent_delete(
    session_maker: SessionMaker,
    dependencies: AcceptedNoteMutationDependencies,
    test_project: Project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A delete that loses the race to another delete acknowledges without recording a change."""
    note = await accepted_note(session_maker, dependencies, test_project)

    async def concurrent_delete(session: AsyncSession) -> None:
        await session.execute(
            delete(Entity)
            .where(Entity.id == note.entity_id)
            .execution_options(synchronize_session=False)
        )

    inject_before_note_lock(monkeypatch, concurrent_delete)

    result = await delete_note(session_maker, dependencies, test_project, note.external_id)

    assert result.change.status_code == 200
    assert result.change.payload == {"deleted": False}
    assert result.change.project_change is None
    assert result.change.file_delete is None
    assert [row.operation for row in await project_change_rows(session_maker, note.entity_id)] == [
        "created"
    ]
