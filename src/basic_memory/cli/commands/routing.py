"""CLI routing utilities for --local/--cloud flag handling.

This module provides utilities for CLI commands to override default routing.
This allows users to force local or cloud routing per-command.

The routing is controlled via environment variables:
- BASIC_MEMORY_FORCE_LOCAL: When "true", forces local ASGI transport
- BASIC_MEMORY_FORCE_CLOUD: When "true", forces cloud proxy transport
- BASIC_MEMORY_EXPLICIT_ROUTING: When "true", signals that --local/--cloud
  was explicitly passed, overriding per-project routing in get_client()
- These are checked in basic_memory.mcp.async_client.get_client()
"""

import os
from contextlib import contextmanager
from typing import Generator


@contextmanager
def force_routing(local: bool = False, cloud: bool = False) -> Generator[None, None, None]:
    """Context manager to temporarily override routing mode.

    Sets environment variables that are checked by get_client() to determine
    whether to use local ASGI transport or cloud proxy transport.

    When either flag is set, BASIC_MEMORY_EXPLICIT_ROUTING is also set so
    that get_client() skips per-project routing and honors the flag directly.
    This only affects CLI commands — the MCP server sets FORCE_LOCAL directly
    (without EXPLICIT_ROUTING), so per-project routing still works for MCP tools.

    Args:
        local: If True, force local ASGI transport
        cloud: If True, force cloud proxy transport

    Usage:
        with force_routing(local=True):
            # All API calls will use local ASGI transport
            await some_api_call()

    Raises:
        ValueError: If both local and cloud are True
    """
    if local and cloud:
        raise ValueError("Cannot specify both --local and --cloud")

    # Save original values
    original_force_local = os.environ.get("BASIC_MEMORY_FORCE_LOCAL")
    original_force_cloud = os.environ.get("BASIC_MEMORY_FORCE_CLOUD")
    original_explicit = os.environ.get("BASIC_MEMORY_EXPLICIT_ROUTING")

    try:
        if local:
            os.environ["BASIC_MEMORY_FORCE_LOCAL"] = "true"
            os.environ.pop("BASIC_MEMORY_FORCE_CLOUD", None)
            os.environ["BASIC_MEMORY_EXPLICIT_ROUTING"] = "true"
        elif cloud:
            os.environ.pop("BASIC_MEMORY_FORCE_LOCAL", None)
            os.environ["BASIC_MEMORY_FORCE_CLOUD"] = "true"
            os.environ["BASIC_MEMORY_EXPLICIT_ROUTING"] = "true"
        # If neither is set, don't change anything (use default behavior)
        yield
    finally:
        # Restore original values
        if original_force_local is None:
            os.environ.pop("BASIC_MEMORY_FORCE_LOCAL", None)
        else:
            os.environ["BASIC_MEMORY_FORCE_LOCAL"] = original_force_local

        if original_force_cloud is None:
            os.environ.pop("BASIC_MEMORY_FORCE_CLOUD", None)
        else:
            os.environ["BASIC_MEMORY_FORCE_CLOUD"] = original_force_cloud

        if original_explicit is None:
            os.environ.pop("BASIC_MEMORY_EXPLICIT_ROUTING", None)
        else:
            os.environ["BASIC_MEMORY_EXPLICIT_ROUTING"] = original_explicit


def validate_routing_flags(local: bool, cloud: bool) -> None:
    """Validate that --local and --cloud flags are not both specified.

    Args:
        local: Value of --local flag
        cloud: Value of --cloud flag

    Raises:
        ValueError: If both flags are True
    """
    if local and cloud:
        raise ValueError("Cannot specify both --local and --cloud flags")
