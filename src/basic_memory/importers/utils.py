"""Utility functions for import services."""

import re
from datetime import datetime
from typing import Any


def clean_filename(name: str) -> str:  # pragma: no cover
    """Clean a string to be used as a filename.

    Args:
        name: The string to clean.

    Returns:
        A cleaned string suitable for use as a filename.
    """
    # Replace common punctuation and whitespace with underscores
    name = re.sub(r"[\s\-,.:/\\\[\]\(\)]+", "_", name)
    # Remove any non-alphanumeric or underscore characters
    name = re.sub(r"[^\w]+", "", name)
    # Ensure the name isn't too long
    if len(name) > 100:  # pragma: no cover
        name = name[:100]
    # Ensure the name isn't empty
    if not name:  # pragma: no cover
        name = "untitled"
    return name


def format_timestamp(timestamp: Any) -> str:  # pragma: no cover
    """Format a timestamp for use in a filename or title.

    Args:
        timestamp: A timestamp in various formats.

    Returns:
        A formatted string representation of the timestamp.
    """
    if isinstance(timestamp, str):
        try:
            # Try ISO format
            timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError:
            try:
                # Try unix timestamp as string
                timestamp = datetime.fromtimestamp(float(timestamp))
            except ValueError:
                # Return as is if we can't parse it
                return timestamp
    elif isinstance(timestamp, (int, float)):
        # Unix timestamp
        timestamp = datetime.fromtimestamp(timestamp)

    if isinstance(timestamp, datetime):
        return timestamp.strftime("%Y-%m-%d %H:%M:%S")

    # Return as is if we can't format it
    return str(timestamp)  # pragma: no cover
