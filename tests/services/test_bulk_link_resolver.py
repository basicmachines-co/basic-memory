"""Tests for bulk exact link resolution used by relation repair."""

from datetime import datetime, timezone

import pytest
import pytest_asyncio

from basic_memory import db
from basic_memory.config import BasicMemoryConfig
from basic_memory.models import Entity, Project
from basic_memory.repository import EntityRepository, ProjectRepository
from basic_memory.services.bulk_link_resolver import (
    BulkLinkResolver,
    ProjectEntityIdentityIndex,
    RelationTargetReference,
)
from basic_memory.services.exceptions import AmbiguousIdentifierError
from basic_memory.services.link_resolver import LinkResolver


def note_permalink(project: Project, path: str, app_config: BasicMemoryConfig) -> str:
    """Return the configured canonical permalink for a test note."""
    if app_config.permalinks_include_project:
        return f"{project.permalink}/{path}"
    return path


def test_relation_target_reference_keeps_incomplete_project_paths_local() -> None:
    target = RelationTargetReference.parse("/")

    assert target.project_path() == (None, "/")


@pytest_asyncio.fixture
async def bulk_entities(
    entity_repository: EntityRepository,
    test_project: Project,
    session_maker,
    app_config: BasicMemoryConfig,
) -> list[Entity]:
    """Create exact, ambiguous, and regular-file targets for bulk resolution."""
    now = datetime.now(timezone.utc)
    entities = [
        Entity(
            title="Auth Service",
            note_type="component",
            content_type="text/markdown",
            file_path="components/Auth Service.md",
            permalink=note_permalink(test_project, "components/auth-service", app_config),
            created_at=now,
            updated_at=now,
            project_id=test_project.id,
        ),
        Entity(
            title="Core Service",
            note_type="component",
            content_type="text/markdown",
            file_path="components/Core Service.md",
            permalink=note_permalink(test_project, "components/core-service", app_config),
            created_at=now,
            updated_at=now,
            project_id=test_project.id,
        ),
        Entity(
            title="Core Service",
            note_type="component",
            content_type="text/markdown",
            file_path="archive/Core Service.md",
            permalink=note_permalink(test_project, "archive/core-service", app_config),
            created_at=now,
            updated_at=now,
            project_id=test_project.id,
        ),
        Entity(
            title="image.png",
            note_type="file",
            content_type="image/png",
            file_path="assets/image.png",
            permalink=None,
            created_at=now,
            updated_at=now,
            project_id=test_project.id,
        ),
    ]
    async with db.scoped_session(session_maker) as session:
        for entity in entities:
            await entity_repository.add(session, entity)
    return entities


@pytest.mark.asyncio
async def test_bulk_resolution_matches_regular_strict_resolution(
    entity_repository: EntityRepository,
    search_service,
    session_maker,
    app_config: BasicMemoryConfig,
    bulk_entities: list[Entity],
) -> None:
    """The bulk flavor preserves the regular resolver's exact-match contract."""
    regular_resolver = LinkResolver(
        entity_repository,
        search_service,
        session_maker,
        app_config,
    )
    bulk_resolver = BulkLinkResolver(entity_repository, app_config)
    auth_service, _, _, image = bulk_entities
    assert auth_service.permalink is not None
    link_texts = [
        auth_service.external_id.upper(),
        auth_service.title,
        auth_service.permalink,
        auth_service.file_path,
        "components/Auth Service",
        image.title,
        "Does Not Exist",
        "Core Service",
    ]

    async with db.scoped_session(session_maker) as session:
        bulk_results = await bulk_resolver.resolve_relation_targets(
            link_texts,
            session=session,
        )
        for link_text in link_texts[:-1]:
            regular_result = await regular_resolver.resolve_link(
                link_text,
                strict=True,
                load_relations=False,
                session=session,
            )
            bulk_result = bulk_results[link_text]
            assert (bulk_result.id if bulk_result else None) == (
                regular_result.id if regular_result else None
            )

        with pytest.raises(AmbiguousIdentifierError):
            await regular_resolver.resolve_link(
                "Core Service",
                strict=True,
                load_relations=False,
                session=session,
            )

    assert bulk_results["Core Service"] is None


