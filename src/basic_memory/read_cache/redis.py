"""Redis version store for the read cache.

Entries themselves are read and written by fastapi-redis-sdk. This module owns the
version tokens that go into entry keys, and replaces them to invalidate.

Replacing a version makes the entries under it unreadable, but Redis would keep them
until their TTL. Each stored entry is therefore recorded in an index for the versions
it was keyed under, and invalidation deletes the members of the replaced version's
index. Memory then follows the entries that can still be read, not every response
written in the last TTL.

The indexes are sorted sets scored by when each entry expires. Entries have different
TTLs (search a day, recent activity fifteen minutes) and some are never stored, so
each write trims the members whose entry is already gone; an index never lists more
than the entries a TTL could still hold.
"""

import time
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import uuid4

from redis.asyncio import Redis
from redis.exceptions import RedisError

from basic_memory.read_cache.contract import (
    PROJECT,
    ReadCacheChange,
    ReadCacheGroup,
    ReadCacheInvalidationStatus,
    ReadCacheUnavailable,
)
from basic_memory.read_cache.keys import (
    DEFAULT_READ_CACHE_PREFIX,
    entry_index_key,
    global_epoch_key,
    group_version_key,
)
from basic_memory.read_cache.policy import READ_CACHE_TTL_SECONDS

# A version absent from Redis reads as this token. Replacement tokens are random,
# so a later version can never collide with an earlier one.
_INITIAL_VERSION = "0"

# Entries popped and unlinked per round trip when retiring a project's entries, so
# one invalidation of a large project never moves its whole index in one reply.
_UNLINK_BATCH_SIZE = 500


def create_redis_read_cache_client(
    url: str,
    *,
    max_connections: int = 20,
    socket_timeout: float = 0.1,
) -> Redis:
    """Create an async Redis client whose lifecycle remains caller-owned."""
    return Redis.from_url(
        url,
        decode_responses=False,
        max_connections=max_connections,
        socket_connect_timeout=socket_timeout,
        socket_timeout=socket_timeout,
    )


def _token(value: object) -> str:
    if value is None:
        return _INITIAL_VERSION
    if isinstance(value, bytes):
        return value.decode("ascii")
    return str(value)


def _group_index_version(project_epoch: str, group_version: str) -> str:
    """Name a group's entry set by the project epoch it was filled in.

    Replacing the project epoch deletes every entry, and the next reads fill new
    sets under the new epoch, so the old sets stop growing and expire.
    """
    return f"{project_epoch}.{group_version}"


@dataclass(frozen=True, slots=True)
class EntryVersions:
    """The versions one entry key embeds: global epoch, project epoch, group.

    For a project-group read the group version is the project epoch itself.
    """

    epoch: str
    project: str
    group: str

    @property
    def token(self) -> str:
        return f"{self.epoch}.{self.project}.{self.group}"


