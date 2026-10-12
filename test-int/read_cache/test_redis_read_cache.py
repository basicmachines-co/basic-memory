"""Integration coverage for the Redis read-cache version store and invalidation helpers.

Entries are read and written by fastapi-redis-sdk; this suite covers what Basic Memory
owns: the group version tokens embedded in every entry key, and the helpers that
replace them after committed changes.
"""

from __future__ import annotations

import asyncio
import socket
from typing import Protocol, override
from uuid import uuid4

import pytest
from redis.asyncio import Redis

from basic_memory.read_cache import (
    CONTENT,
    PROJECT,
    NoteGroup,
    ReadCacheChange,
    ReadCacheInvalidationStatus,
    ReadCacheUnavailable,
    finish_project_read_cache_invalidation,
    invalidate_cache,
    invalidate_project_read_cache,
    invalidate_read_cache,
    invalidate_recorded_change,
)
from basic_memory.read_cache.keys import entry_index_key, global_epoch_key, group_version_key
from basic_memory.read_cache.policy import READ_CACHE_TTL_SECONDS
from basic_memory.read_cache.redis import (
    EntryVersions,
    RedisReadCache,
    create_redis_read_cache_client,
)


class RedisCacheHarness(Protocol):
    """Structural type for the real Redis fixture."""

    cache: RedisReadCache
    client: Redis
    namespace: str
    prefix: str


pytestmark = pytest.mark.redis

PROJECT_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
OTHER_PROJECT_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
NOTE_A = "11111111-1111-4111-8111-111111111111"
NOTE_B = "22222222-2222-4222-8222-222222222222"


def _unreachable_cache() -> RedisReadCache:
    with socket.socket() as reserved_port:
        reserved_port.bind(("127.0.0.1", 0))
        port = reserved_port.getsockname()[1]
    client = create_redis_read_cache_client(f"redis://127.0.0.1:{port}/0", socket_timeout=0.05)
    return RedisReadCache(client=client, namespace="unavailable")


async def _all_versions(
    cache: RedisReadCache, project_id: str = PROJECT_ID
) -> dict[str, EntryVersions]:
    return {
        "a": await cache.versions(project_id, NoteGroup(NOTE_A)),
        "b": await cache.versions(project_id, NoteGroup(NOTE_B)),
        "content": await cache.versions(project_id, CONTENT),
        "project": await cache.versions(project_id, PROJECT),
    }


def _unchanged(
    before: dict[str, EntryVersions], after: dict[str, EntryVersions], *names: str
) -> bool:
    return all(after[name] == before[name] for name in names)


# --- Version tokens ---


@pytest.mark.asyncio
async def test_a_note_change_moves_only_that_notes_version(redis_cache: RedisCacheHarness) -> None:
    before = await _all_versions(redis_cache.cache)

    await redis_cache.cache.invalidate(PROJECT_ID, ReadCacheChange(notes=frozenset({NOTE_A})))
    after = await _all_versions(redis_cache.cache)

    assert after["a"] != before["a"]
    assert _unchanged(before, after, "b", "content", "project")


@pytest.mark.asyncio
async def test_a_body_change_moves_its_notes_and_content(redis_cache: RedisCacheHarness) -> None:
    before = await _all_versions(redis_cache.cache)

    await redis_cache.cache.invalidate(PROJECT_ID, ReadCacheChange.note_content({NOTE_A}))
    after = await _all_versions(redis_cache.cache)

    assert after["a"] != before["a"]
    assert after["content"] != before["content"]
    assert _unchanged(before, after, "b", "project")


@pytest.mark.asyncio
async def test_whole_project_invalidation_moves_every_group(redis_cache: RedisCacheHarness) -> None:
    before = await _all_versions(redis_cache.cache)

    status = await redis_cache.cache.invalidate_project(PROJECT_ID)
    after = await _all_versions(redis_cache.cache)

    assert status is ReadCacheInvalidationStatus.invalidated
    assert all(after[name] != before[name] for name in before)


