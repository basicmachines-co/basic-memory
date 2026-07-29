"""No-op cache used by default local-first installations."""

from basic_memory.read_cache.contract import (
    ReadCacheInvalidationStatus,
    ReadCacheKey,
    ReadCacheLookup,
    ReadCacheStoreStatus,
)


class NullReadCache:
    """A disabled cache implementation with no external dependencies."""

    async def lookup(self, key: ReadCacheKey) -> ReadCacheLookup:
        return ReadCacheLookup(generation=None)

    async def store(
        self,
        key: ReadCacheKey,
        lookup: ReadCacheLookup,
        payload: bytes,
        *,
        ttl_seconds: int,
    ) -> ReadCacheStoreStatus:
        return ReadCacheStoreStatus.disabled

    async def invalidate_project(self, project_id: str) -> ReadCacheInvalidationStatus:
        return ReadCacheInvalidationStatus.disabled