class RedisReadCache:
    """Namespace-bound version store and invalidator.

    A managed host may reuse one client and Redis deployment across tenants. It must
    construct this lightweight adapter from trusted tenant context so the namespace
    cannot be supplied by an API caller.
    """

    def __init__(
        self,
        *,
        client: Redis,
        namespace: str,
        prefix: str = DEFAULT_READ_CACHE_PREFIX,
    ) -> None:
        namespace = namespace.strip()
        if not namespace:
            raise ValueError("read-cache namespace must not be empty")
        self.client = client
        self.namespace = namespace
        self.prefix = prefix

    def _version_key(self, project_id: str, group: ReadCacheGroup) -> str:
        return group_version_key(
            prefix=self.prefix,
            namespace=self.namespace,
            project_id=project_id,
            group=group,
        )

    def _index_key(self, project_id: str, group: ReadCacheGroup, version: str) -> str:
        return entry_index_key(
            prefix=self.prefix,
            namespace=self.namespace,
            project_id=project_id,
            group=group,
            version=version,
        )

    async def versions(self, project_id: str, group: ReadCacheGroup) -> EntryVersions:
        """The global epoch, project epoch and group version an entry key embeds."""
        keys = [global_epoch_key(prefix=self.prefix), self._version_key(project_id, PROJECT)]
        if group != PROJECT:
            keys.append(self._version_key(project_id, group))
        try:
            async with self.client.pipeline(transaction=False) as pipe:
                for key in keys:
                    pipe.get(key)
                values = await pipe.execute()
        except (RedisError, OSError) as error:
            raise ReadCacheUnavailable("Redis read-cache version lookup failed") from error
        epoch, project, *rest = (_token(value) for value in values)
        return EntryVersions(epoch=epoch, project=project, group=rest[0] if rest else project)

    async def track_entry(
        self,
        project_id: str,
        group: ReadCacheGroup,
        versions: EntryVersions,
        entry_key: str,
        ttl_seconds: int,
    ) -> None:
        """Record an entry about to be stored, so replacing its versions deletes it.

        Every entry is listed under its project epoch, because replacing that epoch
        retires every read in the project, and under its own group's version within
        that epoch. Recording happens before the store, so a response that turns out
        not to be cacheable is listed until its TTL would have ended, then trimmed.
        """
        indexes = [self._index_key(project_id, PROJECT, versions.project)]
        if group != PROJECT:
            indexes.append(
                self._index_key(
                    project_id, group, _group_index_version(versions.project, versions.group)
                )
            )
        now = time.time()
        try:
            async with self.client.pipeline(transaction=False) as pipe:
                for index in indexes:
                    pipe.zadd(index, {entry_key: now + ttl_seconds})
                    # Members whose entry has expired would otherwise stay listed for
                    # as long as later misses keep the index alive.
                    pipe.zremrangebyscore(index, "-inf", now)
                    # The index lives as long as the longest-lived entry it can list.
                    pipe.expire(index, READ_CACHE_TTL_SECONDS)
                await pipe.execute()
        except (RedisError, OSError) as error:
            raise ReadCacheUnavailable("Redis read-cache entry tracking failed") from error

    async def invalidate(
        self,
        project_id: str,
        change: ReadCacheChange,
    ) -> ReadCacheInvalidationStatus:
        groups = change.groups
        if not groups:
            return ReadCacheInvalidationStatus.invalidated
        project_epoch_key = self._version_key(project_id, PROJECT)
        try:
            # --- Replace versions ---
            # Readers switch to the new keys as soon as this lands, so the entries
            # deleted below can no longer be served.
            async with self.client.pipeline(transaction=False) as pipe:
                # Group sets live under the project epoch they were filled in, so a
                # group's retired entries are found in the set for the current epoch.
                pipe.get(project_epoch_key)
                for group in groups:
                    # The version outlives every entry keyed under it, so when it
                    # expires back to the initial token no entry from before its
                    # replacement can still be read.
                    pipe.set(
                        self._version_key(project_id, group),
                        uuid4().hex,
                        ex=READ_CACHE_TTL_SECONDS,
                        get=True,
                    )
                current_project, *replaced = await pipe.execute()

            # --- Delete the retired entries ---
            if change.project:
                # Every entry in the project is listed under its epoch. The group sets
                # filled under that epoch are no longer added to and expire on their
                # own, so they are not looked up.
                await self._delete_indexed_entries(
                    [self._index_key(project_id, PROJECT, _token(replaced[0]))]
                )
            else:
                project_epoch = _token(current_project)
                await self._delete_indexed_entries(
                    [
                        self._index_key(
                            project_id, group, _group_index_version(project_epoch, _token(previous))
                        )
                        for group, previous in zip(groups, replaced, strict=True)
                    ],
                    also_listed_in=self._index_key(project_id, PROJECT, project_epoch),
                )
        except (RedisError, OSError) as error:
            raise ReadCacheUnavailable("Redis read-cache invalidation failed") from error
        return ReadCacheInvalidationStatus.invalidated

    async def _delete_indexed_entries(
        self, indexes: Sequence[str], *, also_listed_in: str | None = None
    ) -> None:
        """Delete every entry listed in ``indexes``, emptying them (Redis drops an empty index).

        ``also_listed_in`` is the project index that lists the same entries. Their keys
        are removed from it too, so a project whose notes change but whose epoch
        never does keeps an index the size of its live entries.

        A reader that looked up the old version just before it was replaced can
        still store its entry after this runs. That entry is never read again and
        expires with its TTL, as every retired entry did before entries were tracked.
        """
        for index in indexes:
            # Pop the index a batch at a time so no single reply carries a large
            # project's whole index. Popped members leave the index, so an
            # interrupted run leaves the rest listed rather than half-deleted.
            while popped := await self._pop_index_batch(index):
                batch = [member for member, _expires_at in popped]
                async with self.client.pipeline(transaction=False) as pipe:
                    pipe.unlink(*batch)
                    if also_listed_in is not None:
                        pipe.zrem(also_listed_in, *batch)
                    await pipe.execute()

    async def _pop_index_batch(self, index: str) -> list[tuple[bytes, float]]:
        """Remove and return up to one batch of an index's members."""
        async with self.client.pipeline(transaction=False) as pipe:
            pipe.zpopmin(index, _UNLINK_BATCH_SIZE)
            (popped,) = await pipe.execute()
        return popped

    async def invalidate_project(self, project_id: str) -> ReadCacheInvalidationStatus:
        return await self.invalidate(project_id, ReadCacheChange.whole_project())

    async def reset_all(self) -> None:
        """Retire every entry for every tenant, e.g. after a deploy changes responses.

        The global epoch has no expiry, so it can never fall back to a value an
        older entry was keyed under. Entries keyed under the old epoch are left to
        their TTL: a reset is rare, and finding them would mean scanning every
        tenant's keys.
        """
        try:
            await self.client.set(global_epoch_key(prefix=self.prefix), uuid4().hex)
        except (RedisError, OSError) as error:
            raise ReadCacheUnavailable("Redis read-cache reset failed") from error