@pytest.mark.asyncio
async def test_invalidation_is_isolated_by_project_and_namespace(
    redis_cache: RedisCacheHarness,
) -> None:
    other_tenant = RedisReadCache(
        client=redis_cache.client,
        namespace=f"other-{uuid4().hex}",
        prefix=redis_cache.prefix,
    )
    other_project_before = await _all_versions(redis_cache.cache, OTHER_PROJECT_ID)
    other_tenant_before = await _all_versions(other_tenant)

    await redis_cache.cache.invalidate_project(PROJECT_ID)

    assert await _all_versions(redis_cache.cache, OTHER_PROJECT_ID) == other_project_before
    assert await _all_versions(other_tenant) == other_tenant_before


@pytest.mark.asyncio
async def test_reset_all_moves_every_tenants_versions(redis_cache: RedisCacheHarness) -> None:
    other_tenant = RedisReadCache(
        client=redis_cache.client,
        namespace=f"other-{uuid4().hex}",
        prefix=redis_cache.prefix,
    )
    mine_before = await _all_versions(redis_cache.cache)
    theirs_before = await _all_versions(other_tenant)

    await redis_cache.cache.reset_all()

    mine_after = await _all_versions(redis_cache.cache)
    theirs_after = await _all_versions(other_tenant)
    assert all(mine_after[name] != mine_before[name] for name in mine_before)
    assert all(theirs_after[name] != theirs_before[name] for name in theirs_before)


@pytest.mark.asyncio
async def test_versions_outlive_entries_and_the_global_epoch_never_expires(
    redis_cache: RedisCacheHarness,
) -> None:
    """A version that expired back to its initial token could revive older entries."""
    await redis_cache.cache.invalidate(PROJECT_ID, ReadCacheChange.note_content({NOTE_A}))
    await redis_cache.cache.reset_all()

    note_ttl = await redis_cache.client.ttl(
        group_version_key(
            prefix=redis_cache.prefix,
            namespace=redis_cache.namespace,
            project_id=PROJECT_ID,
            group=NoteGroup(NOTE_A),
        )
    )
    epoch_ttl = await redis_cache.client.ttl(global_epoch_key(prefix=redis_cache.prefix))

    assert READ_CACHE_TTL_SECONDS - 5 <= note_ttl <= READ_CACHE_TTL_SECONDS
    assert epoch_ttl == -1


@pytest.mark.asyncio
async def test_an_index_lists_an_entry_only_until_its_own_ttl_ends(
    redis_cache: RedisCacheHarness,
) -> None:
    """Index members expire with their entries, not with the index.

    Later misses keep the index alive, so a member listed for the index's lifetime
    would outlive a short-TTL or never-stored entry indefinitely. A one-second TTL
    stands in for the fifteen-minute recent-activity TTL.
    """
    cache = redis_cache.cache
    versions = await cache.versions(PROJECT_ID, CONTENT)
    project_index = entry_index_key(
        prefix=redis_cache.prefix,
        namespace=redis_cache.namespace,
        project_id=PROJECT_ID,
        group=PROJECT,
        version=versions.project,
    )

    await cache.track_entry(PROJECT_ID, CONTENT, versions, "short-lived", 1)
    await asyncio.sleep(1.1)
    await cache.track_entry(PROJECT_ID, CONTENT, versions, "long-lived", 3600)

    assert await redis_cache.client.zrange(project_index, 0, -1) == [b"long-lived"]