@pytest.mark.asyncio
async def test_bulk_resolution_normalizes_file_paths(
    entity_repository: EntityRepository,
    session_maker,
    app_config: BasicMemoryConfig,
    bulk_entities: list[Entity],
) -> None:
    """Bulk file lookup uses the same Path normalization as EntityRepository."""
    image = bulk_entities[-1]
    now = datetime.now(timezone.utc)
    project_id = entity_repository.project_id
    assert project_id is not None
    custom_permalink_note = Entity(
        title="Custom Guide",
        note_type="note",
        content_type="text/markdown",
        file_path="docs/Guide.md",
        permalink="hand-picked-guide",
        created_at=now,
        updated_at=now,
        project_id=project_id,
    )
    async with db.scoped_session(session_maker) as session:
        await entity_repository.add(session, custom_permalink_note)

    resolver = BulkLinkResolver(entity_repository, app_config)

    async with db.scoped_session(session_maker) as session:
        results = await resolver.resolve_relation_targets(
            ["./assets//image.png", "docs/Guide"],
            session=session,
        )

    resolved_image = results["./assets//image.png"]
    assert resolved_image is not None
    assert resolved_image.id == image.id
    resolved_guide = results["docs/Guide"]
    assert resolved_guide is not None
    assert resolved_guide.id == custom_permalink_note.id


def test_project_entity_index_reports_title_derived_permalink_ambiguity(
    test_project: Project,
    bulk_entities: list[Entity],
) -> None:
    bulk_entities[1].permalink = "core-service"
    index = ProjectEntityIdentityIndex.from_entities(test_project, bulk_entities)

    match = index.resolve_strict(
        "Core Service",
        include_project_permalinks=False,
        workspace_permalink=None,
    )

    assert match.entity is None
    assert match.ambiguous


@pytest.mark.asyncio
async def test_bulk_resolution_routes_cross_project_targets(
    entity_repository: EntityRepository,
    session_maker,
    tmp_path,
    app_config: BasicMemoryConfig,
    bulk_entities: list[Entity],
) -> None:
    """Explicit and project/path references resolve from one multi-project snapshot."""
    project_repository = ProjectRepository()
    now = datetime.now(timezone.utc)
    async with db.scoped_session(session_maker) as session:
        other_project = await project_repository.create(
            session,
            {
                "name": "Other Project",
                "description": "Secondary project",
                "path": str(tmp_path / "other-project"),
                "is_active": True,
                "is_default": False,
            },
        )
        other_entity_repository = EntityRepository(project_id=other_project.id)
        target = await other_entity_repository.add(
            session,
            Entity(
                title="Cross Project Note",
                note_type="note",
                content_type="text/markdown",
                file_path="docs/Cross Project Note.md",
                permalink=note_permalink(
                    other_project,
                    "docs/cross-project-note",
                    app_config,
                ),
                created_at=now,
                updated_at=now,
                project_id=other_project.id,
            ),
        )

    resolver = BulkLinkResolver(
        entity_repository,
        app_config,
        project_repository=project_repository,
    )
    async with db.scoped_session(session_maker) as session:
        results = await resolver.resolve_relation_targets(
            [
                "other project::Cross Project Note",
                "Other Project/docs/cross-project-note",
                "missing::Cross Project Note",
            ],
            session=session,
        )

    explicit_result = results["other project::Cross Project Note"]
    path_result = results["Other Project/docs/cross-project-note"]
    assert explicit_result is not None
    assert path_result is not None
    assert explicit_result.id == target.id
    assert path_result.id == target.id
    assert results["missing::Cross Project Note"] is None


@pytest.mark.asyncio
async def test_bulk_resolution_skips_database_work_for_an_empty_batch(
    entity_repository: EntityRepository,
    session_maker,
    app_config: BasicMemoryConfig,
) -> None:
    resolver = BulkLinkResolver(entity_repository, app_config)

    async with db.scoped_session(session_maker) as session:
        assert await resolver.resolve_relation_targets([], session=session) == {}


@pytest.mark.asyncio
async def test_bulk_resolution_requires_the_current_project_to_exist(
    session_maker,
    app_config: BasicMemoryConfig,
) -> None:
    resolver = BulkLinkResolver(EntityRepository(project_id=999_999), app_config)

    async with db.scoped_session(session_maker) as session:
        with pytest.raises(RuntimeError, match="Current project 999999 does not exist"):
            await resolver.resolve_relation_targets(["Target"], session=session)
