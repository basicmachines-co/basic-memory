"""Integration coverage for the Redis semantic read cache."""

from __future__ import annotations

import asyncio
import socket
from typing import override, Protocol
from uuid import uuid4

import pytest
from pydantic import BaseModel, ValidationError
from redis.asyncio import Redis

from basic_memory.read_cache import (
    ModelReadCache,
    ReadCacheDataError,
    ReadCacheInvalidationStatus,
    ReadCacheKey,
    ReadCacheLookup,
    ReadCacheOperation,
    ReadCacheStoreStatus,
    ReadCacheUnavailable,
    finish_project_read_cache_invalidation,
    invalidate_cache,
    invalidate_project_read_cache,
    read_cache_request_digest,
)
from basic_memory.read_cache.keys import (
    redis_read_cache_generation_key,
    redis_read_cache_keys,
)
from basic_memory.read_cache.redis import (
    RedisReadCache,
    _remaining_ttl_seconds,
    _required_bytes,
    _store_status,
    create_redis_read_cache_client,
)


class RedisCacheHarness(Protocol):
    """Structural type for the real Redis fixture."""

    cache: RedisReadCache
    client: Redis
    namespace: str
    prefix: str


class CachedEntity(BaseModel):
    """Small typed boundary value used by read-through tests."""

    external_id: str
    title: str


class CachedResolution(BaseModel):
    """A second boundary type used to prove facade-local serialization."""

    external_id: str
    resolution_method: str


pytestmark = pytest.mark.redis

PROJECT_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
OTHER_PROJECT_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


def _key(
    *,
    project_id: str = PROJECT_ID,
    operation: ReadCacheOperation = ReadCacheOperation.entity,
    request: str = "entity-1",
) -> ReadCacheKey:
    return ReadCacheKey(
        project_id=project_id,
        operation=operation,
        request_digest=read_cache_request_digest(request),
    )


def _model_cache(
    backend: RedisReadCache,
    *,
    ttl_seconds: int = 60,
    max_payload_bytes: int = 1_024,
) -> ModelReadCache[CachedEntity]:
    return ModelReadCache(
        backend=backend,
        model_type=CachedEntity,
        ttl_seconds=ttl_seconds,
        max_payload_bytes=max_payload_bytes,
    )


@pytest.mark.asyncio
async def test_round_trip_and_ttl_expiry(redis_cache: RedisCacheHarness) -> None:
    key = _key()

    miss = await redis_cache.cache.lookup(key)
    assert miss.generation is not None
    assert not miss.is_hit

    await redis_cache.cache.store(key, miss, b"cached entity", ttl_seconds=1)
    hit = await redis_cache.cache.lookup(key)
    assert hit.is_hit
    assert hit.payload == b"cached entity"
    assert hit.generation == miss.generation
    assert hit.remaining_ttl_seconds is not None
    assert 0 < hit.remaining_ttl_seconds <= 1

    await asyncio.sleep(1.1)
    expired = await redis_cache.cache.lookup(key)
    assert not expired.is_hit
    assert expired.generation == miss.generation
    assert expired.remaining_ttl_seconds is None


@pytest.mark.asyncio
async def test_generation_metadata_expires_and_tracks_response_lifetime(
    redis_cache: RedisCacheHarness,
) -> None:
    """Inactive projects cannot leave permanent generation metadata in Redis."""
    cache = RedisReadCache(
        client=redis_cache.client,
        namespace=redis_cache.namespace,
        prefix=redis_cache.prefix,
        generation_ttl_seconds=1,
    )
    long_lived_key = _key(request="long-lived-generation")
    redis_keys = redis_read_cache_keys(
        prefix=redis_cache.prefix,
        namespace=redis_cache.namespace,
        key=long_lived_key,
    )

    # Existing deployments may already have persistent generation keys. A
    # lookup migrates them to bounded metadata without changing the token.
    legacy_generation = b"0" * 32
    await redis_cache.client.set(redis_keys.generation_key, legacy_generation)
    lookup = await cache.lookup(long_lived_key)
    assert lookup.generation == legacy_generation.decode("ascii")
    assert await redis_cache.client.pttl(redis_keys.generation_key) > 0

    stored = await cache.store(
        long_lived_key,
        lookup,
        b"long-lived payload",
        ttl_seconds=3,
    )
    generation_ttl = await redis_cache.client.pttl(redis_keys.generation_key)
    data_ttl = await redis_cache.client.pttl(redis_keys.data_key)
    assert stored is ReadCacheStoreStatus.stored
    assert generation_ttl >= data_ttl > 2_000

    short_lived_key = _key(request="short-lived-generation")
    short_lookup = await cache.lookup(short_lived_key)
    await cache.store(
        short_lived_key,
        short_lookup,
        b"short-lived payload",
        ttl_seconds=1,
    )
    assert await redis_cache.client.pttl(redis_keys.generation_key) > 2_000

    generation_before_invalidation = await redis_cache.client.get(redis_keys.generation_key)
    await cache.invalidate_project(PROJECT_ID)
    generation_after_invalidation = await redis_cache.client.get(redis_keys.generation_key)
    invalidated_ttl = await redis_cache.client.pttl(redis_keys.generation_key)
    assert generation_before_invalidation is not None
    assert isinstance(generation_after_invalidation, bytes)
    assert generation_after_invalidation != generation_before_invalidation
    assert 0 < invalidated_ttl <= 1_000

    await asyncio.sleep(1.1)
    assert not await redis_cache.client.exists(redis_keys.generation_key)
    after_expiry = await cache.lookup(long_lived_key)
    assert not after_expiry.is_hit
    assert after_expiry.generation != generation_after_invalidation.decode("ascii")


