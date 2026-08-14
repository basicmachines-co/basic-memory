"""Portable contract for best-effort semantic read caching."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from uuid import UUID


class ReadCacheOperation(StrEnum):
    """Read operations supported by the initial cache rollout."""

    directory_list = "directory_list"
    directory_structure = "directory_structure"
    directory_tree = "directory_tree"
    entity = "entity"
    resolve = "resolve"
    resource = "resource"
    search = "search"


class ReadCacheStoreStatus(StrEnum):
    """Outcome of one best-effort cache store."""

    stored = "stored"
    superseded = "superseded"


class ReadCacheInvalidationStatus(StrEnum):
    """Outcome of one project-generation invalidation attempt."""

    invalidated = "invalidated"
    unavailable = "unavailable"


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
            decoded_digest = bytes.fromhex(self.request_digest)
        except ValueError as error:
            raise ValueError("read-cache request_digest must be a SHA-256 hex digest") from error
        if len(decoded_digest) != 32:
            raise ValueError("read-cache request_digest must be a SHA-256 hex digest")
        object.__setattr__(self, "request_digest", self.request_digest.lower())


@dataclass(frozen=True, slots=True)
class ReadCacheLookup:
    """Cache lookup result plus the generation observed by that read."""

    generation: str
    payload: bytes | None = None
    remaining_ttl_seconds: float | None = None

    def __post_init__(self) -> None:
        if not self.generation:
            raise ValueError("read-cache lookup generation must not be empty")
        if self.remaining_ttl_seconds is not None and self.remaining_ttl_seconds < 0:
            raise ValueError("read-cache remaining_ttl_seconds must not be negative")

    @property
    def is_hit(self) -> bool:
        return self.payload is not None


class ReadCacheUnavailable(RuntimeError):
    """The cache backend could not complete an optional operation."""


class ReadCacheDataError(RuntimeError):
    """A cached value violated the Basic Memory cache encoding contract."""


class ReadCacheInvalidator(Protocol):
    """Capability for making one project's cached reads unreachable."""

    async def invalidate_project(self, project_id: str) -> ReadCacheInvalidationStatus:
        """Make every existing cached value for one project unreachable."""


class ReadCache(ReadCacheInvalidator, Protocol):
    """Best-effort read-through backend with project-generation invalidation."""

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
