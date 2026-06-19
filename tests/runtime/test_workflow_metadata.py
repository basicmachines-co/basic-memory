"""Tests for portable workflow metadata helpers."""

from basic_memory.runtime import merge_runtime_workflow_metadata_patch


def test_merge_runtime_workflow_metadata_patch_recurses_into_nested_mappings() -> None:
    metadata = {
        "phase": "queued",
        "transport": {
            "broker": "pgq",
            "entrypoint": "index_project",
            "pgq_job_id": "old-job",
        },
        "checkpoint": {
            "page": 1,
            "cursor": "abc",
        },
    }
    patch = {
        "phase": "running",
        "transport": {
            "pgq_job_id": "new-job",
            "state": "running",
        },
        "checkpoint": {
            "page": 2,
        },
    }

    assert merge_runtime_workflow_metadata_patch(metadata, patch) == {
        "phase": "running",
        "transport": {
            "broker": "pgq",
            "entrypoint": "index_project",
            "pgq_job_id": "new-job",
            "state": "running",
        },
        "checkpoint": {
            "page": 2,
            "cursor": "abc",
        },
    }


def test_merge_runtime_workflow_metadata_patch_replaces_non_mapping_values() -> None:
    metadata = {"checkpoint": {"cursor": "abc"}, "result": "legacy"}
    patch = {"checkpoint": None, "result": {"count": 2}}

    assert merge_runtime_workflow_metadata_patch(metadata, patch) == {
        "checkpoint": None,
        "result": {"count": 2},
    }
