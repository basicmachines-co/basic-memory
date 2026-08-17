"""Full-stack API coverage against the real Redis read cache."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Protocol, override
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

import basic_memory.indexing.note_content_reconciler as note_content_reconciler
import basic_memory.services.note_content_writes as note_content_writes
from basic_memory import db
from basic_memory.deps import (
    get_chatgpt_importer_v2_external,
    get_index_file_executor_v2_external,
    get_note_content_query_service,
    get_read_cache,
)
from basic_memory.indexing.note_content_read_repair_runner import (
    NoteContentReadRepairFile,
    NoteContentReadRepairTarget,
)
from basic_memory.indexing.models import FileIndexResult
from basic_memory.index.note_content_materialization import drain_pending_materializations
from basic_memory.models import Project
from basic_memory.models.knowledge import Entity
from basic_memory.read_cache import (
    ReadCacheInvalidator,
    ReadCacheInvalidationStatus,
    ReadCacheKey,
    ReadCacheOperation,
    read_cache_request_digest,
)
from basic_memory.read_cache.keys import (
    redis_read_cache_generation_key,
    redis_read_cache_keys,
)
from basic_memory.read_cache.redis import RedisReadCache
from basic_memory.repository import EntityRepository
from basic_memory.repository.note_content_repository import NoteContentRepository
from basic_memory.runtime.note_content import NOTE_CONTENT_BASE_CHECKSUM_HEADER
from basic_memory.schemas.directory import DirectoryListResponse, DirectoryNode
from basic_memory.schemas.v2 import EntityResolveRequest
from basic_memory.services.note_content_reads import NoteContentQueryService
from basic_memory.workspace_context import (
    WORKSPACE_SLUG_HEADER,
    WORKSPACE_TYPE_HEADER,
)


class RedisCacheHarness(Protocol):
    """Structural type for the real Redis fixture."""

    cache: RedisReadCache
    client: Redis
    namespace: str
    prefix: str


pytestmark = pytest.mark.redis


class FailingIndexFileExecutor:
    """Represent an indexer that fails after it may have committed entity state."""

    async def index_file(
        self,
        file_path: str,
        *,
        source: str,
    ) -> FileIndexResult:
        del file_path, source
        raise RuntimeError("partial direct index failure")


class PartiallyFailingImporter:
    """Overwrite one resource before reporting an import failure."""

    def __init__(self, target: Path) -> None:
        self.target = target

    async def import_data(
        self,
        source_data: object,
        destination_folder: str,
        **kwargs: object,
    ) -> None:
        del source_data, destination_folder, kwargs
        self.target.write_text("# Imported before failure\n", encoding="utf-8")
        raise RuntimeError("partial import failure")


class LocalReadRepairFileReader:
    """Read canonical project files through the hosted read-repair protocol."""

    async def read_note_content_repair_file(
        self,
        target: NoteContentReadRepairTarget[Project, Entity],
    ) -> NoteContentReadRepairFile | None:
        path = Path(target.project.path) / target.entity.file_path
        if not path.exists():
            return None
        stat = path.stat()
        return NoteContentReadRepairFile(
            markdown_content=path.read_text(encoding="utf-8"),
            observed_at=datetime.fromtimestamp(stat.st_mtime, timezone.utc),
        )


class NoAcceptedNoteContent:
    """Keep this resource on the file-first path for project-root cache coverage."""

    async def get_note_resource_with_read_repair(
        self,
        *,
        project_external_id: str,
        entity_external_id: str,
        session: AsyncSession,
        read_cache: ReadCacheInvalidator,
    ) -> None:
        del project_external_id, entity_external_id, session, read_cache
        return None


class DestinationObservingRedisReadCache(RedisReadCache):
    """Record how many target files exist at each real invalidation."""

    def __init__(
        self,
        *,
        client: Redis,
        namespace: str,
        prefix: str,
        destination_paths: tuple[Path, ...],
    ) -> None:
        super().__init__(client=client, namespace=namespace, prefix=prefix)
        self.destination_paths = destination_paths
        self.destination_counts: list[int] = []
        self.project_ids: list[str] = []

    @override
    async def invalidate_project(self, project_id: str) -> ReadCacheInvalidationStatus:
        status = await super().invalidate_project(project_id)
        self.project_ids.append(project_id)
        self.destination_counts.append(
            sum(destination.exists() for destination in self.destination_paths)
        )
        return status


class BlockingInvalidationRedisReadCache(RedisReadCache):
    """Hold one real invalidation so the request can be cancelled repeatedly."""

    def __init__(
        self,
        *,
        client: Redis,
        namespace: str,
        prefix: str,
    ) -> None:
        super().__init__(client=client, namespace=namespace, prefix=prefix)
        self.invalidation_started = asyncio.Event()
        self.release_invalidation = asyncio.Event()

    @override
    async def invalidate_project(self, project_id: str) -> ReadCacheInvalidationStatus:
        self.invalidation_started.set()
        await self.release_invalidation.wait()
        return await super().invalidate_project(project_id)


def _cache_key(
    *,
    project_id: str,
    operation: ReadCacheOperation,
    request: str,
    request_context: tuple[str, ...] = (),
) -> ReadCacheKey:
    return ReadCacheKey(
        project_id=project_id,
        operation=operation,
        request_digest=read_cache_request_digest(request, *request_context),
    )


def descendant_paths(node: DirectoryNode) -> set[str]:
    """Collect one directory response's complete path set."""
    paths = {node.directory_path}
    for child in node.children:
        paths.update(descendant_paths(child))
    return paths


