"""Optional Logfire telemetry helpers for Basic Memory.

Telemetry is disabled by default. When enabled, this module configures Logfire,
exposes a `loguru` handler for trace-aware logging, and provides lightweight
helpers for manual spans and logger context binding.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator

from loguru import logger

REPOSITORY_URL = "https://github.com/basicmachines-co/basic-memory"
ROOT_PATH = "src/basic_memory"


def _load_logfire() -> Any | None:
    """Load the optional logfire dependency lazily."""
    try:
        import logfire
    except ImportError:
        return None
    return logfire


@dataclass
class TelemetryState:
    """Process-local Logfire configuration state."""

    enabled: bool = False
    configured: bool = False
    service_name: str | None = None
    environment: str | None = None
    send_to_logfire: bool = False
    warnings: list[str] = field(default_factory=list)


_STATE = TelemetryState()
_LOGFIRE_HANDLER: dict[str, Any] | None = None


def reset_telemetry_state() -> None:
    """Reset process-local telemetry state.

    Primarily used by tests.
    """
    global _LOGFIRE_HANDLER
    _STATE.enabled = False
    _STATE.configured = False
    _STATE.service_name = None
    _STATE.environment = None
    _STATE.send_to_logfire = False
    _STATE.warnings.clear()
    _LOGFIRE_HANDLER = None


def _filter_attributes(attrs: dict[str, Any]) -> dict[str, Any]:
    """Drop null attributes so span and log payloads stay compact."""
    return {key: value for key, value in attrs.items() if value is not None}


def configure_telemetry(
    service_name: str,
    *,
    environment: str,
    service_version: str | None = None,
    enable_logfire: bool = False,
    send_to_logfire: bool = False,
    log_level: str = "INFO",
) -> bool:
    """Configure optional Logfire instrumentation for the current process."""
    global _LOGFIRE_HANDLER

    reset_telemetry_state()
    _STATE.service_name = service_name
    _STATE.environment = environment
    _STATE.send_to_logfire = send_to_logfire
    _STATE.enabled = enable_logfire

    if not enable_logfire:
        return False

    logfire = _load_logfire()
    if logfire is None:
        _STATE.enabled = False
        _STATE.warnings.append(
            "Logfire telemetry was enabled but the 'logfire' package is not installed. "
            "Telemetry remains disabled."
        )
        return False

    configure_kwargs = {
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
        logfire.configure(**configure_kwargs)
    except TypeError:
        configure_kwargs.pop("send_to_logfire", None)
        logfire.configure(**configure_kwargs)
    except Exception as exc:  # pragma: no cover
        _STATE.enabled = False  # pragma: no cover
        _STATE.warnings.append(f"Failed to configure Logfire telemetry: {exc}")  # pragma: no cover
        return False  # pragma: no cover

    _LOGFIRE_HANDLER = logfire.loguru_handler()
    _STATE.configured = True
    return True


def telemetry_enabled() -> bool:
    """Return True when telemetry is both enabled and configured."""
    return _STATE.enabled and _STATE.configured


def get_logfire_handler() -> dict[str, Any] | None:
    """Return the active Logfire `loguru` handler, if any."""
    return _LOGFIRE_HANDLER


def pop_telemetry_warnings() -> list[str]:
    """Return and clear pending telemetry warnings."""
    warnings = list(_STATE.warnings)
    _STATE.warnings.clear()
    return warnings


def bind_telemetry_context(**attrs: Any):
    """Bind stable telemetry attributes onto the shared Loguru logger."""
    return logger.bind(**_filter_attributes(attrs))


@contextmanager
def span(name: str, **attrs: Any) -> Iterator[None]:
    """Create a manual Logfire span when telemetry is enabled."""
    if not telemetry_enabled():
        yield
        return

    logfire = _load_logfire()
    if logfire is None:  # pragma: no cover
        yield  # pragma: no cover
        return  # pragma: no cover

    with logfire.span(name, **_filter_attributes(attrs)):
        yield


__all__ = [
    "bind_telemetry_context",
    "configure_telemetry",
    "get_logfire_handler",
    "pop_telemetry_warnings",
    "reset_telemetry_state",
    "span",
    "telemetry_enabled",
]
