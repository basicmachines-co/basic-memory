"""Anonymous telemetry for Basic Memory (Homebrew-style opt-out).

This module provides privacy-first telemetry following the Homebrew analytics model:
- On by default with easy opt-out
- Anonymous installation ID (random UUID, user-deletable)
- No personal data or note content collection
- Fire-and-forget (telemetry errors never break the app)

Usage:
    from basic_memory.telemetry import track, show_telemetry_notice

    # Track an event
    track("app_started", {"mode": "cli"})

    # Show first-run notice (only once)
    show_telemetry_notice()
"""

import os
import platform
import uuid
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from basic_memory import __version__
from basic_memory.config import ConfigManager

# --- Module State ---
_client: Optional[Any] = None
_telemetry_checked = False


def get_install_id() -> str:
    """Get or create anonymous installation ID.

    The install ID is stored at ~/.basic-memory/.install_id and can be
    deleted by the user at any time to generate a new ID.

    Returns:
        UUID string identifying this installation
    """
    id_file = Path.home() / ".basic-memory" / ".install_id"

    if id_file.exists():
        return id_file.read_text().strip()

    # Create new install ID
    install_id = str(uuid.uuid4())
    id_file.parent.mkdir(parents=True, exist_ok=True)
    id_file.write_text(install_id)

    return install_id


def is_telemetry_enabled() -> bool:
    """Check if telemetry is enabled.

    Priority:
    1. BASIC_MEMORY_TELEMETRY_ENABLED environment variable
    2. Config file value (telemetry_enabled)

    Returns:
        True if telemetry is enabled, False otherwise
    """
    env_value = os.environ.get("BASIC_MEMORY_TELEMETRY_ENABLED", "").lower()
    if env_value in ("false", "0", "no"):
        return False
    elif env_value in ("true", "1", "yes"):
        return True

    # Fall back to config file value
    try:
        config_manager = ConfigManager()
        config = config_manager.load_config()
        return config.telemetry_enabled
    except Exception:
        # If config can't be loaded, default to enabled
        return True


def get_client() -> Optional[Any]:
    """Get or create the OpenPanel client.

    Returns None if telemetry is disabled or if OpenPanel import fails.
    Lazily initializes the client on first call.

    Returns:
        OpenPanel client instance or None
    """
    global _client, _telemetry_checked

    if _telemetry_checked:
        return _client

    _telemetry_checked = True

    if not is_telemetry_enabled():
        return None

    try:
        from openpanel import OpenPanel

        # Initialize OpenPanel with Basic Memory project details
        # API key and client details will be provided via environment variables
        # or config in production
        _client = OpenPanel(
            client_id=os.getenv("OPENPANEL_CLIENT_ID", "basic-memory"),
            client_secret=os.getenv("OPENPANEL_CLIENT_SECRET", ""),
        )
        return _client
    except ImportError:
        logger.debug("OpenPanel not available, telemetry disabled")
        return None
    except Exception as e:
        logger.debug(f"Failed to initialize telemetry client: {e}")
        return None


def get_global_properties() -> dict[str, Any]:
    """Get global properties sent with every event.

    Returns:
        Dictionary of global properties including version, OS, architecture, etc.
    """
    return {
        "app_version": __version__,
        "python_version": platform.python_version(),
        "os": platform.system().lower(),
        "arch": platform.machine(),
        "install_id": get_install_id(),
    }


def track(event: str, properties: Optional[dict[str, Any]] = None) -> None:
    """Track an event with optional properties.

    This is a fire-and-forget operation that never raises exceptions.
    If telemetry is disabled or the client is unavailable, this is a no-op.

    Args:
        event: Event name (e.g., "app_started", "mcp_tool_called")
        properties: Optional event-specific properties
    """
    try:
        client = get_client()
        if client is None:
            return

        # Merge global properties with event-specific properties
        all_properties = get_global_properties()
        if properties:
            all_properties.update(properties)

        # Track the event
        client.track(event, all_properties)
    except Exception as e:
        # Telemetry must never break the app
        logger.debug(f"Telemetry tracking failed: {e}")


def show_telemetry_notice() -> None:
    """Show the telemetry notice to the user (once per installation).

    This should be called on first run of CLI or MCP server.
    The notice informs users about data collection and how to opt out.
    """
    try:
        if not is_telemetry_enabled():
            return

        config_manager = ConfigManager()
        config = config_manager.load_config()

        # Check if notice has already been shown
        if config.telemetry_notice_shown:
            return

        # Show the notice
        notice = """
Basic Memory collects anonymous usage statistics to help improve the software.
This includes: version, OS, feature usage, and errors. No personal data or note content.
To opt out: bm telemetry disable
Details: https://memory.basicmachines.co/telemetry
"""
        print(notice)

        # Mark notice as shown
        config.telemetry_notice_shown = True
        config_manager.save_config(config)

    except Exception as e:
        # Telemetry must never break the app
        logger.debug(f"Failed to show telemetry notice: {e}")
