"""Utility functions for basic-memory."""

import os
import re
import sys
import unicodedata
from pathlib import Path
from typing import Optional, Union

from loguru import logger
from unidecode import unidecode

from basic_memory.config import config


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


def setup_logging(home_dir: Path = config.home, log_file: Optional[str] = None) -> None:
    """
    Configure logging for the application.
    """

    # Remove default handler and any existing handlers
    logger.remove()

    # Add file handler
    if log_file:
        log_path = home_dir / log_file
        logger.add(
            str(log_path),  # loguru expects a string path
            level=config.log_level,
            rotation="100 MB",
            retention="10 days",
            backtrace=True,
            diagnose=True,
            enqueue=True,
            colorize=False,
        )

    # Add stderr handler
    logger.add(sys.stderr, level=config.log_level, backtrace=True, diagnose=True, colorize=True)