@pytest.mark.asyncio
async def test_cache_identity_isolates_every_key_dimension(
    redis_cache: RedisCacheHarness,
) -> None:
    key = _key()
    miss = await redis_cache.cache.lookup(key)
    await redis_cache.cache.store(key, miss, b"only this key", ttl_seconds=60)

    variants = [
        _key(project_id=OTHER_PROJECT_ID),
        _key(operation=ReadCacheOperation.resolve),
        _key(request="entity-2"),
    ]
    for variant in variants:
        assert not (await redis_cache.cache.lookup(variant)).is_hit

    other_namespace = RedisReadCache(
        client=redis_cache.client,
        namespace="another-tenant",
        prefix=redis_cache.prefix,
    )
    assert not (await other_namespace.lookup(key)).is_hit
    await other_namespace.invalidate_project(key.project_id)
    assert (await redis_cache.cache.lookup(key)).payload == b"only this key"


@pytest.mark.asyncio
async def test_project_invalidation_rejects_a_concurrent_stale_fill(
    redis_cache: RedisCacheHarness,
) -> None:
    project_key = _key()
    other_project_key = _key(project_id=OTHER_PROJECT_ID)

    stale_lookup = await redis_cache.cache.lookup(project_key)
    other_lookup = await redis_cache.cache.lookup(other_project_key)
    other_status = await redis_cache.cache.store(
        other_project_key,
        other_lookup,
        b"other project",
        ttl_seconds=60,
    )

    invalidation_status = await redis_cache.cache.invalidate_project(project_key.project_id)
    current_lookup = await redis_cache.cache.lookup(project_key)
    current_status = await redis_cache.cache.store(
        project_key,
        current_lookup,
        b"current fill",
        ttl_seconds=60,
    )
    stale_status = await redis_cache.cache.store(
        project_key,
        stale_lookup,
        b"stale fill",
        ttl_seconds=60,
    )

    assert other_status is ReadCacheStoreStatus.stored
    assert invalidation_status is ReadCacheInvalidationStatus.invalidated
    assert current_status is ReadCacheStoreStatus.stored
    assert stale_status is ReadCacheStoreStatus.superseded
    assert (await redis_cache.cache.lookup(project_key)).payload == b"current fill"
    assert (await redis_cache.cache.lookup(other_project_key)).payload == b"other project"


@pytest.mark.asyncio
async def test_invalidation_context_invalidates_real_generation_on_exit(
    redis_cache: RedisCacheHarness,
) -> None:
    key = _key(request="context-normal-exit")
    await redis_cache.cache.lookup(key)
    generation_key = redis_read_cache_generation_key(
        prefix=redis_cache.prefix,
        namespace=redis_cache.namespace,
        project_id=PROJECT_ID,
    )
    generation_before = await redis_cache.client.get(generation_key)

    async with invalidate_cache(redis_cache.cache, PROJECT_ID):
        assert await redis_cache.client.get(generation_key) == generation_before

    generation_after = await redis_cache.client.get(generation_key)
    assert generation_before is not None
    assert generation_after is not None
    assert generation_after != generation_before


