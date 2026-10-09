"""Diagnostic tool for Basic Memory version and system information."""

import json
import os
import platform
import sys

import basic_memory
from basic_memory.config import CONFIG_FILE_NAME, BasicMemoryConfig, resolve_data_dir
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
    - BASIC_MEMORY_* environment variables that override the file at runtime

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

    # --- Environment overrides ---
    # The running server's settings are BasicMemoryConfig (pydantic-settings), so a
    # BASIC_MEMORY_<FIELD> env var wins over config.json. Without this section the
    # dump above can disagree with what the server actually uses (#1595).
    env_overrides, other_env_names = _environment_overrides()
    if env_overrides:
        env_dump = json.dumps(env_overrides, indent=2, default=str)
    else:
        env_dump = "{}"

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
        "",
        "## Environment Overrides",
        "BASIC_MEMORY_* variables override the config file values above.",
        "",
        "```json",
        env_dump,
        "```",
    ]
    if other_env_names:
        # Not config fields (API keys, test switches, routing flags): their names
        # help support, their values could be secrets, so only names are listed.
        lines += ["", f"- Other BASIC_MEMORY_* variables set: {', '.join(other_env_names)}"]
    return "\n".join(lines)


def _environment_overrides() -> tuple[dict[str, object], list[str]]:
    """Split BASIC_MEMORY_* env vars into config-field overrides and other names.

    Overrides are keyed by config field name so they go through the same
    redaction as the file dump; redact_config matches field names, not env var
    names. A secret field is still reported as overridden, with its value hidden.
    """
    prefix = str(BasicMemoryConfig.model_config["env_prefix"])
    overrides: dict[str, object] = {}
    other_names: list[str] = []
    for env_name, value in os.environ.items():
        # pydantic-settings matches env names case-insensitively by default.
        if not env_name.upper().startswith(prefix):
            continue
        field_name = env_name[len(prefix) :].lower()
        if field_name in BasicMemoryConfig.model_fields:
            overrides[field_name] = value
        else:
            other_names.append(env_name)

    redacted = _redact_config(overrides)
    for field_name in overrides.keys() - redacted.keys():
        redacted[field_name] = "<redacted>"
    return dict(sorted(redacted.items())), sorted(other_names)
