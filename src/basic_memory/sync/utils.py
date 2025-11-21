"""Utilities for sync operations."""

from typing import Iterator, List, TypeVar

T = TypeVar("T")


def chunks(items: List[T], size: int) -> Iterator[List[T]]:
    """Split a list into chunks of specified size.

    Args:
        items: List of items to chunk
        size: Size of each chunk

    Yields:
        Lists of items, each of specified size (last chunk may be smaller)

    Example:
        >>> list(chunks([1, 2, 3, 4, 5], 2))
        [[1, 2], [3, 4], [5]]
    """
    for i in range(0, len(items), size):
        yield items[i : i + size]
