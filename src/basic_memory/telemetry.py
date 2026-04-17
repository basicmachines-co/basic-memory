"""Logfire telemetry bootstrap.

`configure_telemetry()` wires up the Logfire SDK and returns the loguru
handler. Call sites use `logfire.span(...)` and `logfire.metric_counter(...)`
directly — there are no wrappers here.
"""

from __future__ import annotations

from typing import Any

import logfire

REPOSITORY_URL = "https://github.com/basicmachines-co/basic-memory"
ROOT_PATH = "src/basic_memory"

_LOGFIRE_HANDLER: dict[str, Any] | None = None


def configure_telemetry(
    service_name: str,
    *,
    environment: str,
    service_version: str | None = None,
    enable_logfire: bool = False,
    send_to_logfire: bool = False,
    log_level: str = "INFO",
) -> bool:
    """Configure Logfire for the current process. Returns True when enabled."""
    global _LOGFIRE_HANDLER
    _LOGFIRE_HANDLER = None

    if not enable_logfire:
        return False

    kwargs: dict[str, Any] = {
        "service_name": service_name,
        "environment": environment,
        "code_source": logfire.CodeSource(
            repository=REPOSITORY_URL,
            revision=service_version or "",
            root_path=ROOT_PATH,
        ),
        "min_level": log_level.lower(),
        "send_to_logfire": send_to_logfire,
    }

    try:
        logfire.configure(**kwargs)
    except TypeError:
        # Older logfire releases don't accept send_to_logfire as a keyword.
        kwargs.pop("send_to_logfire", None)
        logfire.configure(**kwargs)

    _LOGFIRE_HANDLER = logfire.loguru_handler()
    return True


def get_logfire_handler() -> dict[str, Any] | None:
    """Return the active Logfire loguru handler, if any."""
    return _LOGFIRE_HANDLER


__all__ = ["configure_telemetry", "get_logfire_handler"]
