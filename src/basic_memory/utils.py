"""Utility functions for basic-memory."""

# Set environment variable before importing logfire to suppress warnings
import os

os.environ["LOGFIRE_IGNORE_NO_CONFIG"] = "1"

import logging
import re
import sys
from pathlib import Path
from typing import Optional, Protocol, Union, runtime_checkable

from loguru import logger
from unidecode import unidecode

import basic_memory
import logfire


@runtime_checkable
class PathLike(Protocol):
    """Protocol for objects that can be used as paths."""

    def __str__(self) -> str: ...


# In type annotations, use Union[Path, str] instead of FilePath for now
# This preserves compatibility with existing code while we migrate
FilePath = Union[Path, str]

# Disable the "Queue is full" warning
logging.getLogger("opentelemetry.sdk.metrics._internal.instrument").setLevel(logging.ERROR)


def generate_permalink(file_path: Union[Path, str, PathLike]) -> str:
    """Generate a stable permalink from a file path.

    Args:
        file_path: Original file path (str, Path, or PathLike)

    Returns:
        Normalized permalink that matches validation rules. Converts spaces and underscores
        to hyphens for consistency.

    Examples:
        >>> generate_permalink("docs/My Feature.md")
        'docs/my-feature'
        >>> generate_permalink("specs/API (v2).md")
        'specs/api-v2'
        >>> generate_permalink("design/unified_model_refactor.md")
        'design/unified-model-refactor'
    """
    # Convert Path to string if needed
    path_str = str(file_path)

    # Remove extension
    base = os.path.splitext(path_str)[0]

    # Transliterate unicode to ascii
    ascii_text = unidecode(base)

    # Insert dash between camelCase
    ascii_text = re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", ascii_text)

    # Convert to lowercase
    lower_text = ascii_text.lower()

    # replace underscores with hyphens
    text_with_hyphens = lower_text.replace("_", "-")

    # Replace remaining invalid chars with hyphens
    clean_text = re.sub(r"[^a-z0-9/\-]", "-", text_with_hyphens)

    # Collapse multiple hyphens
    clean_text = re.sub(r"-+", "-", clean_text)

    # Clean each path segment
    segments = clean_text.split("/")
    clean_segments = [s.strip("-") for s in segments]

    return "/".join(clean_segments)


def setup_logging(
    env: str,
    home_dir: Path,
    log_file: Optional[str] = None,
    log_level: str = "INFO",
    console: bool = True,
) -> None:  # pragma: no cover
    """
    Configure logging for the application.

    Args:
        env: The environment name (dev, test, prod)
        home_dir: The root directory for the application
        log_file: The name of the log file to write to
        log_level: The logging level to use
        console: Whether to log to the console
    """
    # Remove default handler and any existing handlers
    logger.remove()

    # Add file handler if we are not running tests and a log file is specified
    if log_file and env != "test":
        try:
            # Only configure logfire if API key is set - avoids interactive prompts
            if "LOGFIRE_TOKEN" in os.environ:
                # Configure logfire with code source info
                logfire.configure(
                    code_source=logfire.CodeSource(
                        repository="https://github.com/basicmachines-co/basic-memory",
                        revision=f"v{basic_memory.__version__}" if env != "dev" else "HEAD",
                    ),
                    environment=env,
                    console=False,
                )
                logger.configure(handlers=[logfire.loguru_handler()])

                # Instrument code spans for better observability
                logfire.instrument_sqlite3()
                logfire.instrument_httpx()
        except Exception as e:
            logger.warning(f"Failed to configure logfire: {e}")

        # Setup file logger
        log_path = home_dir / log_file
        logger.add(
            str(log_path),
            level=log_level,
            rotation="10 MB",
            retention="10 days",
            backtrace=True,
            diagnose=True,
            enqueue=True,
            colorize=False,
        )

    # Add console logger if requested or in test mode
    if env == "test" or console:
        logger.add(sys.stderr, level=log_level, backtrace=True, diagnose=True, colorize=True)

    logger.info(f"ENV: '{env}' Log level: '{log_level}' Logging to {log_file}")

    # Reduce noise from third-party libraries
    noisy_loggers = {
        # HTTP client logs
        "httpx": logging.WARNING,
        # File watching logs
        "watchfiles.main": logging.WARNING,
        # Instrumentation noise
        "instrumentor": logging.ERROR,
        "opentelemetry.instrumentation.instrumentor": logging.ERROR,
        "opentelemetry.instrumentation": logging.ERROR,
        "logfire.instrumentor": logging.ERROR,
        "opentelemetry.sdk.metrics._internal.instrument": logging.ERROR,
    }

    # Set log levels for noisy loggers
    for logger_name, level in noisy_loggers.items():
        logging.getLogger(logger_name).setLevel(level)
