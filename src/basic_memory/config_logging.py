"""Entrypoint-specific logging and telemetry configuration."""

from typing import Protocol

from basic_memory.config_models import BasicMemoryConfig


class ConfigureTelemetry(Protocol):
    def __call__(
        self,
        service_name: str,
        *,
        environment: str,
        service_version: str | None = None,
        enable_logfire: bool = False,
        send_to_logfire: bool = False,
        log_level: str = "INFO",
    ) -> bool: ...


class SetupLogging(Protocol):
    def __call__(
        self,
        log_level: str = "INFO",
        log_to_file: bool = False,
        log_to_stdout: bool = False,
        structured_context: bool = False,
    ) -> None: ...


def configure_logfire_for_entrypoint(
    entrypoint: str,
    *,
    config: BasicMemoryConfig,
    service_version: str,
    configure_telemetry: ConfigureTelemetry,
) -> None:
    """Configure optional Logfire telemetry for a specific entrypoint."""
    configure_telemetry(
        service_name=f"{config.logfire_service_name}-{entrypoint}",
        environment=config.logfire_environment or config.env,
        service_version=service_version,
        enable_logfire=config.logfire_enabled,
        send_to_logfire=config.logfire_send_to_logfire,
    )


def initialize_file_logging(
    *,
    log_level: str,
    setup_logging: SetupLogging,
) -> None:
    """Initialize an entrypoint that must keep stdout protocol-clean."""
    setup_logging(log_level=log_level, log_to_file=True)


def initialize_api_logging(
    *,
    log_level: str,
    cloud_mode: bool,
    setup_logging: SetupLogging,
) -> None:
    """Initialize API file logging locally or structured stderr in Cloud."""
    if cloud_mode:
        setup_logging(log_level=log_level, log_to_stdout=True, structured_context=True)
    else:
        setup_logging(log_level=log_level, log_to_file=True)