@pytest.mark.asyncio
async def test_invalidation_context_invalidates_real_generation_after_body_error(
    redis_cache: RedisCacheHarness,
) -> None:
    key = _key(request="context-error-exit")
    await redis_cache.cache.lookup(key)
    generation_key = redis_read_cache_generation_key(
        prefix=redis_cache.prefix,
        namespace=redis_cache.namespace,
        project_id=PROJECT_ID,
    )
    generation_before = await redis_cache.client.get(generation_key)

    with pytest.raises(RuntimeError, match="operation failed"):
        async with invalidate_cache(redis_cache.cache, PROJECT_ID):
            raise RuntimeError("operation failed")

    generation_after = await redis_cache.client.get(generation_key)
    assert generation_before is not None
    assert generation_after is not None
    assert generation_after != generation_before


@pytest.mark.asyncio
async def test_cancelled_invalidation_records_real_redis_cleanup_failure(
    redis_cache: RedisCacheHarness,
) -> None:
    """Preserve cancellation while attaching a failure from the shielded cleanup."""

    class FailingAfterRealInvalidation(RedisReadCache):
        def __init__(self) -> None:
            super().__init__(
                client=redis_cache.client,
                namespace=redis_cache.namespace,
                prefix=redis_cache.prefix,
            )
            self.invalidation_started = asyncio.Event()
            self.release_invalidation = asyncio.Event()

        @override
        async def invalidate_project(self, project_id: str) -> ReadCacheInvalidationStatus:
            self.invalidation_started.set()
            await self.release_invalidation.wait()
            await super().invalidate_project(project_id)
            raise ReadCacheDataError("cleanup failed after real invalidation")

    key = _key(request="cancelled-cleanup-failure")
    await redis_cache.cache.lookup(key)
    generation_key = redis_read_cache_generation_key(
        prefix=redis_cache.prefix,
        namespace=redis_cache.namespace,
        project_id=PROJECT_ID,
    )
    generation_before = await redis_cache.client.get(generation_key)
    cache = FailingAfterRealInvalidation()
    invalidation = asyncio.create_task(finish_project_read_cache_invalidation(cache, PROJECT_ID))

    await cache.invalidation_started.wait()
    invalidation.cancel()
    await asyncio.sleep(0)
    assert not invalidation.done()
    cache.release_invalidation.set()

    with pytest.raises(asyncio.CancelledError) as exc_info:
        await invalidation

    generation_after = await redis_cache.client.get(generation_key)
    assert generation_before is not None
    assert generation_after is not None
    assert generation_after != generation_before
    assert any(
        "cleanup failed after real invalidation" in note
        for note in getattr(exc_info.value, "__notes__", ())
    )


@pytest.mark.asyncio
async def test_child_cancelled_after_real_invalidation_is_recorded(
    redis_cache: RedisCacheHarness,
) -> None:
    """A cancelled cleanup task remains visible on the propagated cancellation."""

    class CancellingAfterRealInvalidation(RedisReadCache):
        @override
        async def invalidate_project(self, project_id: str) -> ReadCacheInvalidationStatus:
            await super().invalidate_project(project_id)
            raise asyncio.CancelledError("cleanup task cancelled")

    key = _key(request="child-cancelled-after-invalidation")
    await redis_cache.cache.lookup(key)
    generation_key = redis_read_cache_generation_key(
        prefix=redis_cache.prefix,
        namespace=redis_cache.namespace,
        project_id=PROJECT_ID,
    )
    generation_before = await redis_cache.client.get(generation_key)
    cache = CancellingAfterRealInvalidation(
        client=redis_cache.client,
        namespace=redis_cache.namespace,
        prefix=redis_cache.prefix,
    )

    with pytest.raises(asyncio.CancelledError) as exc_info:
        await finish_project_read_cache_invalidation(cache, PROJECT_ID)

    generation_after = await redis_cache.client.get(generation_key)
    assert generation_before is not None
    assert generation_after is not None
    assert generation_after != generation_before
    assert any(
        "read-cache invalidation task was cancelled" in note
        for note in getattr(exc_info.value, "__notes__", ())
    )


