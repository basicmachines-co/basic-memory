"""Tests for runtime mode resolution."""

from dataclasses import FrozenInstanceError
from datetime import timedelta

import pytest

from basic_memory.runtime import RuntimeMode, resolve_runtime_mode
from basic_memory.runtime.contracts import (
    RuntimeCapabilities,
    RuntimeJobCounts,
    RuntimeJobRequest,
    StorageObjectIdentity,
)


class TestRuntimeMode:
    """Tests for RuntimeMode enum."""

    def test_local_mode_properties(self):
        mode = RuntimeMode.LOCAL
        assert mode.is_local is True
        assert mode.is_cloud is False
        assert mode.is_test is False

    def test_cloud_mode_properties(self):
        mode = RuntimeMode.CLOUD
        assert mode.is_local is False
        assert mode.is_cloud is True
        assert mode.is_test is False

    def test_test_mode_properties(self):
        mode = RuntimeMode.TEST
        assert mode.is_local is False
        assert mode.is_cloud is False
        assert mode.is_test is True


class TestResolveRuntimeMode:
    """Tests for resolve_runtime_mode function."""

    def test_resolves_to_test_when_test_env(self):
        """Test environment resolves to TEST mode."""
        mode = resolve_runtime_mode(is_test_env=True)
        assert mode == RuntimeMode.TEST

    def test_resolves_to_local_when_not_test_env(self):
        """Non-test environments resolve to LOCAL mode."""
        mode = resolve_runtime_mode(is_test_env=False)
        assert mode == RuntimeMode.LOCAL

    def test_never_resolves_to_cloud_in_local_app_context(self):
        """Resolver no longer returns CLOUD for local app composition roots."""
        mode = resolve_runtime_mode(is_test_env=False)
        assert mode is not RuntimeMode.CLOUD


class TestRuntimeContracts:
    """Tests for portable runtime contracts shared with hosted adapters."""

    def test_storage_object_identity_splits_project_relative_paths(self):
        identity = StorageObjectIdentity(bucket_name="memory-bucket", key="project/notes/a.md")

        assert identity.project_path == "project"
        assert identity.relative_path == "notes/a.md"

    def test_runtime_job_request_is_immutable(self):
        request = RuntimeJobRequest(
            entrypoint="index_file",
            payload=b"{}",
            execute_after=timedelta(seconds=30),
            headers={"tenant_id": "tenant-1"},
        )

        with pytest.raises(FrozenInstanceError):
            setattr(request, "entrypoint", "other")

    def test_runtime_job_counts_are_immutable_accumulators(self):
        result = RuntimeJobCounts().with_processed(2).with_failed().add(RuntimeJobCounts(skipped=3))

        assert result.as_dict() == {"processed": 2, "failed": 1, "skipped": 3}

    def test_runtime_capabilities_fail_fast_when_factories_are_missing(self):
        capabilities: RuntimeCapabilities[object, object] = RuntimeCapabilities()

        with pytest.raises(RuntimeError, match="Snapshot provider factory"):
            capabilities.require_snapshot_provider_factory()

        with pytest.raises(RuntimeError, match="Note history provider factory"):
            capabilities.require_note_history_provider_factory()
