"""Logfire telemetry bootstrap.

`configure_telemetry()` wires up the Logfire SDK and returns the loguru
handler. Call sites use `logfire.span(...)` and `logfire.metric_counter(...)`
directly. The one exception is `debug_span(...)`, which marks a step span as
diagnostic detail.
"""

from __future__ import annotations

from typing import Any, Final

import logfire

REPOSITORY_URL = "https://github.com/basicmachines-co/basic-memory"
ROOT_PATH = "src/basic_memory"

# Step spans that only narrate a request (routing steps, typed-client wrappers,
# cache lookups) are created at this level. Logfire returns a no-op span when it is
# below the configured min_level, so a host exporting at info (Basic Memory Cloud
# production) pays nothing for them, while a debug host still sees the full tree.
# Spans opened inside a no-op span attach to the next recording span up.
DEBUG_SPAN_LEVEL: Final = "debug"

_LOGFIRE_HANDLER: dict[str, Any] | None = None


def debug_span(msg_template: str, /, **attributes: Any) -> logfire.LogfireSpan:
    """Open a span that is exported only when Logfire's min_level is debug.

    Attributes set on the span are dropped when it is not recorded. Anything a
    dashboard or alert reads belongs on an enclosing info span or in a metric.
    """
    return logfire.span(msg_template, _level=DEBUG_SPAN_LEVEL, **attributes)


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


__all__ = ["DEBUG_SPAN_LEVEL", "configure_telemetry", "debug_span", "get_logfire_handler"]
