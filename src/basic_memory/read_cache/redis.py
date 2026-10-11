"""Redis version store for the read cache.

Entries themselves are read and written by fastapi-redis-sdk. This module owns the
version tokens that go into entry keys, and replaces them to invalidate.
"""

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
    global_epoch_key,
    group_version_key,
)
from basic_memory.read_cache.policy import READ_CACHE_TTL_SECONDS

# A version absent from Redis reads as this token. Replacement tokens are random,
# so a later version can never collide with an earlier one.
_INITIAL_VERSION = "0"


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

    async def versions(self, project_id: str, group: ReadCacheGroup) -> str:
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
        return ".".join(_token(value) for value in values)

    async def invalidate(
        self,
        project_id: str,
        change: ReadCacheChange,
    ) -> ReadCacheInvalidationStatus:
        groups = change.groups
        if not groups:
            return ReadCacheInvalidationStatus.invalidated
        try:
            async with self.client.pipeline(transaction=False) as pipe:
                for group in groups:
                    # The version outlives every entry keyed under it, so when it
                    # expires back to the initial token no entry from before its
                    # replacement can still be read.
                    pipe.set(
                        self._version_key(project_id, group),
                        uuid4().hex,
                        ex=READ_CACHE_TTL_SECONDS,
                    )
                await pipe.execute()
        except (RedisError, OSError) as error:
            raise ReadCacheUnavailable("Redis read-cache invalidation failed") from error
        return ReadCacheInvalidationStatus.invalidated

    async def invalidate_project(self, project_id: str) -> ReadCacheInvalidationStatus:
        return await self.invalidate(project_id, ReadCacheChange.whole_project())

    async def reset_all(self) -> None:
        """Retire every entry for every tenant, e.g. after a deploy changes responses.

        The global epoch has no expiry, so it can never fall back to a value an
        older entry was keyed under.
        """
        try:
            await self.client.set(global_epoch_key(prefix=self.prefix), uuid4().hex)
        except (RedisError, OSError) as error:
            raise ReadCacheUnavailable("Redis read-cache reset failed") from error
