"""Diagnostic tool for Basic Memory version and system information."""

import json
import platform
import sys

import basic_memory
from basic_memory.config import CONFIG_FILE_NAME, ConfigManager
from basic_memory.mcp.server import mcp

# Fields in BasicMemoryConfig that contain secrets and must never be surfaced.
_SECRET_FIELDS = frozenset({"cloud_api_key"})


def _redact_config(raw: dict) -> dict:
    """Return a copy of the raw config dict with secret fields removed.

    Only top-level keys are redacted. Nested secret-looking keys within
    project entries are not currently present, but the pattern is explicit
    so it is easy to extend.
    """
    return {k: v for k, v in raw.items() if k not in _SECRET_FIELDS}


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