@pytest.mark.asyncio
async def test_lost_generation_key_cannot_revive_old_data(
    redis_cache: RedisCacheHarness,
) -> None:
    key = _key()
    miss = await redis_cache.cache.lookup(key)
    await redis_cache.cache.store(key, miss, b"old data", ttl_seconds=60)
    redis_keys = redis_read_cache_keys(
        prefix=redis_cache.prefix,
        namespace=redis_cache.namespace,
        key=key,
    )

    await redis_cache.client.delete(redis_keys.generation_key)
    after_eviction = await redis_cache.cache.lookup(key)

    assert not after_eviction.is_hit
    assert after_eviction.generation != miss.generation


@pytest.mark.asyncio
async def test_invalidation_leaves_unrelated_redis_data_untouched(
    redis_cache: RedisCacheHarness,
) -> None:
    unrelated_key = f"unrelated:{uuid4().hex}"
    await redis_cache.client.set(unrelated_key, b"keep")
    try:
        await redis_cache.cache.invalidate_project(PROJECT_ID)
        assert await redis_cache.client.get(unrelated_key) == b"keep"
    finally:
        await redis_cache.client.delete(unrelated_key)


@pytest.mark.asyncio
async def test_corrupt_redis_values_fail_fast(redis_cache: RedisCacheHarness) -> None:
    key = _key()
    lookup = await redis_cache.cache.lookup(key)
    redis_keys = redis_read_cache_keys(
        prefix=redis_cache.prefix,
        namespace=redis_cache.namespace,
        key=key,
    )

    await redis_cache.client.set(
        redis_keys.data_key,
        lookup.generation.encode("ascii") + b"\npersistent payload",
    )
    with pytest.raises(ReadCacheDataError, match="has no expiration"):
        await redis_cache.cache.lookup(key)

    await redis_cache.client.set(redis_keys.data_key, b"missing envelope")
    with pytest.raises(ReadCacheDataError, match="invalid generation envelope"):
        await redis_cache.cache.lookup(key)

    await redis_cache.client.set(redis_keys.data_key, b"\npayload")
    with pytest.raises(ReadCacheDataError, match="invalid generation envelope"):
        await redis_cache.cache.lookup(key)

    await redis_cache.client.set(redis_keys.generation_key, b"\xff")
    with pytest.raises(ReadCacheDataError, match="invalid generation token"):
        await redis_cache.cache.lookup(key)

    await redis_cache.client.set(redis_keys.generation_key, b"abcd")
    with pytest.raises(ReadCacheDataError, match="invalid generation token"):
        await redis_cache.cache.lookup(key)


@pytest.mark.asyncio
async def test_decode_responses_client_remains_compatible(redis_url: str) -> None:
    prefix = f"bm:test:read:{uuid4().hex}"
    client = Redis.from_url(redis_url, decode_responses=True)
    cache = RedisReadCache(
        client=client,
        namespace="decoded-client",
        prefix=prefix,
    )
    key = _key()
    try:
        miss = await cache.lookup(key)
        await cache.store(key, miss, b"text payload", ttl_seconds=60)
        assert (await cache.lookup(key)).payload == b"text payload"
    finally:
        keys = [key async for key in client.scan_iter(match=f"{prefix}:*")]
        if keys:
            await client.delete(*keys)
        await client.aclose()


@pytest.mark.asyncio
async def test_unavailable_redis_is_explicit_for_every_operation() -> None:
    with socket.socket() as reserved_port:
        reserved_port.bind(("127.0.0.1", 0))
        port = reserved_port.getsockname()[1]

    client = create_redis_read_cache_client(
        f"redis://127.0.0.1:{port}/0",
        socket_timeout=0.05,
    )
    cache = RedisReadCache(client=client, namespace="unavailable")
    key = _key()
    try:
        with pytest.raises(ReadCacheUnavailable, match="lookup failed"):
            await cache.lookup(key)
        with pytest.raises(ReadCacheUnavailable, match="store failed"):
            await cache.store(
                key,
                ReadCacheLookup(generation="0" * 32),
                b"payload",
                ttl_seconds=60,
            )
        with pytest.raises(ReadCacheUnavailable, match="invalidation failed"):
            await cache.invalidate_project(key.project_id)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_real_redis_capacity_failures_are_cache_unavailable(
    redis_cache: RedisCacheHarness,
) -> None:
    """A no-eviction maxmemory error cannot fail reads or committed-write invalidation."""
    key = _key(request="capacity-limited-store")
    original_maxmemory = str((await redis_cache.client.config_get("maxmemory"))["maxmemory"])
    original_policy = str(
        (await redis_cache.client.config_get("maxmemory-policy"))["maxmemory-policy"]
    )
    await redis_cache.cache.lookup(key)
    read_cache = _model_cache(redis_cache.cache)

    try:
        await redis_cache.client.config_set("maxmemory-policy", "noeviction")
        await redis_cache.client.config_set("maxmemory", "1")

        async with read_cache.read(key=key) as cached:
            assert cached.value is None
            cached.value = CachedEntity(
                external_id="entity-1",
                title="Authoritative",
            )
            result = cached.value
        invalidation_status = await invalidate_project_read_cache(
            redis_cache.cache,
            PROJECT_ID,
        )
    finally:
        await redis_cache.client.config_set("maxmemory", original_maxmemory)
        await redis_cache.client.config_set("maxmemory-policy", original_policy)

    assert result.title == "Authoritative"
    assert invalidation_status is ReadCacheInvalidationStatus.unavailable


