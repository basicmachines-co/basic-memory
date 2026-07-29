"""Full-stack API coverage against the real Redis read cache."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.deps import get_index_file_executor_v2_external, get_read_cache
from basic_memory.indexing.models import FileIndexResult
from basic_memory.models import Project
from basic_memory.models.knowledge import Entity
from basic_memory.read_cache import ReadCacheKey, ReadCacheOperation, read_cache_request_digest
from basic_memory.read_cache.keys import (
    redis_read_cache_generation_key,
    redis_read_cache_keys,
)
from basic_memory.read_cache.redis import RedisReadCache
from basic_memory.repository import EntityRepository
from basic_memory.runtime.note_content import NOTE_CONTENT_BASE_CHECKSUM_HEADER
from basic_memory.schemas.v2 import EntityResolveRequest
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
async def test_entity_resolve_and_markdown_reads_cache_then_write_invalidates(
    app: FastAPI,
    client: AsyncClient,
    test_project: Project,
    redis_cache: RedisCacheHarness,
) -> None:
    """Successful reads populate Redis; rejected writes preserve and accepted writes bump."""
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
        assert await redis_cache.client.exists(redis_keys.data) == 1

    generation_key = redis_read_cache_generation_key(
        prefix=redis_cache.prefix,
        namespace=redis_cache.namespace,
        project_id=project_external_id,
    )
    populated_generation = await redis_cache.client.get(generation_key)
    assert populated_generation is not None

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
    assert await redis_cache.client.get(generation_key) == populated_generation

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
    assert invalidated_generation != populated_generation

    refreshed_entity = await client.get(f"{project_url}/knowledge/entities/{entity_id}")
    refreshed_resource = await client.get(f"{project_url}/resource/{entity_id}")
    assert refreshed_entity.status_code == 200
    assert "Version two." in refreshed_entity.json()["content"]
    assert refreshed_resource.status_code == 200
    assert "Version two." in refreshed_resource.text


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
    assert await redis_cache.client.exists(redis_keys.data) == 0


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