@pytest.mark.asyncio
async def test_project_invalidation_deletes_an_index_larger_than_one_batch(
    redis_cache: RedisCacheHarness,
) -> None:
    """Invalidation drains the whole index, a bounded batch per round trip."""
    cache = redis_cache.cache
    versions = await cache.versions(PROJECT_ID, CONTENT)
    entries = [f"{redis_cache.prefix}:entry:{number}" for number in range(1201)]
    async with redis_cache.client.pipeline(transaction=False) as pipe:
        for entry in entries:
            pipe.set(entry, b"cached", ex=3600)
        await pipe.execute()
    for entry in entries:
        await cache.track_entry(PROJECT_ID, CONTENT, versions, entry, 3600)

    await cache.invalidate_project(PROJECT_ID)

    assert await redis_cache.client.exists(*entries) == 0
    # The project index is drained; group indexes from the old epoch expire on their own.
    project_index = entry_index_key(
        prefix=redis_cache.prefix,
        namespace=redis_cache.namespace,
        project_id=PROJECT_ID,
        group=PROJECT,
        version=versions.project,
    )
    assert await redis_cache.client.exists(project_index) == 0


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
async def test_decode_responses_client_remains_compatible(redis_url: str) -> None:
    client = Redis.from_url(redis_url, decode_responses=True)
    cache = RedisReadCache(client=client, namespace=f"decoded-{uuid4().hex}")
    try:
        before = await cache.versions(PROJECT_ID, NoteGroup(NOTE_A))
        await cache.invalidate(PROJECT_ID, ReadCacheChange(notes=frozenset({NOTE_A})))
        after = await cache.versions(PROJECT_ID, NoteGroup(NOTE_A))
    finally:
        await client.aclose()

    assert before != after


# --- Invalidation helpers ---


@pytest.mark.asyncio
async def test_invalidation_context_invalidates_on_exit(redis_cache: RedisCacheHarness) -> None:
    before = await redis_cache.cache.versions(PROJECT_ID, PROJECT)

    async with invalidate_cache(redis_cache.cache, PROJECT_ID):
        assert await redis_cache.cache.versions(PROJECT_ID, PROJECT) == before

    assert await redis_cache.cache.versions(PROJECT_ID, PROJECT) != before


@pytest.mark.asyncio
async def test_invalidation_context_invalidates_after_body_error(
    redis_cache: RedisCacheHarness,
) -> None:
    before = await redis_cache.cache.versions(PROJECT_ID, PROJECT)

    with pytest.raises(RuntimeError, match="operation failed"):
        async with invalidate_cache(redis_cache.cache, PROJECT_ID):
            raise RuntimeError("operation failed")

    assert await redis_cache.cache.versions(PROJECT_ID, PROJECT) != before


@pytest.mark.asyncio
async def test_recorded_change_retires_only_what_was_recorded(
    redis_cache: RedisCacheHarness,
) -> None:
    before = await _all_versions(redis_cache.cache)

    async with invalidate_recorded_change(redis_cache.cache, PROJECT_ID) as recorder:
        recorder.record(ReadCacheChange(notes=frozenset({NOTE_A})))

    after = await _all_versions(redis_cache.cache)
    assert after["a"] != before["a"]
    assert _unchanged(before, after, "b", "content", "project")


@pytest.mark.asyncio
async def test_recorded_change_retires_the_whole_project_when_the_body_raises(
    redis_cache: RedisCacheHarness,
) -> None:
    """A raise can follow a commit the recorder never described."""
    before = await redis_cache.cache.versions(PROJECT_ID, PROJECT)

    with pytest.raises(RuntimeError, match="after commit"):
        async with invalidate_recorded_change(redis_cache.cache, PROJECT_ID) as recorder:
            recorder.record(ReadCacheChange(notes=frozenset({NOTE_A})))
            raise RuntimeError("after commit")

    assert await redis_cache.cache.versions(PROJECT_ID, PROJECT) != before