@pytest.mark.asyncio
async def test_typed_read_through_uses_real_cached_representation(
    redis_cache: RedisCacheHarness,
) -> None:
    loads = 0
    read_cache = _model_cache(redis_cache.cache)

    results: list[CachedEntity] = []
    for _ in range(2):
        async with read_cache.read(key=_key()) as cached:
            if cached.value is None:
                loads += 1
                cached.value = CachedEntity(
                    external_id="entity-1",
                    title="First",
                )
            results.append(cached.value)

    first, second = results
    assert first == CachedEntity(external_id="entity-1", title="First")
    assert second == first
    assert loads == 1


@pytest.mark.asyncio
async def test_typed_facades_share_backend_and_keep_model_types_local(
    redis_cache: RedisCacheHarness,
) -> None:
    entity_cache = _model_cache(redis_cache.cache)
    resolution_cache = ModelReadCache(
        backend=redis_cache.cache,
        model_type=CachedResolution,
        ttl_seconds=60,
        max_payload_bytes=1_024,
    )
    entity_key = _key(request="typed-entity")
    resolution_key = _key(
        operation=ReadCacheOperation.resolve,
        request="typed-resolution",
    )

    async with entity_cache.read(key=entity_key) as cached_entity:
        cached_entity.value = CachedEntity(external_id="entity-1", title="First")
    async with resolution_cache.read(key=resolution_key) as cached_resolution:
        cached_resolution.value = CachedResolution(
            external_id="entity-1",
            resolution_method="permalink",
        )

    async with entity_cache.read(key=entity_key) as cached_entity:
        assert cached_entity.value == CachedEntity(external_id="entity-1", title="First")
    async with resolution_cache.read(key=resolution_key) as cached_resolution:
        assert cached_resolution.value == CachedResolution(
            external_id="entity-1",
            resolution_method="permalink",
        )

    assert entity_cache.backend is redis_cache.cache
    assert resolution_cache.backend is redis_cache.cache


@pytest.mark.asyncio
async def test_typed_read_through_does_not_cache_oversize_models(
    redis_cache: RedisCacheHarness,
) -> None:
    loads = 0
    read_cache = _model_cache(redis_cache.cache, max_payload_bytes=1)

    for _ in range(2):
        async with read_cache.read(key=_key()) as cached:
            assert cached.value is None
            loads += 1
            cached.value = CachedEntity(
                external_id="entity-1",
                title="Too large",
            )

    assert loads == 2


@pytest.mark.asyncio
async def test_typed_read_through_does_not_cache_ineligible_models(
    redis_cache: RedisCacheHarness,
) -> None:
    loads = 0
    read_cache = _model_cache(redis_cache.cache)

    for _ in range(2):
        async with read_cache.read(
            key=_key(operation=ReadCacheOperation.resolve),
        ) as cached:
            assert cached.value is None
            loads += 1
            cached.cacheable = False
            cached.value = CachedEntity(
                external_id="cross-project",
                title="Other tenant",
            )

    assert loads == 2


@pytest.mark.asyncio
async def test_typed_read_through_does_not_store_after_body_error(
    redis_cache: RedisCacheHarness,
) -> None:
    key = _key(request="failed-authoritative-read")
    read_cache = _model_cache(redis_cache.cache)

    with pytest.raises(RuntimeError, match="authoritative read failed"):
        async with read_cache.read(key=key) as cached:
            cached.value = CachedEntity(
                external_id="entity-1",
                title="Must not be stored",
            )
            raise RuntimeError("authoritative read failed")

    assert not (await redis_cache.cache.lookup(key)).is_hit


