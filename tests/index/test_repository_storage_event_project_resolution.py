"""Tests for repository-backed storage-event project resolution."""

import pytest

from basic_memory import db
from basic_memory.index import RepositoryStorageEventProjectResolver
from basic_memory.index import repository_project_resolution as resolver_module
from basic_memory.models import Project
from basic_memory.repository import ProjectRepository
from basic_memory.runtime import (
    ProjectRuntimeReference,
    StorageProjectPrefixMatch,
    StorageProjectPrefixResolution,
)


async def create_project(
    *,
    project_repository: ProjectRepository,
    session_maker,
    name: str,
    path: str,
    is_active: bool = True,
) -> Project:
    async with db.scoped_session(session_maker) as session:
        return await project_repository.create(
            session,
            {
                "name": name,
                "path": path,
                "is_active": is_active,
            },
        )


@pytest.mark.asyncio
async def test_repository_storage_event_project_resolver_uses_cloud_prefix_rules(
    project_repository: ProjectRepository,
    session_maker,
) -> None:
    """Storage-event routing resolves path, name, and legacy path-suffix project prefixes."""
    path_project = await create_project(
        project_repository=project_repository,
        session_maker=session_maker,
        name="Path Project",
        path="path-prefix",
    )
    name_project = await create_project(
        project_repository=project_repository,
        session_maker=session_maker,
        name="name-prefix",
        path="/projects/name-project",
    )
    suffix_project = await create_project(
        project_repository=project_repository,
        session_maker=session_maker,
        name="Suffix Project",
        path="/app/data/suffix-prefix",
    )
    resolver = RepositoryStorageEventProjectResolver(
        project_repository=project_repository,
        session_maker=session_maker,
    )

    assert await resolver.resolve_project("path-prefix") == ProjectRuntimeReference.from_project(
        path_project
    )
    assert await resolver.resolve_project("name-prefix") == ProjectRuntimeReference.from_project(
        name_project
    )
    assert await resolver.resolve_project("suffix-prefix") == ProjectRuntimeReference.from_project(
        suffix_project
    )


@pytest.mark.asyncio
async def test_repository_storage_event_project_resolver_skips_missing_inactive_and_ambiguous(
    project_repository: ProjectRepository,
    session_maker,
) -> None:
    """Storage-event routing returns None when a project prefix is not safely routable."""
    await create_project(
        project_repository=project_repository,
        session_maker=session_maker,
        name="Inactive",
        path="inactive-prefix",
        is_active=False,
    )
    await create_project(
        project_repository=project_repository,
        session_maker=session_maker,
        name="Ambiguous Left",
        path="/left/shared-prefix",
    )
    await create_project(
        project_repository=project_repository,
        session_maker=session_maker,
        name="Ambiguous Right",
        path="/right/shared-prefix",
    )
    resolver = RepositoryStorageEventProjectResolver(
        project_repository=project_repository,
        session_maker=session_maker,
    )

    assert await resolver.resolve_project("missing-prefix") is None
    assert await resolver.resolve_project("inactive-prefix") is None
    assert await resolver.resolve_project("shared-prefix") is None


@pytest.mark.asyncio
async def test_repository_storage_event_project_resolver_fails_fast_on_incomplete_suffix_result(
    project_repository: ProjectRepository,
    session_maker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The resolver rejects an impossible suffix match with no matched project."""

    def incomplete_prefix_resolution(*args, **kwargs) -> StorageProjectPrefixResolution[Project]:
        return StorageProjectPrefixResolution(
            bucket_prefix="broken-prefix",
            match=StorageProjectPrefixMatch.path_suffix,
            project=None,
        )

    monkeypatch.setattr(
        resolver_module,
        "resolve_storage_project_prefix",
        incomplete_prefix_resolution,
    )
    resolver = RepositoryStorageEventProjectResolver(
        project_repository=project_repository,
        session_maker=session_maker,
    )

    with pytest.raises(RuntimeError, match="Storage prefix suffix resolution had no project"):
        await resolver.find_project_by_bucket_prefix("broken-prefix")
