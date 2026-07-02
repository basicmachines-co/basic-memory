"""Tests for the project-index orchestration surface."""

from collections.abc import Sequence
from dataclasses import MISSING, fields
from importlib.util import find_spec
from inspect import signature
from typing import get_type_hints

from basic_memory import deps
from basic_memory.deps import services as service_deps
from basic_memory.index.inline_operations import InlineStorageEventIndexRuntime
from basic_memory.index.local_dependencies import (
    LocalIndexEntityRepository,
    LocalIndexEntityService,
)
from basic_memory.index.local_project import LocalProjectIndexRuntimeFactory
from basic_memory.index.local_runtime import LocalWatchEventIndexRuntimeFactory
from basic_memory.index.watch_service import WatchService
from basic_memory.indexing.file_index_checking import IndexedFileChecksumRow
from basic_memory.markdown import EntityMarkdown
from basic_memory.models import Entity


def test_watch_service_uses_event_index_runtime_not_sync_service() -> None:
    """The local runtime watcher lives with event-index orchestration, not sync."""
    watch_signature = signature(WatchService)

    assert "event_index_runtime_factory" in watch_signature.parameters
    assert "sync_service_factory" not in watch_signature.parameters


def test_index_package_local_factories_are_not_sync_service_adapters() -> None:
    """The new index runtime must not depend on the legacy SyncService shape."""
    factory_signatures = (
        signature(LocalWatchEventIndexRuntimeFactory),
        signature(LocalProjectIndexRuntimeFactory),
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
        field for field in fields(InlineStorageEventIndexRuntime) if field.name == "result_recorder"
    )

    assert result_recorder_field.default is MISSING
    assert result_recorder_field.default_factory is MISSING


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
