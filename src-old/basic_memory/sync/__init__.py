"""Deprecated reference-only sync package.

Event-based indexing now lives under ``basic_memory.index``. This package is
retained only as source material while the replacement local runtime lands.
"""

from .coordinator import SyncCoordinator, SyncStatus
from .sync_service import SYNC_SERVICE_DEPRECATION_MESSAGE, SyncService
from .watch_service import SYNC_WATCH_SERVICE_DEPRECATION_MESSAGE, WatchService

SYNC_PACKAGE_DEPRECATION_MESSAGE = (
    "basic_memory.sync is a deprecated reference-only package. New local and cloud "
    "indexing work must use basic_memory.index; this package will be removed."
)

__all__ = [
    "SYNC_PACKAGE_DEPRECATION_MESSAGE",
    "SYNC_SERVICE_DEPRECATION_MESSAGE",
    "SYNC_WATCH_SERVICE_DEPRECATION_MESSAGE",
    "SyncService",
    "WatchService",
    "SyncCoordinator",
    "SyncStatus",
]