async def _initialized_generation(
    redis_cache: RedisCacheHarness,
    project_id: str,
    *,
    request: str,
) -> bytes | str:
    await redis_cache.cache.lookup(
        _cache_key(
            project_id=project_id,
            operation=ReadCacheOperation.entity,
            request=request,
        )
    )
    generation = await redis_cache.client.get(
        redis_read_cache_generation_key(
            prefix=redis_cache.prefix,
            namespace=redis_cache.namespace,
            project_id=project_id,
        )
    )
    assert generation is not None
    return generation


@pytest.mark.asyncio
async def test_cancelled_committed_create_finishes_real_redis_invalidation(
    app: FastAPI,
    client: AsyncClient,
    engine_factory: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    test_project: Project,
    redis_cache: RedisCacheHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancellation after commit waits for invalidation before propagating."""
    project_external_id = str(test_project.external_id)
    generation_before = await _initialized_generation(
        redis_cache,
        project_external_id,
        request="cancelled-committed-create",
    )
    blocking_cache = BlockingInvalidationRedisReadCache(
        client=redis_cache.client,
        namespace=redis_cache.namespace,
        prefix=redis_cache.prefix,
    )
    app.dependency_overrides[get_read_cache] = lambda: blocking_cache

    transaction_committed = asyncio.Event()
    hold_after_commit = asyncio.Event()
    original_transaction = note_content_writes.accepted_note_transaction

    @asynccontextmanager
    async def pause_after_commit(
        session_maker: async_sessionmaker[AsyncSession],
    ) -> AsyncIterator[AsyncSession]:
        async with original_transaction(session_maker) as session:
            yield session
        transaction_committed.set()
        await hold_after_commit.wait()

    monkeypatch.setattr(
        note_content_writes,
        "accepted_note_transaction",
        pause_after_commit,
    )
    title = f"Cancelled committed create {uuid4()}"
    request_task = asyncio.create_task(
        client.post(
            f"/v2/projects/{project_external_id}/knowledge/entities",
            json={
                "title": title,
                "directory": "cache",
                "content": f"# {title}\n",
            },
        )
    )

    async with asyncio.timeout(5):
        await transaction_committed.wait()
    request_task.cancel()
    async with asyncio.timeout(5):
        await blocking_cache.invalidation_started.wait()

    # A second cancellation must not abandon the already-running cleanup.
    request_task.cancel()
    await asyncio.sleep(0)
    assert not request_task.done()

    blocking_cache.release_invalidation.set()
    with pytest.raises(asyncio.CancelledError):
        await request_task

    generation_after = await _initialized_generation(
        redis_cache,
        project_external_id,
        request="cancelled-committed-create",
    )
    assert generation_after != generation_before

    _, session_maker = engine_factory
    async with db.scoped_session(session_maker) as session:
        committed_entities = await EntityRepository(project_id=test_project.id).get_by_title(
            session,
            title,
        )
    assert len(committed_entities) == 1


@pytest.mark.asyncio
async def test_entity_resolve_and_markdown_reads_cache_then_freshened_write_invalidates(
    app: FastAPI,
    client: AsyncClient,
    test_project: Project,
    redis_cache: RedisCacheHarness,
) -> None:
    """Pre-write freshening invalidates even when the following write is rejected."""
    app.dependency_overrides[get_read_cache] = lambda: redis_cache.cache
    project_external_id = str(test_project.external_id)
    project_url = f"/v2/projects/{project_external_id}"

    created_response = await client.post(
        f"{project_url}/knowledge/entities",
        json={
            "title": "Redis Cached Note",
            "directory": "cache",
            "content": "# Redis Cached Note\n\nVersion one.",
        },
    )
    assert created_response.status_code == 202
    created = created_response.json()
    entity_id = created["external_id"]
    await drain_pending_materializations()

    entity_response = await client.get(f"{project_url}/knowledge/entities/{entity_id}")
    resolve_request = EntityResolveRequest(identifier=created["permalink"])
    resolve_response = await client.post(
        f"{project_url}/knowledge/resolve",
        json=resolve_request.model_dump(mode="json"),
    )
    workspace_resolve_response = await client.post(
        f"{project_url}/knowledge/resolve",
        headers={
            WORKSPACE_SLUG_HEADER: "team-paul",
            WORKSPACE_TYPE_HEADER: "organization",
        },
        json=resolve_request.model_dump(mode="json"),
    )
    resource_response = await client.get(f"{project_url}/resource/{entity_id}")

    assert entity_response.status_code == 200
    assert resolve_response.status_code == 200
    assert workspace_resolve_response.status_code == 200
    assert resource_response.status_code == 200
    assert "Version one." in resource_response.text

    keys = (
        _cache_key(
            project_id=project_external_id,
            operation=ReadCacheOperation.entity,
            request=entity_id,
        ),
        _cache_key(
            project_id=project_external_id,
            operation=ReadCacheOperation.resolve,
            request=resolve_request.model_dump_json(),
            request_context=("", ""),
        ),
        _cache_key(
            project_id=project_external_id,
            operation=ReadCacheOperation.resolve,
            request=resolve_request.model_dump_json(),
            request_context=("team-paul", "organization"),
        ),
        _cache_key(
            project_id=project_external_id,
            operation=ReadCacheOperation.resource,
            request=entity_id,
        ),
    )
    for key in keys:
        redis_keys = redis_read_cache_keys(
            prefix=redis_cache.prefix,
            namespace=redis_cache.namespace,
            key=key,
        )
        assert await redis_cache.client.exists(redis_keys.data_key) == 1

    generation_key = redis_read_cache_generation_key(
        prefix=redis_cache.prefix,
        namespace=redis_cache.namespace,
        project_id=project_external_id,
    )
    populated_generation = await redis_cache.client.get(generation_key)
    assert populated_generation is not None

    disk_path = Path(test_project.path) / created["file_path"]
    disk_path.write_text(
        "# Redis Cached Note\n\nExternally edited before rejected update.",
        encoding="utf-8",
    )
    rejected_response = await client.put(
        f"{project_url}/knowledge/entities/{entity_id}",
        headers={NOTE_CONTENT_BASE_CHECKSUM_HEADER: "stale-checksum"},
        json={
            "title": "Redis Cached Note",
            "directory": "cache",
            "content": "# Redis Cached Note\n\nRejected replacement.",
        },
    )
    assert rejected_response.status_code == 409
    freshened_generation = await redis_cache.client.get(generation_key)
    assert freshened_generation is not None
    assert freshened_generation != populated_generation

    freshened_entity = await client.get(f"{project_url}/knowledge/entities/{entity_id}")
    freshened_resource = await client.get(f"{project_url}/resource/{entity_id}")
    assert freshened_entity.status_code == 200
    assert "Externally edited" in freshened_entity.json()["content"]
    assert freshened_resource.status_code == 200
    assert "Externally edited" in freshened_resource.text

    edited_response = await client.patch(
        f"{project_url}/knowledge/entities/{entity_id}",
        json={
            "operation": "append",
            "content": "\n\nVersion two.",
        },
    )
    assert edited_response.status_code == 202
    invalidated_generation = await redis_cache.client.get(generation_key)
    assert invalidated_generation is not None
    assert invalidated_generation != freshened_generation

    refreshed_entity = await client.get(f"{project_url}/knowledge/entities/{entity_id}")
    refreshed_resource = await client.get(f"{project_url}/resource/{entity_id}")
    assert refreshed_entity.status_code == 200
    assert "Externally edited" in refreshed_entity.json()["content"]
    assert "Version two." in refreshed_entity.json()["content"]
    assert refreshed_resource.status_code == 200
    assert "Externally edited" in refreshed_resource.text
    assert "Version two." in refreshed_resource.text


@pytest.mark.asyncio
async def test_directory_reads_cache_then_project_invalidation_refreshes(
    app: FastAPI,
    client: AsyncClient,
    engine_factory: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    test_project: Project,
    redis_cache: RedisCacheHarness,
) -> None:
    """Tree, structure, and paginated list reads share real project invalidation."""
    project_external_id = str(test_project.external_id)
    project_url = f"/v2/projects/{project_external_id}"
    repository = EntityRepository(project_id=test_project.id)
    _, session_maker = engine_factory

    async with db.scoped_session(session_maker) as session:
        await repository.add(
            session,
            Entity(
                title="Existing cached directory note",
                note_type="note",
                content_type="text/markdown",
                file_path="cache-surface/existing.md",
                checksum="existing-cached-directory-checksum",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            ),
        )
        await repository.add(
            session,
            Entity(
                title="Zebra cached directory note",
                note_type="note",
                content_type="text/markdown",
                file_path="cache-surface/second.md",
                checksum="second-cached-directory-checksum",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            ),
        )

    app.dependency_overrides[get_read_cache] = lambda: redis_cache.cache
    tree_url = f"{project_url}/directory/tree"
    structure_url = f"{project_url}/directory/structure"
    list_url = f"{project_url}/directory/list"
    list_params = {
        "dir_name": "/cache-surface",
        "depth": 2,
        "page": 1,
        "page_size": 200,
    }

    first_tree_response = await client.get(tree_url)
    first_structure_response = await client.get(structure_url)
    first_list_response = await client.get(list_url, params=list_params)
    title_desc_response = await client.get(
        list_url,
        params={**list_params, "sort": "title_desc"},
    )
    assert first_tree_response.status_code == 200
    assert first_structure_response.status_code == 200
    assert first_list_response.status_code == 200
    assert title_desc_response.status_code == 200

    first_tree = DirectoryNode.model_validate(first_tree_response.json())
    first_structure = DirectoryNode.model_validate(first_structure_response.json())
    first_list = DirectoryListResponse.model_validate(first_list_response.json())
    title_desc_list = DirectoryListResponse.model_validate(title_desc_response.json())
    assert "/cache-surface/after-cache" not in descendant_paths(first_tree)
    assert "/cache-surface/after-cache" not in descendant_paths(first_structure)
    assert "/cache-surface/after-cache" not in {node.directory_path for node in first_list.nodes}
    assert [node.title for node in first_list.nodes] == [
        "Existing cached directory note",
        "Zebra cached directory note",
    ]
    assert [node.title for node in title_desc_list.nodes] == [
        "Zebra cached directory note",
        "Existing cached directory note",
    ]

    cache_keys = (
        _cache_key(
            project_id=project_external_id,
            operation=ReadCacheOperation.directory_tree,
            request="tree",
        ),
        _cache_key(
            project_id=project_external_id,
            operation=ReadCacheOperation.directory_structure,
            request="structure",
        ),
        _cache_key(
            project_id=project_external_id,
            operation=ReadCacheOperation.directory_list,
            request="/cache-surface",
            request_context=("2", "", "", "1", "200"),
        ),
        _cache_key(
            project_id=project_external_id,
            operation=ReadCacheOperation.directory_list,
            request="/cache-surface",
            request_context=("2", "", "title_desc", "1", "200"),
        ),
    )
    for key in cache_keys:
        redis_keys = redis_read_cache_keys(
            prefix=redis_cache.prefix,
            namespace=redis_cache.namespace,
            key=key,
        )
        assert await redis_cache.client.exists(redis_keys.data_key) == 1

    async with db.scoped_session(session_maker) as session:
        await repository.add(
            session,
            Entity(
                title="Added after directory cache fill",
                note_type="note",
                content_type="text/markdown",
                file_path="cache-surface/after-cache/new.md",
                checksum="added-after-directory-cache-checksum",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            ),
        )

    cached_tree_response = await client.get(tree_url)
    cached_structure_response = await client.get(structure_url)
    cached_list_response = await client.get(list_url, params=list_params)
    cached_title_desc_response = await client.get(
        list_url,
        params={**list_params, "sort": "title_desc"},
    )
    assert cached_tree_response.json() == first_tree_response.json()
    assert cached_structure_response.json() == first_structure_response.json()
    assert cached_list_response.json() == first_list_response.json()
    assert cached_title_desc_response.json() == title_desc_response.json()

    await redis_cache.cache.invalidate_project(project_external_id)

    refreshed_tree = DirectoryNode.model_validate((await client.get(tree_url)).json())
    refreshed_structure = DirectoryNode.model_validate((await client.get(structure_url)).json())
    refreshed_list = DirectoryListResponse.model_validate(
        (await client.get(list_url, params=list_params)).json()
    )

    assert "/cache-surface/after-cache" in descendant_paths(refreshed_tree)
    assert "/cache-surface/after-cache" in descendant_paths(refreshed_structure)
    assert "/cache-surface/after-cache" in {node.directory_path for node in refreshed_list.nodes}


@pytest.mark.asyncio
async def test_non_markdown_resource_is_never_cached(
    app: FastAPI,
    client: AsyncClient,
    test_project: Project,
    redis_cache: RedisCacheHarness,
    engine_factory: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    """Arbitrary files remain authoritative even when they fit under the payload cap."""
    app.dependency_overrides[get_read_cache] = lambda: redis_cache.cache
    project_external_id = str(test_project.external_id)
    file_path = "cache/plain.txt"
    disk_path = Path(test_project.path) / file_path
    disk_path.parent.mkdir(parents=True, exist_ok=True)
    disk_path.write_text("Plain text remains uncached.", encoding="utf-8")

    repository = EntityRepository(project_id=test_project.id)
    _, session_maker = engine_factory
    async with db.scoped_session(session_maker) as session:
        entity = await repository.add(
            session,
            Entity(
                title="plain.txt",
                note_type="file",
                content_type="text/plain",
                file_path=file_path,
                checksum="plain-checksum",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            ),
        )

    resource_url = f"/v2/projects/{project_external_id}/resource/{entity.external_id}"
    first = await client.get(resource_url)
    second = await client.get(resource_url)
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.text == "Plain text remains uncached."

    key = _cache_key(
        project_id=project_external_id,
        operation=ReadCacheOperation.resource,
        request=entity.external_id,
    )
    redis_keys = redis_read_cache_keys(
        prefix=redis_cache.prefix,
        namespace=redis_cache.namespace,
        key=key,
    )
    assert await redis_cache.client.exists(redis_keys.data_key) == 0


@pytest.mark.asyncio
async def test_project_root_change_invalidates_cached_markdown_resource(
    app: FastAPI,
    client: AsyncClient,
    engine_factory: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    test_project: Project,
    redis_cache: RedisCacheHarness,
    tmp_path: Path,
) -> None:
    """A stable project/entity UUID cannot retain bytes from the previous root."""
    app.dependency_overrides[get_read_cache] = lambda: redis_cache.cache
    app.dependency_overrides[get_note_content_query_service] = NoAcceptedNoteContent
    project_external_id = str(test_project.external_id)
    file_path = "cache/project-root.md"
    old_disk_path = Path(test_project.path) / file_path
    old_disk_path.parent.mkdir(parents=True, exist_ok=True)
    old_disk_path.write_text("# Old project root\n", encoding="utf-8")

    new_root = tmp_path / "new-project-root"
    new_disk_path = new_root / file_path
    new_disk_path.parent.mkdir(parents=True, exist_ok=True)
    new_disk_path.write_text("# New project root\n", encoding="utf-8")

    repository = EntityRepository(project_id=test_project.id)
    _, session_maker = engine_factory
    async with db.scoped_session(session_maker) as session:
        entity = await repository.add(
            session,
            Entity(
                title="project-root.md",
                note_type="file",
                content_type="text/markdown",
                file_path=file_path,
                checksum="project-root-checksum",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            ),
        )

    project_url = f"/v2/projects/{project_external_id}"
    resource_url = f"{project_url}/resource/{entity.external_id}"
    cached_response = await client.get(resource_url)
    assert cached_response.status_code == 200
    assert cached_response.text == "# Old project root\n"
    generation_before = await _initialized_generation(
        redis_cache,
        project_external_id,
        request="project-root-change",
    )

    move_response = await client.patch(
        project_url,
        json={"path": str(new_root)},
    )

    assert move_response.status_code == 200
    generation_after = await _initialized_generation(
        redis_cache,
        project_external_id,
        request="project-root-change",
    )
    assert generation_after != generation_before
    refreshed_response = await client.get(resource_url)
    assert refreshed_response.status_code == 200
    assert refreshed_response.text == "# New project root\n"


@pytest.mark.asyncio
async def test_direct_index_failure_invalidates_real_redis(
    app: FastAPI,
    test_project: Project,
    redis_cache: RedisCacheHarness,
) -> None:
    """A partial direct index commit cannot retain the previous cache generation."""
    app.dependency_overrides[get_read_cache] = lambda: redis_cache.cache
    app.dependency_overrides[get_index_file_executor_v2_external] = FailingIndexFileExecutor
    project_external_id = str(test_project.external_id)
    generation_before = await _initialized_generation(
        redis_cache,
        project_external_id,
        request="direct-index-failure",
    )
    file_path = "cache/direct-index-failure.md"
    disk_path = Path(test_project.path) / file_path
    disk_path.parent.mkdir(parents=True, exist_ok=True)
    disk_path.write_text("# Partial direct index\n", encoding="utf-8")

    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as failure_client:
        response = await failure_client.post(
            f"/v2/projects/{project_external_id}/knowledge/index-file",
            json={"file_path": file_path},
        )

    assert response.status_code == 500
    generation_after = await _initialized_generation(
        redis_cache,
        project_external_id,
        request="direct-index-failure",
    )
    assert generation_after != generation_before


@pytest.mark.asyncio
async def test_partial_import_failure_invalidates_cached_resource_in_real_redis(
    app: FastAPI,
    client: AsyncClient,
    engine_factory: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    test_project: Project,
    redis_cache: RedisCacheHarness,
) -> None:
    """File writes from a failed import cannot retain cached pre-import bytes."""
    app.dependency_overrides[get_read_cache] = lambda: redis_cache.cache
    project_external_id = str(test_project.external_id)
    file_path = "cache/partial-import.md"
    disk_path = Path(test_project.path) / file_path
    disk_path.parent.mkdir(parents=True, exist_ok=True)
    disk_path.write_text("# Before import\n", encoding="utf-8")

    repository = EntityRepository(project_id=test_project.id)
    _, session_maker = engine_factory
    async with db.scoped_session(session_maker) as session:
        entity = await repository.add(
            session,
            Entity(
                title="partial-import.md",
                note_type="note",
                content_type="text/markdown",
                file_path=file_path,
                checksum="partial-import-checksum",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            ),
        )

    resource_url = f"/v2/projects/{project_external_id}/resource/{entity.external_id}"
    cached_response = await client.get(resource_url)
    assert cached_response.status_code == 200
    assert cached_response.text == "# Before import\n"
    generation_before = await _initialized_generation(
        redis_cache,
        project_external_id,
        request="partial-import-failure",
    )
    app.dependency_overrides[get_chatgpt_importer_v2_external] = lambda: PartiallyFailingImporter(
        disk_path
    )

    response = await client.post(
        f"/v2/projects/{project_external_id}/import/chatgpt",
        files={"file": ("conversations.json", b"[]", "application/json")},
    )

    assert response.status_code == 500
    generation_after = await _initialized_generation(
        redis_cache,
        project_external_id,
        request="partial-import-failure",
    )
    assert generation_after != generation_before
    refreshed_response = await client.get(resource_url)
    assert refreshed_response.status_code == 200
    assert refreshed_response.text == "# Imported before failure\n"


@pytest.mark.asyncio
async def test_import_invalidates_after_each_written_file_in_real_redis(
    app: FastAPI,
    client: AsyncClient,
    test_project: Project,
    redis_cache: RedisCacheHarness,
) -> None:
    """Each imported file advances Redis before the next item is processed."""
    project_external_id = str(test_project.external_id)
    destination_directory = "cache/import-per-file"
    destination_paths = tuple(
        Path(test_project.path) / destination_directory / "note" / f"{name}.md"
        for name in ("First import", "Second import")
    )
    observing_cache = DestinationObservingRedisReadCache(
        client=redis_cache.client,
        namespace=redis_cache.namespace,
        prefix=redis_cache.prefix,
        destination_paths=destination_paths,
    )
    app.dependency_overrides[get_read_cache] = lambda: observing_cache
    source_data = b"\n".join(
        json.dumps(
            {
                "type": "entity",
                "name": name,
                "entityType": "note",
                "observations": [],
            }
        ).encode()
        for name in ("First import", "Second import")
    )

    response = await client.post(
        f"/v2/projects/{project_external_id}/import/memory-json",
        files={"file": ("memory.json", source_data, "application/json")},
        data={"directory": destination_directory},
    )

    assert response.status_code == 200
    assert response.json()["entities"] == 2
    assert observing_cache.destination_counts[:2] == [1, 2]
    assert observing_cache.project_ids[:2] == [project_external_id, project_external_id]
    assert all(destination.exists() for destination in destination_paths)


@pytest.mark.asyncio
async def test_resource_read_repair_invalidates_cached_entity_in_real_redis(
    app: FastAPI,
    client: AsyncClient,
    engine_factory: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    test_project: Project,
    redis_cache: RedisCacheHarness,
) -> None:
    """A resource-triggered note_content repair supersedes cached entity metadata."""
    app.dependency_overrides[get_read_cache] = lambda: redis_cache.cache
    project_external_id = str(test_project.external_id)
    file_path = "cache/read-repair.md"
    disk_path = Path(test_project.path) / file_path
    disk_path.parent.mkdir(parents=True, exist_ok=True)
    disk_path.write_text("# Read repair\n\nCanonical file content.\n", encoding="utf-8")

    repository = EntityRepository(project_id=test_project.id)
    _, session_maker = engine_factory
    async with db.scoped_session(session_maker) as session:
        entity = await repository.add(
            session,
            Entity(
                title="Read repair",
                note_type="note",
                content_type="text/markdown",
                file_path=file_path,
                checksum="read-repair-checksum",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            ),
        )

    project_url = f"/v2/projects/{project_external_id}"
    entity_url = f"{project_url}/knowledge/entities/{entity.external_id}"
    resource_url = f"{project_url}/resource/{entity.external_id}"
    cached_entity = await client.get(entity_url)
    assert cached_entity.status_code == 200
    assert cached_entity.json().get("db_version") is None
    generation_before = await _initialized_generation(
        redis_cache,
        project_external_id,
        request="resource-read-repair",
    )

    query_service = NoteContentQueryService(
        session_maker=session_maker,
        read_repair_file_reader=LocalReadRepairFileReader(),
    )
    app.dependency_overrides[get_note_content_query_service] = lambda: query_service

    repaired_resource = await client.get(resource_url)
    assert repaired_resource.status_code == 200
    assert repaired_resource.text == "# Read repair\n\nCanonical file content.\n"
    generation_after = await _initialized_generation(
        redis_cache,
        project_external_id,
        request="resource-read-repair",
    )
    assert generation_after != generation_before

    refreshed_entity = await client.get(entity_url)
    assert refreshed_entity.status_code == 200
    assert refreshed_entity.json()["db_version"] == 1
    assert "Canonical file content." in refreshed_entity.json()["content"]


@pytest.mark.asyncio
@pytest.mark.parametrize("read_method", ["entity", "resource"])
async def test_cancelled_committed_read_repair_finishes_real_redis_invalidation(
    engine_factory: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    test_project: Project,
    redis_cache: RedisCacheHarness,
    read_method: Literal["entity", "resource"],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancellation during read-repair transaction exit waits for invalidation."""
    project_external_id = str(test_project.external_id)
    file_path = "cache/cancelled-read-repair.md"
    markdown_content = "# Cancelled read repair\n\nCommitted canonical content.\n"
    disk_path = Path(test_project.path) / file_path
    disk_path.parent.mkdir(parents=True, exist_ok=True)
    disk_path.write_text(markdown_content, encoding="utf-8")

    repository = EntityRepository(project_id=test_project.id)
    _, session_maker = engine_factory
    async with db.scoped_session(session_maker) as session:
        entity = await repository.add(
            session,
            Entity(
                title="Cancelled read repair",
                note_type="note",
                content_type="text/markdown",
                file_path=file_path,
                checksum="cancelled-read-repair-checksum",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            ),
        )

    blocking_cache = BlockingInvalidationRedisReadCache(
        client=redis_cache.client,
        namespace=redis_cache.namespace,
        prefix=redis_cache.prefix,
    )
    generation_before = await _initialized_generation(
        redis_cache,
        project_external_id,
        request="cancelled-committed-read-repair",
    )
    transaction_committed = asyncio.Event()
    hold_after_commit = asyncio.Event()
    original_scoped_session = note_content_reconciler.db.scoped_session

    @asynccontextmanager
    async def pause_after_repair_commit(
        scoped_session_maker: async_sessionmaker[AsyncSession],
        session: AsyncSession | None = None,
    ) -> AsyncIterator[AsyncSession]:
        async with original_scoped_session(scoped_session_maker, session) as scoped_session:
            yield scoped_session
        if transaction_committed.is_set():
            return

        async with original_scoped_session(scoped_session_maker) as verification_session:
            repaired = await NoteContentRepository(project_id=test_project.id).get_by_entity_id(
                verification_session,
                entity.id,
            )
        if repaired is not None:
            transaction_committed.set()
            await hold_after_commit.wait()

    monkeypatch.setattr(
        note_content_reconciler.db,
        "scoped_session",
        pause_after_repair_commit,
    )

    query_service = NoteContentQueryService(
        session_maker=session_maker,
        read_repair_file_reader=LocalReadRepairFileReader(),
    )
    if read_method == "entity":
        read_repair = query_service.get_note_entity_payload_with_read_repair(
            project_external_id=project_external_id,
            entity_external_id=entity.external_id,
            read_cache=blocking_cache,
        )
    else:
        read_repair = query_service.get_note_resource_with_read_repair(
            project_external_id=project_external_id,
            entity_external_id=entity.external_id,
            read_cache=blocking_cache,
        )
    request_task = asyncio.create_task(read_repair)

    async with asyncio.timeout(5):
        await transaction_committed.wait()
    request_task.cancel()
    async with asyncio.timeout(5):
        await blocking_cache.invalidation_started.wait()

    # A second cancellation must not abandon the already-running generation bump.
    request_task.cancel()
    await asyncio.sleep(0)
    assert not request_task.done()

    blocking_cache.release_invalidation.set()
    with pytest.raises(asyncio.CancelledError):
        await request_task

    generation_after = await _initialized_generation(
        redis_cache,
        project_external_id,
        request="cancelled-committed-read-repair",
    )
    assert generation_after != generation_before

    async with db.scoped_session(session_maker) as session:
        repaired = await NoteContentRepository(project_id=test_project.id).get_by_entity_id(
            session,
            entity.id,
        )
    assert repaired is not None
    assert repaired.db_version == 1
    assert repaired.markdown_content == markdown_content


@pytest.mark.asyncio
async def test_directory_move_invalidates_after_each_committed_file_in_real_redis(
    app: FastAPI,
    client: AsyncClient,
    test_project: Project,
    redis_cache: RedisCacheHarness,
) -> None:
    """Each file move advances Redis before the next directory item is processed."""
    project_external_id = str(test_project.external_id)
    project_url = f"/v2/projects/{project_external_id}"
    created_paths: list[str] = []
    for title in ("First directory move", "Second directory move"):
        response = await client.post(
            f"{project_url}/knowledge/entities",
            json={
                "title": title,
                "directory": "move-source",
                "content": f"# {title}\n",
            },
        )
        assert response.status_code == 202
        created_paths.append(response.json()["file_path"])

    destination_paths = tuple(
        Path(test_project.path) / source_path.replace("move-source/", "move-destination/", 1)
        for source_path in created_paths
    )
    observing_cache = DestinationObservingRedisReadCache(
        client=redis_cache.client,
        namespace=redis_cache.namespace,
        prefix=redis_cache.prefix,
        destination_paths=destination_paths,
    )
    app.dependency_overrides[get_read_cache] = lambda: observing_cache
    generation_before = await _initialized_generation(
        redis_cache,
        project_external_id,
        request="directory-move",
    )

    response = await client.post(
        f"{project_url}/knowledge/move-directory",
        json={
            "source_directory": "move-source",
            "destination_directory": "move-destination",
        },
    )

    assert response.status_code == 200
    assert response.json()["successful_moves"] == 2
    assert observing_cache.destination_counts[:2] == [1, 2]
    assert all(destination.exists() for destination in destination_paths)
    generation_after = await _initialized_generation(
        redis_cache,
        project_external_id,
        request="directory-move",
    )
    assert generation_after != generation_before
