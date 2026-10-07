"""Accepted note creates against a real database and the production mutation dependencies."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.config import BasicMemoryConfig
from basic_memory.index.local_notes import LocalAcceptedNotePreparerFactory
from basic_memory.indexing.accepted_note_mutation_runner import (
    AcceptedNoteCreateMutation,
    AcceptedNoteMutationActor,
    AcceptedNoteMutationDependencies,
    AcceptedNoteMutationMovePolicy,
    AcceptedNoteMutationRejectKind,
    AcceptedNoteMutationRejected,
    AcceptedNoteMutationResult,
    run_accepted_note_create,
)
from basic_memory.models import (
    AcceptedProjectNoteChange,
    Entity,
    NoteContent,
    Project,
    RelationSearchRefresh,
)
from basic_memory.repository import ProjectRepository
from basic_memory.repository.accepted_note_repositories import AcceptedNoteRepositories
from basic_memory.repository.search_repository import create_search_repository
from basic_memory.runtime.note_content import RuntimeAcceptedNoteResponse
from basic_memory.runtime.project_partition import RuntimeProjectNoteOperation
from basic_memory.schemas.base import Entity as EntitySchema
from basic_memory.services.note_content_writes import accepted_note_transaction

type SessionMaker = async_sessionmaker[AsyncSession]

ACTOR_ID = uuid4()


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
                actor=AcceptedNoteMutationActor(
                    user_profile_id=ACTOR_ID, kind="user", name="Ada"
                ),
                source="api",
                publish_graph_facts=publish_graph_facts,
            ),
            dependencies=dependencies,
        )


async def add_entity(
    session_maker: SessionMaker,
    project: Project,
    *,
    file_path: str,
    title: str,
    content_type: str = "text/markdown",
) -> None:
    """Seed an indexed row the create must account for."""
    now = datetime.now(UTC)
    async with db.scoped_session(session_maker) as session:
        session.add(
            Entity(
                project_id=project.id,
                external_id=str(uuid4()),
                title=title,
                note_type="note",
                content_type=content_type,
                file_path=file_path,
                permalink=file_path.rsplit(".", 1)[0].lower(),
                created_at=now,
                updated_at=now,
            )
        )


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
                select(Entity.id).join(Entity.outgoing_relations).where(
                    Entity.id == publication.entity_id
                )
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
