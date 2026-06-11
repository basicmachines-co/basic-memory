"""Diagnostic tool for Basic Memory version and system information."""

import json
import platform
import sys
from urllib.parse import urlparse, urlunparse

import basic_memory
from basic_memory.config import CONFIG_FILE_NAME, ConfigManager
from basic_memory.mcp.server import mcp

# Fields in BasicMemoryConfig that contain secrets and must never be surfaced.
_SECRET_FIELDS = frozenset({"cloud_api_key"})

# Fields whose values are URLs that may embed user:password credentials.
# The userinfo component is stripped before surfacing.
_URL_FIELDS = frozenset({"database_url"})


def _redact_url(url: str) -> str:
    """Strip the userinfo (user:password) from a URL string.

    Replaces any credentials with *** so the host/path remain visible for
    diagnostics (e.g. ``postgresql://***@localhost/mydb``).  If the value
    cannot be parsed as a URL it is returned unchanged.
    """
    try:
        parsed = urlparse(url)
    except Exception:  # pragma: no cover — urlparse is very permissive
        return url

    if not parsed.hostname:
        # Not a meaningful URL (e.g. a bare file path); leave it alone.
        return url

    if parsed.username or parsed.password:
        # Rebuild with credentials replaced by a placeholder.
        netloc = f"***@{parsed.hostname}"
        if parsed.port:
            netloc = f"{netloc}:{parsed.port}"
        redacted = parsed._replace(netloc=netloc)
        return urlunparse(redacted)

    return url


def _redact_config(raw: dict) -> dict:
    """Return a copy of the raw config dict with secret fields removed.

    - Keys in ``_SECRET_FIELDS`` are dropped entirely.
    - Keys in ``_URL_FIELDS`` have their userinfo component stripped so that
      host and database name remain visible for diagnostics.

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
    annotations={"readOnlyHint": True, "openWorldHint": False},
)
def basic_memory_diagnostics() -> str:
    """Return version, system, and configuration diagnostics for Basic Memory.

    Provides:
    - Basic Memory package version and API version
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
    manager = ConfigManager()
    config_file = manager.config_dir / CONFIG_FILE_NAME
    config_exists = config_file.exists()

    if config_exists:
        try:
            raw_config = json.loads(config_file.read_text(encoding="utf-8"))
            safe_config = _redact_config(raw_config)
            config_dump = json.dumps(safe_config, indent=2, default=str)
        except Exception as exc:  # pragma: no cover
            config_dump = f"<error reading config: {exc}>"
    else:
        config_dump = "<config file not found>"

    lines = [
        "# Basic Memory Diagnostics",
        "",
        "## Version",
        f"- basic-memory: {bm_version}",
        f"- API version: {api_version}",
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
