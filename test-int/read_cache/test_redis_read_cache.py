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
    NullReadCache,
    ReadCacheDataError,
    ReadCacheInvalidationStatus,
    ReadCacheKey,
    ReadCacheLookup,
    ReadCacheOperation,
    ReadCacheStoreStatus,
    ReadCacheUnavailable,
    invalidate_project_read_cache,
    read_cache_request_digest,
    read_through_model,
)
from basic_memory.read_cache.keys import (
    redis_read_cache_generation_key,
    redis_read_cache_keys,
)
from basic_memory.read_cache.redis import (
    RedisReadCache,
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

    await asyncio.sleep(1.1)
    expired = await redis_cache.cache.lookup(key)
    assert not expired.is_hit
    assert expired.generation == miss.generation


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

    await redis_cache.client.delete(redis_keys.generation)
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
    await redis_cache.cache.lookup(key)
    redis_keys = redis_read_cache_keys(
        prefix=redis_cache.prefix,
        namespace=redis_cache.namespace,
        key=key,
    )

    await redis_cache.client.set(redis_keys.data, b"missing envelope")
    with pytest.raises(ReadCacheDataError, match="invalid generation envelope"):
        await redis_cache.cache.lookup(key)

    await redis_cache.client.set(redis_keys.data, b"\npayload")
    with pytest.raises(ReadCacheDataError, match="invalid generation envelope"):
        await redis_cache.cache.lookup(key)

    await redis_cache.client.set(redis_keys.generation, b"\xff")
    with pytest.raises(ReadCacheDataError, match="invalid generation token"):
        await redis_cache.cache.lookup(key)

    await redis_cache.client.set(redis_keys.generation, b"abcd")
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

    async def load() -> CachedEntity:
        return CachedEntity(external_id="entity-1", title="Authoritative")

    try:
        await redis_cache.client.config_set("maxmemory-policy", "noeviction")
        await redis_cache.client.config_set("maxmemory", "1")

        result = await read_through_model(
            cache=redis_cache.cache,
            key=key,
            model_type=CachedEntity,
            load=load,
            ttl_seconds=60,
            max_payload_bytes=1_024,
        )
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
async def test_null_cache_preserves_disabled_semantics() -> None:
    class StoreMustNotRun(NullReadCache):
        @override
        async def store(
            self,
            key: ReadCacheKey,
            lookup: ReadCacheLookup,
            payload: bytes,
            *,
            ttl_seconds: int,
        ) -> ReadCacheStoreStatus:
            raise AssertionError("disabled read-through must skip serialization and storage")

    cache = StoreMustNotRun()
    key = _key()
    lookup = await cache.lookup(key)

    assert lookup == ReadCacheLookup(generation=None)
    store_status = await NullReadCache().store(key, lookup, b"ignored", ttl_seconds=60)
    assert store_status is ReadCacheStoreStatus.disabled
    status = await cache.invalidate_project(key.project_id)
    assert status is ReadCacheInvalidationStatus.disabled

    async def load() -> CachedEntity:
        return CachedEntity(external_id="entity-1", title="Authoritative")

    result = await read_through_model(
        cache=cache,
        key=key,
        model_type=CachedEntity,
        load=load,
        ttl_seconds=60,
        max_payload_bytes=1_024,
    )
    assert result.title == "Authoritative"


@pytest.mark.asyncio
async def test_typed_read_through_uses_real_cached_representation(
    redis_cache: RedisCacheHarness,
) -> None:
    loads = 0

    async def load() -> CachedEntity:
        nonlocal loads
        loads += 1
        return CachedEntity(external_id="entity-1", title="First")

    first = await read_through_model(
        cache=redis_cache.cache,
        key=_key(),
        model_type=CachedEntity,
        load=load,
        ttl_seconds=60,
        max_payload_bytes=1_024,
    )
    second = await read_through_model(
        cache=redis_cache.cache,
        key=_key(),
        model_type=CachedEntity,
        load=load,
        ttl_seconds=60,
        max_payload_bytes=1_024,
    )

    assert first == CachedEntity(external_id="entity-1", title="First")
    assert second == first
    assert loads == 1


@pytest.mark.asyncio
async def test_typed_read_through_does_not_cache_oversize_models(
    redis_cache: RedisCacheHarness,
) -> None:
    loads = 0

    async def load() -> CachedEntity:
        nonlocal loads
        loads += 1
        return CachedEntity(external_id="entity-1", title="Too large")

    for _ in range(2):
        await read_through_model(
            cache=redis_cache.cache,
            key=_key(),
            model_type=CachedEntity,
            load=load,
            ttl_seconds=60,
            max_payload_bytes=1,
        )

    assert loads == 2


@pytest.mark.asyncio
async def test_typed_read_through_does_not_cache_ineligible_models(
    redis_cache: RedisCacheHarness,
) -> None:
    loads = 0

    async def load() -> CachedEntity:
        nonlocal loads
        loads += 1
        return CachedEntity(external_id="cross-project", title="Other tenant")

    for _ in range(2):
        await read_through_model(
            cache=redis_cache.cache,
            key=_key(operation=ReadCacheOperation.resolve),
            model_type=CachedEntity,
            load=load,
            ttl_seconds=60,
            max_payload_bytes=1_024,
            should_store=lambda entity: entity.external_id != "cross-project",
        )

    assert loads == 2


@pytest.mark.asyncio
async def test_typed_read_through_rejects_invalid_cached_models(
    redis_cache: RedisCacheHarness,
) -> None:
    key = _key()
    miss = await redis_cache.cache.lookup(key)
    await redis_cache.cache.store(key, miss, b'{"wrong":"shape"}', ttl_seconds=60)

    async def load() -> CachedEntity:
        raise AssertionError("invalid cache data must not fall through to the loader")

    with pytest.raises(ValidationError):
        await read_through_model(
            cache=redis_cache.cache,
            key=key,
            model_type=CachedEntity,
            load=load,
            ttl_seconds=60,
            max_payload_bytes=1_024,
        )


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

    async def load() -> CachedEntity:
        return CachedEntity(external_id="entity-1", title="Authoritative")

    try:
        result = await read_through_model(
            cache=cache,
            key=_key(),
            model_type=CachedEntity,
            load=load,
            ttl_seconds=60,
            max_payload_bytes=1_024,
        )
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

    async def load() -> CachedEntity:
        await redis_cache.client.execute_command("CLIENT", "PAUSE", 200, "WRITE")
        return CachedEntity(external_id="entity-1", title="Authoritative")

    try:
        result = await read_through_model(
            cache=cache,
            key=_key(),
            model_type=CachedEntity,
            load=load,
            ttl_seconds=60,
            max_payload_bytes=1_024,
        )
        assert result.title == "Authoritative"
    finally:
        await asyncio.sleep(0.25)
        keys = [key async for key in client.scan_iter(match=f"{prefix}:*")]
        if keys:
            await client.delete(*keys)
        await client.aclose()


@pytest.mark.asyncio
async def test_typed_read_through_validates_policy_before_loading() -> None:
    async def load() -> CachedEntity:
        raise AssertionError("invalid policy must fail before loading")

    with pytest.raises(ValueError, match="ttl_seconds"):
        await read_through_model(
            cache=NullReadCache(),
            key=_key(),
            model_type=CachedEntity,
            load=load,
            ttl_seconds=0,
            max_payload_bytes=1,
        )
    with pytest.raises(ValueError, match="max_payload_bytes"):
        await read_through_model(
            cache=NullReadCache(),
            key=_key(),
            model_type=CachedEntity,
            load=load,
            ttl_seconds=1,
            max_payload_bytes=0,
        )


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
    assert redis_keys.generation == generation_key
    assert f"{{{read_cache_request_digest('tenant', key.project_id)}}}" in redis_keys.data
    assert generation_key == redis_read_cache_generation_key(
        prefix="bm:read:v1",
        namespace="tenant",
        project_id=PROJECT_ID.upper(),
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

    with pytest.raises(ValueError, match="lookup generation"):
        await redis_cache.cache.store(
            key,
            ReadCacheLookup(generation=None),
            b"payload",
            ttl_seconds=60,
        )
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
    with pytest.raises(ValueError, match="project_id"):
        await redis_cache.cache.invalidate_project("")


def test_required_bytes_rejects_non_string_values() -> None:
    with pytest.raises(ReadCacheDataError, match="invalid test value"):
        _required_bytes(1, field="test")
    with pytest.raises(ReadCacheDataError, match="invalid cache store result"):
        _store_status(2)
