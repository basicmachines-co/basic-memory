"""Portable contract for best-effort semantic read caching."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from uuid import UUID


class ReadCacheOperation(StrEnum):
    """Read operations supported by the initial cache rollout."""

    entity = "entity"
    resolve = "resolve"
    resource = "resource"


class ReadCacheStoreStatus(StrEnum):
    """Outcome of one best-effort cache store."""

    stored = "stored"
    superseded = "superseded"
    disabled = "disabled"


class ReadCacheInvalidationStatus(StrEnum):
    """Outcome of one project-generation invalidation attempt."""

    invalidated = "invalidated"
    unavailable = "unavailable"
    disabled = "disabled"


def canonical_read_cache_project_id(project_id: str) -> str:
    """Return one canonical UUID spelling for project cache scope."""
    if not project_id:
        raise ValueError("read-cache project_id must not be empty")
    try:
        return str(UUID(project_id))
    except ValueError as error:
        raise ValueError("read-cache project_id must be a valid UUID") from error


@dataclass(frozen=True, slots=True)
class ReadCacheKey:
    """Project-scoped identity for one canonical read request."""

    project_id: str
    operation: ReadCacheOperation
    request_digest: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "project_id",
            canonical_read_cache_project_id(self.project_id),
        )
        if len(self.request_digest) != 64:
            raise ValueError("read-cache request_digest must be a SHA-256 hex digest")
        try:
            bytes.fromhex(self.request_digest)
        except ValueError as error:
            raise ValueError("read-cache request_digest must be a SHA-256 hex digest") from error


@dataclass(frozen=True, slots=True)
class ReadCacheLookup:
    """Cache lookup result plus the generation observed by that read.

    A missing generation means the cache implementation is disabled. Read-through
    callers can then skip serialization and the store call entirely.
    """

    generation: str | None
    payload: bytes | None = None

    @property
    def is_hit(self) -> bool:
        return self.payload is not None


class ReadCacheUnavailable(RuntimeError):
    """The cache backend could not complete an optional operation."""


class ReadCacheDataError(RuntimeError):
    """A cached value violated the Basic Memory cache encoding contract."""


class ReadCache(Protocol):
    """Best-effort read cache with project-generation invalidation."""

    async def lookup(self, key: ReadCacheKey) -> ReadCacheLookup:
        """Return a cached payload and the generation observed by this lookup."""

    async def store(
        self,
        key: ReadCacheKey,
        lookup: ReadCacheLookup,
        payload: bytes,
        *,
        ttl_seconds: int,
    ) -> ReadCacheStoreStatus:
        """Store a payload under the generation observed by ``lookup``."""

    async def invalidate_project(self, project_id: str) -> ReadCacheInvalidationStatus:
        """Make every existing cached value for one project unreachable."""
