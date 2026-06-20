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
from basic_memory.index.project_index import (
    INDEX_PROJECT_ENTRYPOINT,
    ProjectIndexBatchEnqueuer,
    ProjectIndexCoordinatorResult,
    ProjectIndexFanoutFailureRecorder,
    ProjectIndexJobPayload,
    ProjectIndexObservedFileSource,
    ProjectIndexOrphanCleaner,
    ProjectIndexWorkflowRequest,
    ProjectIndexWorkflowStarter,
    build_project_index_workflow_queued,
    project_index_workflow_logical_key,
    run_project_index_coordinator,
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
    "INDEX_PROJECT_ENTRYPOINT",
    "ProjectIndexBatchEnqueuer",
    "ProjectIndexCoordinatorResult",
    "ProjectIndexFanoutFailureRecorder",
    "ProjectIndexJobPayload",
    "ProjectIndexObservedFileSource",
    "ProjectIndexOrphanCleaner",
    "ProjectIndexWorkflowRequest",
    "ProjectIndexWorkflowStarter",
    "StorageEventIndexRuntime",
    "StorageEventOperationProcessorFactory",
    "StorageEventProjectResolver",
    "build_project_index_workflow_queued",
    "local_storage_event_input_from_watchfiles_change",
    "local_storage_event_inputs_from_watchfiles_changes",
    "local_storage_events_from_watchfiles_changes",
    "project_index_workflow_logical_key",
    "run_project_index_coordinator",
    "run_storage_event_indexing",
]