@pytest.mark.asyncio
async def test_an_empty_recorded_change_retires_nothing(redis_cache: RedisCacheHarness) -> None:
    before = await _all_versions(redis_cache.cache)

    async with invalidate_recorded_change(redis_cache.cache, PROJECT_ID):
        pass

    assert await _all_versions(redis_cache.cache) == before


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
        async def invalidate(
            self,
            project_id: str,
            change: ReadCacheChange,
        ) -> ReadCacheInvalidationStatus:
            self.invalidation_started.set()
            await self.release_invalidation.wait()
            await super().invalidate(project_id, change)
            raise RuntimeError("cleanup failed after real invalidation")

    before = await redis_cache.cache.versions(PROJECT_ID, PROJECT)
    cache = FailingAfterRealInvalidation()
    invalidation = asyncio.create_task(finish_project_read_cache_invalidation(cache, PROJECT_ID))

    await cache.invalidation_started.wait()
    invalidation.cancel()
    await asyncio.sleep(0)
    assert not invalidation.done()
    cache.release_invalidation.set()

    with pytest.raises(asyncio.CancelledError) as exc_info:
        await invalidation

    assert await redis_cache.cache.versions(PROJECT_ID, PROJECT) != before
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
        async def invalidate(
            self,
            project_id: str,
            change: ReadCacheChange,
        ) -> ReadCacheInvalidationStatus:
            await super().invalidate(project_id, change)
            raise asyncio.CancelledError("cleanup task cancelled")

    before = await redis_cache.cache.versions(PROJECT_ID, PROJECT)
    cache = CancellingAfterRealInvalidation(
        client=redis_cache.client,
        namespace=redis_cache.namespace,
        prefix=redis_cache.prefix,
    )

    with pytest.raises(asyncio.CancelledError) as exc_info:
        await finish_project_read_cache_invalidation(cache, PROJECT_ID)

    assert await redis_cache.cache.versions(PROJECT_ID, PROJECT) != before
    assert any(
        "read-cache invalidation task was cancelled" in note
        for note in getattr(exc_info.value, "__notes__", ())
    )


# --- Redis unavailable ---


@pytest.mark.asyncio
async def test_unavailable_redis_is_explicit_for_every_operation() -> None:
    cache = _unreachable_cache()
    try:
        with pytest.raises(ReadCacheUnavailable, match="version lookup failed"):
            await cache.versions(PROJECT_ID, CONTENT)
        with pytest.raises(ReadCacheUnavailable, match="invalidation failed"):
            await cache.invalidate_project(PROJECT_ID)
        with pytest.raises(ReadCacheUnavailable, match="reset failed"):
            await cache.reset_all()
    finally:
        await cache.client.aclose()


@pytest.mark.asyncio
async def test_invalidation_helper_preserves_committed_write_when_redis_is_unavailable() -> None:
    cache = _unreachable_cache()
    try:
        status = await invalidate_project_read_cache(cache, PROJECT_ID)
    finally:
        await cache.client.aclose()

    assert status is ReadCacheInvalidationStatus.unavailable


@pytest.mark.asyncio
async def test_real_redis_capacity_failures_are_cache_unavailable(
    redis_cache: RedisCacheHarness,
) -> None:
    """A no-eviction maxmemory error cannot fail committed-write invalidation."""
    original_maxmemory = str((await redis_cache.client.config_get("maxmemory"))["maxmemory"])
    original_policy = str(
        (await redis_cache.client.config_get("maxmemory-policy"))["maxmemory-policy"]
    )
    try:
        await redis_cache.client.config_set("maxmemory-policy", "noeviction")
        await redis_cache.client.config_set("maxmemory", "1")
        status = await invalidate_read_cache(
            redis_cache.cache,
            PROJECT_ID,
            ReadCacheChange.note_content({NOTE_A}),
        )
    finally:
        await redis_cache.client.config_set("maxmemory", original_maxmemory)
        await redis_cache.client.config_set("maxmemory-policy", original_policy)

    assert status is ReadCacheInvalidationStatus.unavailable


# --- Change contract ---


def test_change_groups_and_canonical_note_ids() -> None:
    assert ReadCacheChange.whole_project().groups == (PROJECT,)
    assert ReadCacheChange.note_content({NOTE_A.upper()}).groups == (CONTENT, NoteGroup(NOTE_A))
    # Under a project bump, per-group bumps would only add Redis writes.
    combined = ReadCacheChange.whole_project() | ReadCacheChange.note_content({NOTE_A})
    assert combined.groups == (PROJECT,)
    assert ReadCacheChange().is_empty
    assert ReadCacheChange().groups == ()


def test_note_ids_and_namespaces_are_validated() -> None:
    with pytest.raises(ValueError, match="valid UUID"):
        ReadCacheChange(notes=frozenset({"not-a-uuid"}))
    with pytest.raises(ValueError, match="valid UUID"):
        NoteGroup("not-a-uuid")
    with pytest.raises(ValueError, match="namespace must not be empty"):
        RedisReadCache(client=Redis(), namespace="  ")
