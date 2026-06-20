"""Event-based indexing orchestration contracts."""

from basic_memory.index.filesystem import (
    LOCAL_FILESYSTEM_BUCKET_NAME,
    local_storage_event_input_from_watchfiles_change,
    local_storage_event_inputs_from_watchfiles_changes,
    local_storage_events_from_watchfiles_changes,
)
from basic_memory.index.inline_operations import (
    InlineStorageEventIndexRuntime,
    InlineStorageEventOperationProcessor,
    InlineStorageEventResultRecorder,
    NoopInlineStorageEventResultRecorder,
)
from basic_memory.index.storage_events import (
    StorageEventIndexRuntime,
    StorageEventOperationProcessorFactory,
    StorageEventProjectResolver,
    run_storage_event_indexing,
)

__all__ = [
    "LOCAL_FILESYSTEM_BUCKET_NAME",
    "InlineStorageEventIndexRuntime",
    "InlineStorageEventOperationProcessor",
    "InlineStorageEventResultRecorder",
    "NoopInlineStorageEventResultRecorder",
    "StorageEventIndexRuntime",
    "StorageEventOperationProcessorFactory",
    "StorageEventProjectResolver",
    "local_storage_event_input_from_watchfiles_change",
    "local_storage_event_inputs_from_watchfiles_changes",
    "local_storage_events_from_watchfiles_changes",
    "run_storage_event_indexing",
]
