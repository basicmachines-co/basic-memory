"""Tests for the project-index orchestration surface."""

from inspect import signature

from basic_memory import index


def test_index_package_exports_project_index_fanout_contracts() -> None:
    """Project-wide fanout orchestration is owned by ``basic_memory.index``."""
    assert index.INDEX_PROJECT_ENTRYPOINT == "index_project"

    expected_contracts = {
        "ProjectIndexJobPayload": index.ProjectIndexJobPayload,
        "ProjectIndexWorkflowRequest": index.ProjectIndexWorkflowRequest,
        "ProjectIndexCoordinatorResult": index.ProjectIndexCoordinatorResult,
        "ProjectIndexObservedFileSource": index.ProjectIndexObservedFileSource,
        "ProjectIndexChangeDetector": index.ProjectIndexChangeDetector,
        "ProjectIndexMaintenanceRunner": index.ProjectIndexMaintenanceRunner,
        "ProjectIndexWorkflowStarter": index.ProjectIndexWorkflowStarter,
        "ProjectIndexBatchEnqueuer": index.ProjectIndexBatchEnqueuer,
        "ProjectIndexFanoutFailureRecorder": index.ProjectIndexFanoutFailureRecorder,
        "StoreProjectIndexMaintenanceRunner": index.StoreProjectIndexMaintenanceRunner,
    }
    for expected_name, contract in expected_contracts.items():
        assert contract.__name__ == expected_name

    assert callable(index.build_project_index_workflow_queued)
    assert callable(index.project_index_workflow_logical_key)
    assert callable(index.run_project_index_coordinator)


def test_index_package_exports_local_event_index_runtime_contracts() -> None:
    """Local event-index adapters are available from the core index surface."""
    assert index.LOCAL_EVENT_INDEX_TENANT_ID.hex == "00000000000000000000000000000000"
    assert index.LocalWatchEventIndexRuntimeFactory.__name__ == (
        "LocalWatchEventIndexRuntimeFactory"
    )
    assert callable(index.local_project_prefix)


def test_index_package_local_factories_are_not_sync_service_adapters() -> None:
    """The new index runtime must not depend on the legacy SyncService shape."""
    factory_signatures = (
        signature(index.LocalWatchEventIndexRuntimeFactory),
        signature(index.LocalProjectIndexRuntimeFactory),
    )

    for factory_signature in factory_signatures:
        assert "sync_service_factory" not in factory_signature.parameters


def test_index_package_exports_storage_event_source_contracts() -> None:
    """Storage ingress adapters should depend on the index-owned event surface."""
    assert index.StorageEventInput.__name__ == "StorageEventInput"
    assert index.StorageEventPayload.__name__ == "StorageEventPayload"
    assert index.StorageEventSource.__name__ == "StorageEventSource"
    assert index.RuntimeStorageEventSource.__name__ == "RuntimeStorageEventSource"
    assert index.StorageBucketName.__name__ == "StorageBucketName"
    assert index.StorageKey.__name__ == "StorageKey"
    assert index.StorageEtag.__name__ == "StorageEtag"
    assert index.StorageEventName.__name__ == "StorageEventName"
    assert callable(index.storage_event_payload_from_input)
    assert callable(index.group_storage_events_by_bucket)
