"""Diagnostic tool for Basic Memory version and system information."""

import json
import platform
import sys
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import basic_memory
from basic_memory.config import CONFIG_FILE_NAME, resolve_data_dir
from basic_memory.mcp.server import mcp

# Fields in BasicMemoryConfig that contain secrets and must never be surfaced.
_SECRET_FIELDS = frozenset({"cloud_api_key"})

# Fields whose values are URLs that may embed user:password credentials.
# The userinfo component is stripped before surfacing.
_URL_FIELDS = frozenset({"database_url"})

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


def _redact_url(url: str) -> str:
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


def _redact_config(raw: dict) -> dict:
    """Return a copy of the raw config dict with secret fields removed.

    - Keys in ``_SECRET_FIELDS`` are dropped entirely.
    - Keys in ``_URL_FIELDS`` have userinfo and credential-bearing query values
      stripped so that safe host, database, and connection options remain visible.

    Only top-level keys are processed. Nested keys within project entries are
    not currently credential-bearing, but the two sets make the pattern easy
    to extend.
    """
    result: dict = {}
    for k, v in raw.items():
        if k in _SECRET_FIELDS:
            # Drop entirely — value has no diagnostic value.
            continue
        if k in _URL_FIELDS and isinstance(v, str):
            result[k] = _redact_url(v)
        else:
            result[k] = v
    return result


@mcp.tool(
    "basic_memory_diagnostics",
    title="Basic Memory Diagnostics",
    tags={"diagnostics"},
    annotations={
        "title": "Basic Memory Diagnostics",
        "readOnlyHint": True,
        "destructiveHint": False,
        "openWorldHint": False,
    },
    # The report is a markdown string; suppress FastMCP's wrap_result so the
    # payload isn't duplicated into structuredContent.
    output_schema=None,
)
def basic_memory_diagnostics() -> str:
    """Return version, system, and configuration diagnostics for Basic Memory.

    Provides:
    - Basic Memory package version
    - Python version and platform details
    - Config file path and its contents (secrets redacted)

    Useful for troubleshooting installations and gathering information for
    support requests. Read-only; never emits secrets or API keys.
    """
    # --- Version information ---
    bm_version = basic_memory.__version__
    api_version = basic_memory.__api_version__

    # --- System information ---
    python_version = sys.version
    platform_info = platform.platform()
    machine = platform.machine()

    # --- Configuration ---
    # resolve_data_dir only computes the path. ConfigManager would create and
    # chmod the directory, violating this tool's read-only contract.
    config_file = resolve_data_dir() / CONFIG_FILE_NAME
    config_exists = config_file.exists()

    if config_exists:
        try:
            raw_config = json.loads(config_file.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            config_dump = f"<error reading config: {exc}>"
        else:
            if isinstance(raw_config, dict):
                safe_config = _redact_config(raw_config)
                config_dump = json.dumps(safe_config, indent=2, default=str)
            else:
                config_dump = "<error reading config: expected a JSON object>"
    else:
        config_dump = "<config file not found>"

    lines = [
        "# Basic Memory Diagnostics",
        "",
        "## Version",
        f"- basic-memory: {bm_version}",
        f"- API: {api_version}",
        "",
        "## System",
        f"- Python: {python_version}",
        f"- Platform: {platform_info}",
        f"- Architecture: {machine}",
        "",
        "## Configuration",
        f"- Config path: {config_file}",
        f"- Config exists: {config_exists}",
        "",
        "```json",
        config_dump,
        "```",
    ]
    return "\n".join(lines)
