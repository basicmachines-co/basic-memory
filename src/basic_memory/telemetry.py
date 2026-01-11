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

from __future__ import annotations

import platform
import re
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from basic_memory import __version__

if TYPE_CHECKING:
    from openpanel import OpenPanel

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
_telemetry_enabled: bool | None = None  # Cached to avoid repeated config reads


# --- Telemetry State ---


def _is_telemetry_enabled() -> bool:
    """Check if telemetry is enabled (cached).

    Returns False if:
    - User disabled via `bm telemetry disable`
    - DO_NOT_TRACK environment variable is set
    - Running in test environment
    """
    global _telemetry_enabled

    if _telemetry_enabled is None:
        from basic_memory.config import ConfigManager

        config = ConfigManager().config
        _telemetry_enabled = config.telemetry_enabled and not config.is_test_env

    return _telemetry_enabled


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


def _get_client() -> OpenPanel | None:
    """Get or create the OpenPanel client (singleton).

    Lazily initializes the client with global properties.
    Returns None if telemetry is disabled (avoids creating background thread).
    """
    global _client, _initialized

    # Trigger: telemetry disabled via config, env var, or test mode
    # Why: OpenPanel creates a background thread even when disabled=True,
    #      which can cause hangs on Python 3.14 during thread shutdown
    # Outcome: return None early, no OpenPanel client or thread created
    if not _is_telemetry_enabled():
        return None

    if _client is None:
        # Defer import to avoid creating background thread when telemetry disabled
        from openpanel import OpenPanel

        _client = OpenPanel(
            client_id=OPENPANEL_CLIENT_ID,
            client_secret=OPENPANEL_CLIENT_SECRET,
        )

        if not _initialized:
            install_id = get_install_id()
            # Set profile ID for OpenPanel (required for API to accept events)
            _client.identify(install_id)
            # Set global properties that go with every event
            _client.set_global_properties(
                {
                    "app_version": __version__,
                    "python_version": platform.python_version(),
                    "os": platform.system().lower(),
                    "arch": platform.machine(),
                    "install_id": install_id,
                    "source": "foss",
                }
            )
            _initialized = True

    return _client


def reset_client() -> None:
    """Reset the telemetry client (for testing or after config changes)."""
    global _client, _initialized, _telemetry_enabled
    _client = None
    _initialized = False
    _telemetry_enabled = None


def shutdown_telemetry() -> None:
    """Shutdown the telemetry client, stopping its background thread.

    Call this on application exit to ensure clean shutdown.
    The OpenPanel client creates a background thread with an event loop
    that needs to be stopped to avoid hangs on Python 3.14+.
    """
    import gc
    import io
    import sys

    global _client

    if _client is not None:
        try:
            # Suppress "Task was destroyed but it is pending!" warnings
            # These occur when we stop the event loop with pending HTTP requests,
            # which is expected during shutdown. The message is printed directly
            # to stderr by asyncio.Task.__del__(), so we redirect stderr temporarily.
            # We also force garbage collection to ensure the warning happens
            # while stderr is still redirected.
            stderr_backup = sys.stderr
            sys.stderr = io.StringIO()
            try:
                # OpenPanel._cleanup stops the event loop and joins the thread
                _client._cleanup()
                _client = None
                # Force garbage collection to trigger Task.__del__ while stderr is redirected
                gc.collect()
            finally:
                sys.stderr = stderr_backup
        except Exception as e:
            logger.opt(exception=False).debug(f"Telemetry shutdown failed: {e}")
        finally:
            _client = None


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
        client = _get_client()
        if client is not None:
            client.track(event, properties or {})
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
