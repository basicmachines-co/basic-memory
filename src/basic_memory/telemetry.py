"""Anonymous telemetry for Basic Memory (Homebrew-style opt-out).

This module implements privacy-respecting usage analytics following the Homebrew model:
- Telemetry is ON by default
- Users can easily opt out: `bm telemetry disable`
- First run shows a one-time notice (not a prompt)
- Only anonymous data is collected (random UUID, no personal info)

What we collect:
- App version, Python version, OS, architecture
- Feature usage (which MCP tools and CLI commands are used)
- Error types (sanitized, no file paths or personal data)

What we NEVER collect:
- Note content, file names, or paths
- Personal information
- IP addresses (OpenPanel doesn't store these)

Documentation: https://basicmemory.com/telemetry
"""

import platform
import re
import uuid
from pathlib import Path
from typing import Any

from loguru import logger
from openpanel import OpenPanel

from basic_memory import __version__

# --- Configuration ---

# OpenPanel credentials (write-only, safe to embed in client code)
# These can only send events to our dashboard, not read any data
OPENPANEL_CLIENT_ID = "2e7b036d-c6e5-40aa-91eb-5c70a8ef21a3"
OPENPANEL_CLIENT_SECRET = "sec_92f7f8328bd0368ff4c2"

TELEMETRY_DOCS_URL = "https://basicmemory.com/telemetry"

TELEMETRY_NOTICE = f"""
Basic Memory collects anonymous usage statistics to help improve the software.
This includes: version, OS, feature usage, and errors. No personal data or note content.

To opt out: bm telemetry disable
Details: {TELEMETRY_DOCS_URL}
"""

# --- Module State ---

_client: OpenPanel | None = None
_initialized: bool = False


# --- Installation ID ---


def get_install_id() -> str:
    """Get or create anonymous installation ID.

    Creates a random UUID on first run and stores it locally.
    User can delete ~/.basic-memory/.install_id to reset.
    """
    id_file = Path.home() / ".basic-memory" / ".install_id"

    if id_file.exists():
        return id_file.read_text().strip()

    install_id = str(uuid.uuid4())
    id_file.parent.mkdir(parents=True, exist_ok=True)
    id_file.write_text(install_id)
    return install_id


# --- Client Management ---


def _get_client() -> OpenPanel:
    """Get or create the OpenPanel client (singleton).

    Lazily initializes the client with global properties.
    """
    global _client, _initialized

    if _client is None:
        from basic_memory.config import ConfigManager

        config = ConfigManager().config

        # Trigger: first call to track an event
        # Why: lazy init avoids work if telemetry never used; disabled flag
        #      tells OpenPanel to skip network calls when user opts out
        # Outcome: client ready to queue events (or silently discard if disabled)
        _client = OpenPanel(
            client_id=OPENPANEL_CLIENT_ID,
            client_secret=OPENPANEL_CLIENT_SECRET,
            disabled=not config.telemetry_enabled,
        )

        if config.telemetry_enabled and not _initialized:
            # Set global properties that go with every event
            _client.set_global_properties(
                {
                    "app_version": __version__,
                    "python_version": platform.python_version(),
                    "os": platform.system().lower(),
                    "arch": platform.machine(),
                    "install_id": get_install_id(),
                }
            )
            _initialized = True

    return _client


def reset_client() -> None:
    """Reset the telemetry client (for testing or after config changes)."""
    global _client, _initialized
    _client = None
    _initialized = False


# --- Event Tracking ---


def track(event: str, properties: dict[str, Any] | None = None) -> None:
    """Track an event. Fire-and-forget, never raises.

    Args:
        event: Event name (e.g., "app_started", "mcp_tool_called")
        properties: Optional event properties
    """
    # Constraint: telemetry must never break the application
    # Even if OpenPanel API is down or config is corrupt, user's command must succeed
    try:
        _get_client().track(event, properties or {})
    except Exception as e:
        logger.opt(exception=False).debug(f"Telemetry failed: {e}")


# --- First-Run Notice ---


def show_notice_if_needed() -> None:
    """Show one-time telemetry notice (Homebrew style).

    Only shows if:
    - Telemetry is enabled
    - Notice hasn't been shown before

    After showing, marks the notice as shown in config.
    """
    from basic_memory.config import ConfigManager

    config_manager = ConfigManager()
    config = config_manager.config

    if config.telemetry_enabled and not config.telemetry_notice_shown:
        from rich.console import Console
        from rich.panel import Panel

        # Print to stderr so it doesn't interfere with command output
        console = Console(stderr=True)
        console.print(
            Panel(
                TELEMETRY_NOTICE.strip(),
                title="[dim]Telemetry Notice[/dim]",
                border_style="dim",
                expand=False,
            )
        )

        # Mark as shown so we don't show again
        config.telemetry_notice_shown = True
        config_manager.save_config(config)


# --- Convenience Functions ---


def track_app_started(mode: str) -> None:
    """Track app startup.

    Args:
        mode: "cli" or "mcp"
    """
    track("app_started", {"mode": mode})


def track_mcp_tool(tool_name: str) -> None:
    """Track MCP tool usage.

    Args:
        tool_name: Name of the tool (e.g., "write_note", "search_notes")
    """
    track("mcp_tool_called", {"tool": tool_name})


def track_cli_command(command: str) -> None:
    """Track CLI command usage.

    Args:
        command: Command name (e.g., "sync", "import claude")
    """
    track("cli_command", {"command": command})


def track_sync_completed(entity_count: int, duration_ms: int) -> None:
    """Track sync completion.

    Args:
        entity_count: Number of entities synced
        duration_ms: Duration in milliseconds
    """
    track("sync_completed", {"entity_count": entity_count, "duration_ms": duration_ms})


def track_import_completed(source: str, count: int) -> None:
    """Track import completion.

    Args:
        source: Import source (e.g., "claude", "chatgpt")
        count: Number of items imported
    """
    track("import_completed", {"source": source, "count": count})


def track_error(error_type: str, message: str) -> None:
    """Track an error (sanitized).

    Args:
        error_type: Exception class name
        message: Error message (will be sanitized to remove file paths)
    """
    if not message:
        track("error", {"type": error_type, "message": ""})
        return

    # Sanitize file paths to prevent leaking user directory structure
    # Unix paths: /Users/name/file.py, /home/user/notes/doc.md
    sanitized = re.sub(r"/[\w/.+-]+\.\w+", "[FILE]", message)
    # Windows paths: C:\Users\name\file.py, D:\projects\doc.md
    sanitized = re.sub(r"[A-Z]:\\[\w\\.+-]+\.\w+", "[FILE]", sanitized, flags=re.IGNORECASE)

    # Truncate to avoid sending too much data
    track("error", {"type": error_type, "message": sanitized[:200]})