@pytest.mark.asyncio
async def test_typed_read_through_rejects_invalid_cached_models(
    redis_cache: RedisCacheHarness,
) -> None:
    key = _key()
    miss = await redis_cache.cache.lookup(key)
    await redis_cache.cache.store(key, miss, b'{"wrong":"shape"}', ttl_seconds=60)
    read_cache = _model_cache(redis_cache.cache)

    with pytest.raises(ValidationError):
        async with read_cache.read(key=key):
            raise AssertionError("invalid cache data must not enter the read scope")


@pytest.mark.asyncio
async def test_typed_read_through_bypasses_unavailable_real_redis() -> None:
    with socket.socket() as reserved_port:
        reserved_port.bind(("127.0.0.1", 0))
        port = reserved_port.getsockname()[1]

    client = create_redis_read_cache_client(
        f"redis://127.0.0.1:{port}/0",
        socket_timeout=0.05,
    )
    cache = RedisReadCache(client=client, namespace="unavailable")
    read_cache = _model_cache(cache)

    try:
        async with read_cache.read(key=_key()) as cached:
            assert cached.value is None
            cached.value = CachedEntity(
                external_id="entity-1",
                title="Authoritative",
            )
            result = cached.value
    finally:
        await client.aclose()

    assert result.title == "Authoritative"


@pytest.mark.asyncio
async def test_invalidation_helper_preserves_committed_write_when_redis_is_unavailable() -> None:
    with socket.socket() as reserved_port:
        reserved_port.bind(("127.0.0.1", 0))
        port = reserved_port.getsockname()[1]

    client = create_redis_read_cache_client(
        f"redis://127.0.0.1:{port}/0",
        socket_timeout=0.05,
    )
    cache = RedisReadCache(client=client, namespace="unavailable")
    try:
        status = await invalidate_project_read_cache(cache, PROJECT_ID)
    finally:
        await client.aclose()

    assert status is ReadCacheInvalidationStatus.unavailable


@pytest.mark.asyncio
async def test_typed_read_through_returns_data_when_real_redis_store_times_out(
    redis_cache: RedisCacheHarness,
    redis_url: str,
) -> None:
    prefix = f"bm:test:read:{uuid4().hex}"
    client = create_redis_read_cache_client(redis_url, socket_timeout=0.05)
    cache = RedisReadCache(
        client=client,
        namespace="paused-store",
        prefix=prefix,
    )
    read_cache = _model_cache(cache)

    try:
        async with read_cache.read(key=_key()) as cached:
            assert cached.value is None
            await redis_cache.client.execute_command("CLIENT", "PAUSE", 200, "WRITE")
            cached.value = CachedEntity(
                external_id="entity-1",
                title="Authoritative",
            )
            result = cached.value
        assert result.title == "Authoritative"
    finally:
        await asyncio.sleep(0.25)
        keys = [key async for key in client.scan_iter(match=f"{prefix}:*")]
        if keys:
            await client.delete(*keys)
        await client.aclose()


def test_typed_read_through_validates_policy_before_loading(
    redis_cache: RedisCacheHarness,
) -> None:
    with pytest.raises(ValueError, match="ttl_seconds"):
        _model_cache(
            redis_cache.cache,
            ttl_seconds=0,
            max_payload_bytes=1,
        )
    with pytest.raises(ValueError, match="max_payload_bytes"):
        _model_cache(
            redis_cache.cache,
            ttl_seconds=1,
            max_payload_bytes=0,
        )


@pytest.mark.asyncio
async def test_typed_read_through_requires_an_authoritative_result(
    redis_cache: RedisCacheHarness,
) -> None:
    read_cache = _model_cache(redis_cache.cache)
    with pytest.raises(RuntimeError, match="exited without a result"):
        async with read_cache.read(key=_key(request="missing-authoritative-result")):
            pass


