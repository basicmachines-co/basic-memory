"""Tests for portable scheduler task contracts."""

import pytest

from basic_memory.runtime import (
    RuntimeScheduledProjectIndexTask,
    RuntimeScheduledTaskName,
    RuntimeScheduledVectorSyncTask,
    runtime_scheduled_task_from_payload,
)


def test_runtime_scheduled_task_from_payload_maps_vector_sync() -> None:
    task = runtime_scheduled_task_from_payload(
        RuntimeScheduledTaskName.sync_entity_vectors,
        {"entity_id": 42, "project_id": 7},
    )

    assert task == RuntimeScheduledVectorSyncTask(entity_id=42, project_id=7)


def test_runtime_scheduled_task_from_payload_maps_project_index() -> None:
    task = runtime_scheduled_task_from_payload(
        RuntimeScheduledTaskName.index_project,
        {"project_id": 7, "force_full": True},
    )

    assert task == RuntimeScheduledProjectIndexTask(project_id=7, force_full=True)


def test_runtime_scheduled_task_from_payload_defaults_project_index_force_full() -> None:
    task = runtime_scheduled_task_from_payload(
        RuntimeScheduledTaskName.index_project,
        {"project_id": 7},
    )

    assert task == RuntimeScheduledProjectIndexTask(project_id=7)


def test_runtime_scheduled_task_from_payload_ignores_unknown_task() -> None:
    task = runtime_scheduled_task_from_payload("other_task", {"project_id": 7})

    assert task is None


def test_runtime_scheduled_task_from_payload_rejects_missing_required_fields() -> None:
    with pytest.raises(ValueError, match="sync_entity_vectors requires entity_id and project_id"):
        runtime_scheduled_task_from_payload(
            RuntimeScheduledTaskName.sync_entity_vectors,
            {"entity_id": 42},
        )

    with pytest.raises(ValueError, match="index_project requires project_id"):
        runtime_scheduled_task_from_payload(RuntimeScheduledTaskName.index_project, {})


def test_runtime_scheduled_task_from_payload_rejects_untyped_values() -> None:
    with pytest.raises(TypeError, match="sync_entity_vectors entity_id must be an int"):
        runtime_scheduled_task_from_payload(
            RuntimeScheduledTaskName.sync_entity_vectors,
            {"entity_id": "42", "project_id": 7},
        )

    with pytest.raises(TypeError, match="index_project force_full must be a bool"):
        runtime_scheduled_task_from_payload(
            RuntimeScheduledTaskName.index_project,
            {"project_id": 7, "force_full": "yes"},
        )
