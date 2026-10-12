"""Redis key construction for the read cache.

Every key of one tenant project shares a cluster hash tag, so a pipeline over a
project's version keys and its entries stays on one slot.
"""

from hashlib import sha256

from basic_memory.read_cache.contract import ReadCacheGroup, canonical_uuid

# v3: entries are keyed by group version tokens (fastapi-redis-sdk storage), so no
# earlier entry format can be read back.
DEFAULT_READ_CACHE_PREFIX = "bm:read:v3"


def read_cache_request_digest(*parts: str) -> str:
    """Hash length-delimited request parts into one stable cache identity."""
    digest = sha256()
    for part in parts:
        encoded = part.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, byteorder="big"))
        digest.update(encoded)
    return digest.hexdigest()


def project_hash_tag(*, namespace: str, project_id: str) -> str:
    """The cluster hash tag shared by every key of one tenant project.

    The namespace and project id are hashed so keys reveal neither.
    """
    namespace = namespace.strip()
    if not namespace:
        raise ValueError("read-cache namespace must not be empty")
    scope = read_cache_request_digest(namespace, canonical_uuid(project_id, label="project id"))
    return "{" + scope[:32] + "}"


def group_version_key(
    *, prefix: str, namespace: str, project_id: str, group: ReadCacheGroup
) -> str:
    tag = project_hash_tag(namespace=namespace, project_id=project_id)
    return f"{prefix}:{tag}:version:{group.name}"


def entry_index_key(
    *, prefix: str, namespace: str, project_id: str, group: ReadCacheGroup, version: str
) -> str:
    """The set of entry keys stored under one version of one group.

    Replacing that version deletes every key in the set, so retired entries free
    their memory at once instead of waiting out their TTL.
    """
    tag = project_hash_tag(namespace=namespace, project_id=project_id)
    return f"{prefix}:{tag}:entries:{group.name}:{version}"


def global_epoch_key(*, prefix: str) -> str:
    """One key whose replacement retires every entry, for resets after a deploy."""
    return f"{prefix}:epoch"


def entry_key(
    *,
    prefix: str,
    namespace: str,
    project_id: str,
    operation: str,
    versions: str,
    request_digest: str,
) -> str:
    tag = project_hash_tag(namespace=namespace, project_id=project_id)
    return f"{prefix}:{tag}:{operation}:{versions}:{request_digest}"