def test_key_validation_and_canonicalization() -> None:
    assert read_cache_request_digest("ab", "c") != read_cache_request_digest("a", "bc")
    assert read_cache_request_digest("same") == read_cache_request_digest("same")

    key = _key()
    assert _key(project_id=PROJECT_ID.upper()).project_id == PROJECT_ID
    generation_key = redis_read_cache_generation_key(
        prefix="bm:read:v1",
        namespace="tenant",
        project_id=key.project_id,
    )
    redis_keys = redis_read_cache_keys(
        prefix="bm:read:v1",
        namespace="tenant",
        key=key,
    )
    assert redis_keys.generation_key == generation_key
    assert f"{{{read_cache_request_digest('tenant', key.project_id)}}}" in redis_keys.data_key
    assert generation_key == redis_read_cache_generation_key(
        prefix="bm:read:v1",
        namespace="tenant",
        project_id=PROJECT_ID.upper(),
    )
    assert generation_key == redis_read_cache_generation_key(
        prefix="bm:read:v1",
        namespace=" tenant ",
        project_id=PROJECT_ID,
    )

    with pytest.raises(ValueError, match="project_id"):
        _key(project_id="")
    with pytest.raises(ValueError, match="valid UUID"):
        _key(project_id="not-a-uuid")
    with pytest.raises(ValueError, match="SHA-256"):
        ReadCacheKey(
            project_id=PROJECT_ID,
            operation=ReadCacheOperation.entity,
            request_digest="short",
        )
    with pytest.raises(ValueError, match="SHA-256"):
        ReadCacheKey(
            project_id=PROJECT_ID,
            operation=ReadCacheOperation.entity,
            request_digest="z" * 64,
        )
    with pytest.raises(ValueError, match="SHA-256"):
        ReadCacheKey(
            project_id=PROJECT_ID,
            operation=ReadCacheOperation.entity,
            request_digest=("0" * 62) + "  ",
        )
    uppercase_digest = read_cache_request_digest("uppercase").upper()
    assert (
        ReadCacheKey(
            project_id=PROJECT_ID,
            operation=ReadCacheOperation.entity,
            request_digest=uppercase_digest,
        ).request_digest
        == uppercase_digest.lower()
    )
    with pytest.raises(ValueError, match="generation"):
        ReadCacheLookup(generation="", payload=b"orphaned")
    with pytest.raises(ValueError, match="remaining_ttl_seconds"):
        ReadCacheLookup(generation="0" * 32, remaining_ttl_seconds=-0.1)
    with pytest.raises(ValueError, match="prefix"):
        redis_read_cache_generation_key(
            prefix="",
            namespace="tenant",
            project_id=PROJECT_ID,
        )
    with pytest.raises(ValueError, match="prefix"):
        redis_read_cache_generation_key(
            prefix="bad prefix",
            namespace="tenant",
            project_id=PROJECT_ID,
        )
    with pytest.raises(ValueError, match="namespace"):
        redis_read_cache_generation_key(
            prefix="bm:read:v1",
            namespace="",
            project_id=PROJECT_ID,
        )
    with pytest.raises(ValueError, match="project_id"):
        redis_read_cache_generation_key(prefix="bm:read:v1", namespace="tenant", project_id="")


@pytest.mark.asyncio
async def test_invalid_store_inputs_fail_before_redis(
    redis_cache: RedisCacheHarness,
) -> None:
    key = _key()

    with pytest.raises(ValueError, match="positive"):
        await redis_cache.cache.store(
            key,
            ReadCacheLookup(generation="0" * 32),
            b"payload",
            ttl_seconds=0,
        )
    with pytest.raises(ReadCacheDataError, match="generation token"):
        await redis_cache.cache.store(
            key,
            ReadCacheLookup(generation="invalid"),
            b"payload",
            ttl_seconds=60,
        )
    with pytest.raises(ValueError, match="namespace"):
        RedisReadCache(client=redis_cache.client, namespace="")
    with pytest.raises(ValueError, match="namespace"):
        RedisReadCache(client=redis_cache.client, namespace=" ")
    with pytest.raises(ValueError, match="generation_ttl_seconds"):
        RedisReadCache(
            client=redis_cache.client,
            namespace="tenant",
            generation_ttl_seconds=0,
        )
    with pytest.raises(ValueError, match="project_id"):
        await redis_cache.cache.invalidate_project("")


def test_required_bytes_rejects_non_string_values() -> None:
    with pytest.raises(ReadCacheDataError, match="has no expiration"):
        _remaining_ttl_seconds(-1)
    with pytest.raises(ReadCacheDataError, match="invalid cached payload TTL"):
        _remaining_ttl_seconds(-2)
    with pytest.raises(ReadCacheDataError, match="invalid cached payload TTL"):
        _remaining_ttl_seconds("1000")
    with pytest.raises(ReadCacheDataError, match="invalid test value"):
        _required_bytes(1, field="test")
    with pytest.raises(ReadCacheDataError, match="invalid cache store result"):
        _store_status(2)
