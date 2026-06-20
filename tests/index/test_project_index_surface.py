"""Tests for the project-index orchestration surface."""

from basic_memory import index


def test_index_package_exports_project_index_fanout_contracts() -> None:
    """Project-wide fanout orchestration is owned by ``basic_memory.index``."""
    assert index.INDEX_PROJECT_ENTRYPOINT == "index_project"

    expected_contracts = {
        "ProjectIndexJobPayload": index.ProjectIndexJobPayload,
        "ProjectIndexWorkflowRequest": index.ProjectIndexWorkflowRequest,
        "ProjectIndexCoordinatorResult": index.ProjectIndexCoordinatorResult,
        "ProjectIndexObservedFileSource": index.ProjectIndexObservedFileSource,
        "ProjectIndexOrphanCleaner": index.ProjectIndexOrphanCleaner,
        "ProjectIndexWorkflowStarter": index.ProjectIndexWorkflowStarter,
        "ProjectIndexBatchEnqueuer": index.ProjectIndexBatchEnqueuer,
        "ProjectIndexFanoutFailureRecorder": index.ProjectIndexFanoutFailureRecorder,
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
