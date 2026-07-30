"""Canonical request digests and Redis key construction."""

from dataclasses import dataclass
from hashlib import sha256

from basic_memory.read_cache.contract import (
    ReadCacheKey,
    canonical_read_cache_project_id,
)

DEFAULT_READ_CACHE_PREFIX = "bm:read:v1"


def read_cache_request_digest(*parts: str) -> str:
    """Hash length-delimited request parts into one stable cache identity."""
    digest = sha256()
    for part in parts:
        encoded = part.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, byteorder="big"))
        digest.update(encoded)
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class RedisReadCacheKeys:
    """Redis keys for one project generation and canonical request."""

    generation_key: str
    data_key: str


def _redis_read_cache_cluster_scope(*, namespace: str, project_id: str) -> str:
    namespace = namespace.strip()
    if not namespace:
        raise ValueError("read-cache namespace must not be empty")

    scope_digest = read_cache_request_digest(
        namespace,
        canonical_read_cache_project_id(project_id),
    )
    return f"{{{scope_digest}}}"


def _redis_read_cache_key_base(
    *,
    prefix: str,
    namespace: str,
    project_id: str,
) -> str:
    if not prefix or any(character.isspace() for character in prefix):
        raise ValueError("read-cache prefix must be non-empty and contain no whitespace")

    cluster_scope = _redis_read_cache_cluster_scope(
        namespace=namespace,
        project_id=project_id,
    )
    return f"{prefix}:{cluster_scope}"


def redis_read_cache_generation_key(
    *,
    prefix: str,
    namespace: str,
    project_id: str,
) -> str:
    """Build the generation key shared by every cached read in one project."""
    key_base = _redis_read_cache_key_base(
        prefix=prefix,
        namespace=namespace,
        project_id=project_id,
    )
    return f"{key_base}:generation"


def redis_read_cache_keys(
    *,
    prefix: str,
    namespace: str,
    key: ReadCacheKey,
) -> RedisReadCacheKeys:
    """Build versioned Redis keys without exposing namespace values."""
    key_base = _redis_read_cache_key_base(
        prefix=prefix,
        namespace=namespace,
        project_id=key.project_id,
    )
    return RedisReadCacheKeys(
        generation_key=f"{key_base}:generation",
        data_key=f"{key_base}:{key.operation.value}:{key.request_digest}",
    )
