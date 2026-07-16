"""Diagnostic tool for Basic Memory version and system information."""

import json
import platform
import sys

import basic_memory
from basic_memory.config import CONFIG_FILE_NAME, resolve_data_dir
from basic_memory.mcp.server import mcp
from basic_memory.redaction import redact_config as _redact_config
from basic_memory.redaction import redact_url as _redact_url  # noqa: F401 (re-exported for tests)


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
