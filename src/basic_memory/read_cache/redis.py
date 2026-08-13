"""Redis implementation of Basic Memory semantic read caching.

This module is imported only when the optional ``redis`` dependency is installed.
The caller owns the Redis client lifecycle.
"""

from uuid import uuid4

from redis.asyncio import Redis
from redis.exceptions import ClusterError as RedisClusterError
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import InvalidResponse as RedisInvalidResponse
from redis.exceptions import ResponseError as RedisResponseError
from redis.exceptions import TimeoutError as RedisTimeoutError

from basic_memory.read_cache.contract import (
    ReadCacheDataError,
    ReadCacheInvalidationStatus,
    ReadCacheKey,
    ReadCacheLookup,
    ReadCacheStoreStatus,
    ReadCacheUnavailable,
)
from basic_memory.read_cache.keys import (
    DEFAULT_READ_CACHE_PREFIX,
    RedisReadCacheKeys,
    redis_read_cache_generation_key,
    redis_read_cache_keys,
)

_ENVELOPE_SEPARATOR = b"\n"
_DEFAULT_GENERATION_TTL_SECONDS = 60
_REDIS_OPERATIONAL_ERRORS = (
    RedisClusterError,
    RedisConnectionError,
    RedisInvalidResponse,
    RedisResponseError,
    RedisTimeoutError,
)
_LOOKUP_SCRIPT = """
local generation = redis.call("GET", KEYS[1])
if generation then
    if redis.call("TTL", KEYS[1]) == -1 then
        redis.call("EXPIRE", KEYS[1], ARGV[2])
    end
else
    redis.call("SET", KEYS[1], ARGV[1], "EX", ARGV[2])
end
local values = redis.call("MGET", KEYS[1], KEYS[2])
return {values[1], values[2], redis.call("PTTL", KEYS[2])}
"""
_STORE_IF_CURRENT_SCRIPT = """
if redis.call("GET", KEYS[1]) ~= ARGV[1] then
    return 0
end
redis.call("SET", KEYS[2], ARGV[2], "EX", ARGV[3])
local response_ttl_ms = tonumber(ARGV[3]) * 1000
if redis.call("PTTL", KEYS[1]) < response_ttl_ms then
    redis.call("PEXPIRE", KEYS[1], response_ttl_ms)
end
return 1
"""


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


def _required_bytes(value: object, *, field: str) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8")
    raise ReadCacheDataError(f"Redis returned an invalid {field} value")


def _decode_generation(value: object) -> tuple[bytes, str]:
    encoded = _required_bytes(value, field="generation")
    try:
        generation = encoded.decode("ascii")
        decoded = bytes.fromhex(generation)
    except (UnicodeDecodeError, ValueError) as error:
        raise ReadCacheDataError("Redis returned an invalid generation token") from error
    if len(decoded) != 16:
        raise ReadCacheDataError("Redis returned an invalid generation token")
    return encoded, generation


def _store_status(value: object) -> ReadCacheStoreStatus:
    if value == 1:
        return ReadCacheStoreStatus.stored
    if value == 0:
        return ReadCacheStoreStatus.superseded
    raise ReadCacheDataError("Redis returned an invalid cache store result")


def _remaining_ttl_seconds(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ReadCacheDataError("Redis returned an invalid cached payload TTL")
    if value == -1:
        raise ReadCacheDataError("Redis cached payload has no expiration")
    if value < 0:
        raise ReadCacheDataError("Redis returned an invalid cached payload TTL")
    return value / 1_000


class RedisReadCache:
    """Namespace-bound Redis cache with race-safe generation invalidation.

    A managed host may reuse one client and Redis deployment across tenants.
    It must construct this lightweight adapter from trusted tenant/workspace
    context so the mandatory namespace cannot be supplied by an API caller.
    """

    def __init__(
        self,
        *,
        client: Redis,
        namespace: str,
        prefix: str = DEFAULT_READ_CACHE_PREFIX,
        generation_ttl_seconds: int = _DEFAULT_GENERATION_TTL_SECONDS,
    ) -> None:
        namespace = namespace.strip()
        if not namespace:
            raise ValueError("read-cache namespace must not be empty")
        if generation_ttl_seconds <= 0:
            raise ValueError("read-cache generation_ttl_seconds must be positive")
        self._client = client
        self._namespace = namespace
        self._prefix = prefix
        self._generation_ttl_seconds = generation_ttl_seconds

    def _keys(self, key: ReadCacheKey) -> RedisReadCacheKeys:
        return redis_read_cache_keys(
            prefix=self._prefix,
            namespace=self._namespace,
            key=key,
        )

    async def lookup(self, key: ReadCacheKey) -> ReadCacheLookup:
        keys = self._keys(key)
        try:
            generation_value, cached_value, remaining_ttl_ms = await self._client.eval(
                _LOOKUP_SCRIPT,
                2,
                keys.generation_key,
                keys.data_key,
                uuid4().hex.encode("ascii"),
                self._generation_ttl_seconds,
            )
        except _REDIS_OPERATIONAL_ERRORS as error:
            raise ReadCacheUnavailable("Redis cache lookup failed") from error

        generation, generation_text = _decode_generation(generation_value)
        if cached_value is None:
            return ReadCacheLookup(generation=generation_text)

        encoded = _required_bytes(cached_value, field="cached payload")
        cached_generation, separator, payload = encoded.partition(_ENVELOPE_SEPARATOR)
        if not separator or not cached_generation:
            raise ReadCacheDataError("Redis cached payload has an invalid generation envelope")
        if cached_generation != generation:
            return ReadCacheLookup(generation=generation_text)
        return ReadCacheLookup(
            generation=generation_text,
            payload=payload,
            remaining_ttl_seconds=_remaining_ttl_seconds(remaining_ttl_ms),
        )

    async def store(
        self,
        key: ReadCacheKey,
        lookup: ReadCacheLookup,
        payload: bytes,
        *,
        ttl_seconds: int,
    ) -> ReadCacheStoreStatus:
        if ttl_seconds <= 0:
            raise ValueError("read-cache ttl_seconds must be positive")

        keys = self._keys(key)
        generation, _ = _decode_generation(lookup.generation)
        encoded = generation + _ENVELOPE_SEPARATOR + payload
        try:
            stored = await self._client.eval(
                _STORE_IF_CURRENT_SCRIPT,
                2,
                keys.generation_key,
                keys.data_key,
                generation,
                encoded,
                ttl_seconds,
            )
        except _REDIS_OPERATIONAL_ERRORS as error:
            raise ReadCacheUnavailable("Redis cache store failed") from error

        return _store_status(stored)

    async def invalidate_project(self, project_id: str) -> ReadCacheInvalidationStatus:
        generation_key = redis_read_cache_generation_key(
            prefix=self._prefix,
            namespace=self._namespace,
            project_id=project_id,
        )
        try:
            await self._client.set(
                generation_key,
                uuid4().hex.encode("ascii"),
                ex=self._generation_ttl_seconds,
            )
        except _REDIS_OPERATIONAL_ERRORS as error:
            raise ReadCacheUnavailable("Redis project invalidation failed") from error
        return ReadCacheInvalidationStatus.invalidated
