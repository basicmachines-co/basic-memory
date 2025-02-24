"""Utility functions for basic-memory."""

import logging
import os
import re
import sys
from pathlib import Path
from typing import Optional, Union

from loguru import logger
from unidecode import unidecode

import basic_memory

import logfire


def generate_permalink(file_path: Union[Path, str]) -> str:
    """Generate a stable permalink from a file path.

    Args:
        file_path: Original file path

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
    :param home_dir: the root directory for the application
    :param log_file: the name of the log file to write to
    :param app: the fastapi application instance
    :param console: whether to log to the console
    """

    # Remove default handler and any existing handlers
    logger.remove()

    # Add file handler if we are not running tests
    if log_file and env != "test":
        # enable pydantic logfire
        logfire.configure(
            code_source=logfire.CodeSource(
                repository="https://github.com/basicmachines-co/basic-memory",
                revision=basic_memory.__version__,
            ),
            environment=env,
            console=False,
        )
        logger.configure(handlers=[logfire.loguru_handler()])

        # instrument code spans
        logfire.instrument_sqlite3()
        logfire.instrument_httpx()

        # setup logger
        log_path = home_dir / log_file
        logger.add(
            str(log_path),
            level=log_level,
            rotation="100 MB",
            retention="10 days",
            backtrace=True,
            diagnose=True,
            enqueue=True,
            colorize=False,
        )

    if env == "test" or console:
        # Add stderr handler
        logger.add(sys.stderr, level=log_level, backtrace=True, diagnose=True, colorize=True)

    logger.info(f"ENV: '{env}' Log level: '{log_level}' Logging to {log_file}")

    # Get the logger for 'httpx'
    httpx_logger = logging.getLogger("httpx")
    # Set the logging level to WARNING to ignore INFO and DEBUG logs
    httpx_logger.setLevel(logging.WARNING)

    # turn watchfiles to WARNING
    logging.getLogger("watchfiles.main").setLevel(logging.WARNING)
