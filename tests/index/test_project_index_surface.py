"""Tests for the project-index orchestration surface."""

from collections.abc import Sequence
from dataclasses import MISSING, fields
from inspect import signature
from importlib.util import find_spec
from typing import get_type_hints

from basic_memory import index
from basic_memory import deps
from basic_memory.deps import services as service_deps
from basic_memory.index.local_dependencies import (
    LocalIndexEntityRepository,
    LocalIndexEntityService,
)
from basic_memory.indexing import IndexedFileChecksumRow
from basic_memory.markdown import EntityMarkdown
from basic_memory.models import Entity


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


def test_index_package_exports_local_watch_service_surface() -> None:
    """The local runtime watcher lives with event-index orchestration, not sync."""
    watch_signature = signature(index.WatchService)

    assert index.WatchService.__module__ == "basic_memory.index.watch_service"
    assert index.WatchServiceState.__name__ == "WatchServiceState"
    assert index.WatchEvent.__name__ == "WatchEvent"
    assert index.WatchCoordinator.__name__ == "WatchCoordinator"
    assert index.WatchStatus.__name__ == "WatchStatus"
    assert "event_index_runtime_factory" in watch_signature.parameters
    assert "sync_service_factory" not in watch_signature.parameters


def test_index_package_local_factories_are_not_sync_service_adapters() -> None:
    """The new index runtime must not depend on the legacy SyncService shape."""
    factory_signatures = (
        signature(index.LocalWatchEventIndexRuntimeFactory),
        signature(index.LocalProjectIndexRuntimeFactory),
    )

    for factory_signature in factory_signatures:
        assert "sync_service_factory" not in factory_signature.parameters


def test_sync_package_is_not_active_runtime_surface() -> None:
    """The legacy sync package is quarantined outside the active runtime package."""
    assert find_spec("basic_memory.sync") is None


def test_fastapi_deps_do_not_export_sync_service_dependencies() -> None:
    """Runtime dependency injection should no longer construct SyncService."""
    sync_dep_names = {
        "get_sync_service",
        "SyncServiceDep",
        "get_sync_service_v2",
        "SyncServiceV2Dep",
        "get_sync_service_v2_external",
        "SyncServiceV2ExternalDep",
    }

    for name in sync_dep_names:
        assert not hasattr(service_deps, name)
        assert not hasattr(deps, name)
        assert name not in deps.__all__


def test_inline_storage_event_runtime_requires_explicit_result_recorder() -> None:
    """Inline runtimes should receive local/cloud observer behavior explicitly."""
    result_recorder_field = next(
        field
        for field in fields(index.InlineStorageEventIndexRuntime)
        if field.name == "result_recorder"
    )

    assert result_recorder_field.default is MISSING
    assert result_recorder_field.default_factory is MISSING


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


def test_local_index_dependency_contracts_use_domain_types() -> None:
    """Local index dependency protocols should expose behavior types, not broad Any."""
    checksum_hints = get_type_hints(LocalIndexEntityRepository.get_by_file_paths)
    find_hints = get_type_hints(LocalIndexEntityRepository.find_by_ids)
    update_hints = get_type_hints(LocalIndexEntityRepository.update)
    permalink_hints = get_type_hints(LocalIndexEntityService.resolve_permalink)

    assert checksum_hints["return"] == Sequence[IndexedFileChecksumRow]
    assert find_hints["ids"] == list[int]
    assert update_hints["entity_id"] is int
    assert update_hints["entity_data"] == dict[str, object] | Entity
    assert permalink_hints["markdown"] == EntityMarkdown | None
