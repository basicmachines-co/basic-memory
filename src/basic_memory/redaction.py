"""Shared secret-redaction rules for surfacing ``BasicMemoryConfig`` values.

Used by the ``basic_memory_diagnostics`` MCP tool (#963) and the ``bm config``
CLI command group (#991) so both surfaces apply identical scrubbing rules
instead of drifting apart over time.
"""

from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

# Fields in BasicMemoryConfig that contain secrets and must never be surfaced.
SECRET_FIELDS = frozenset({"cloud_api_key", "semantic_embedding_api_key"})

# Fields whose values are URLs that may embed user:password credentials.
# The userinfo component is stripped before surfacing.
URL_FIELDS = frozenset({"database_url", "semantic_embedding_api_base"})

_SECRET_QUERY_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "credential",
        "password",
        "passwd",
        "pwd",
        "secret",
        "secret_key",
        "sslpassword",
        "token",
    }
)


def _query_key_is_secret(key: str) -> bool:
    normalized = key.strip().lower().replace("-", "_")
    return normalized in _SECRET_QUERY_KEYS or normalized.endswith(
        ("_password", "_secret", "_token", "_key")
    )


def _redact_query_secrets(query: str) -> str:
    """Mask credential-bearing query values while preserving diagnostic options."""
    pairs = parse_qsl(query, keep_blank_values=True)
    if not any(_query_key_is_secret(key) for key, _ in pairs):
        return query
    return urlencode([(key, "***" if _query_key_is_secret(key) else value) for key, value in pairs])


def redact_url(url: str) -> str:
    """Strip userinfo and credential-bearing query values from a URL string.

    Replaces any credentials with *** so the host/path remain visible for
    diagnostics (e.g. ``postgresql://***@localhost/mydb``).  If the value
    cannot be parsed as a URL it is returned unchanged.
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        # A malformed authority can still contain credentials. Redact the
        # userinfo and query conservatively rather than returning a secret unchanged.
        base, query_separator, query = url.partition("?")
        safe_url = f"{base}?{_redact_query_secrets(query)}" if query_separator else base
        scheme, separator, remainder = safe_url.partition("://")
        if separator and "@" in remainder:
            _, _, authority = remainder.rpartition("@")
            return f"{scheme}://***@{authority}"
        return safe_url

    redacted_query = _redact_query_secrets(parsed.query)
    if "@" not in parsed.netloc and redacted_query == parsed.query:
        # Neither URL userinfo nor known secret query parameters are present.
        return url

    redacted_netloc = parsed.netloc
    if "@" in parsed.netloc:
        # Preserve the authority verbatim after the final @. In particular, using
        # parsed.hostname here would discard the brackets required around IPv6 hosts.
        _, _, authority = parsed.netloc.rpartition("@")
        redacted_netloc = f"***@{authority}"

    return urlunparse(parsed._replace(netloc=redacted_netloc, query=redacted_query))


def redact_config(raw: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of a raw config dict with secret fields removed.

    - Keys in ``SECRET_FIELDS`` are dropped entirely.
    - Keys in ``URL_FIELDS`` have userinfo and credential-bearing query values
      stripped so that safe host, database, and connection options remain visible.

    Only top-level keys are processed. Nested keys within project entries are
    not currently credential-bearing, but the two sets make the pattern easy
    to extend.
    """
    result: dict[str, Any] = {}
    for k, v in raw.items():
        if k in SECRET_FIELDS:
            # Drop entirely — value has no diagnostic value.
            continue
        if k in URL_FIELDS and isinstance(v, str):
            result[k] = redact_url(v)
        else:
            result[k] = v
    return result
