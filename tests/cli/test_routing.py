"""Tests for CLI routing utilities."""

import os

import pytest

from basic_memory.cli.commands.routing import force_routing, validate_routing_flags


class TestValidateRoutingFlags:
    """Tests for validate_routing_flags function."""

    def test_neither_flag(self):
        """Should not raise when neither flag is set."""
        validate_routing_flags(local=False, cloud=False)

    def test_local_only(self):
        """Should not raise when only local is set."""
        validate_routing_flags(local=True, cloud=False)

    def test_cloud_only(self):
        """Should not raise when only cloud is set."""
        validate_routing_flags(local=False, cloud=True)

    def test_both_flags_raises(self):
        """Should raise ValueError when both flags are set."""
        with pytest.raises(ValueError, match="Cannot specify both --local and --cloud"):
            validate_routing_flags(local=True, cloud=True)


class TestForceRouting:
    """Tests for force_routing context manager."""

    def test_local_sets_env_var(self):
        """Local flag should set BASIC_MEMORY_FORCE_LOCAL."""
        # Ensure env var is not set
        os.environ.pop("BASIC_MEMORY_FORCE_LOCAL", None)

        with force_routing(local=True):
            assert os.environ.get("BASIC_MEMORY_FORCE_LOCAL") == "true"

        # Should be cleaned up after context exits
        assert os.environ.get("BASIC_MEMORY_FORCE_LOCAL") is None

    def test_cloud_clears_env_var(self):
        """Cloud flag should clear BASIC_MEMORY_FORCE_LOCAL if set."""
        # Set env var
        os.environ["BASIC_MEMORY_FORCE_LOCAL"] = "true"

        with force_routing(cloud=True):
            assert os.environ.get("BASIC_MEMORY_FORCE_LOCAL") is None

        # Should restore original value after context exits
        assert os.environ.get("BASIC_MEMORY_FORCE_LOCAL") == "true"

        # Cleanup
        os.environ.pop("BASIC_MEMORY_FORCE_LOCAL", None)

    def test_neither_flag_no_change(self):
        """Neither flag should not change env vars."""
        os.environ.pop("BASIC_MEMORY_FORCE_LOCAL", None)

        with force_routing():
            # Should not be set
            assert os.environ.get("BASIC_MEMORY_FORCE_LOCAL") is None

        # Should still not be set
        assert os.environ.get("BASIC_MEMORY_FORCE_LOCAL") is None

    def test_preserves_original_env_var(self):
        """Should restore original env var value after context exits."""
        original_value = "original"
        os.environ["BASIC_MEMORY_FORCE_LOCAL"] = original_value

        with force_routing(local=True):
            assert os.environ.get("BASIC_MEMORY_FORCE_LOCAL") == "true"

        # Should restore original value
        assert os.environ.get("BASIC_MEMORY_FORCE_LOCAL") == original_value

        # Cleanup
        os.environ.pop("BASIC_MEMORY_FORCE_LOCAL", None)

    def test_both_flags_raises(self):
        """Should raise ValueError when both flags are set."""
        with pytest.raises(ValueError, match="Cannot specify both --local and --cloud"):
            with force_routing(local=True, cloud=True):
                pass

    def test_restores_on_exception(self):
        """Should restore env vars even when exception is raised."""
        os.environ.pop("BASIC_MEMORY_FORCE_LOCAL", None)

        try:
            with force_routing(local=True):
                assert os.environ.get("BASIC_MEMORY_FORCE_LOCAL") == "true"
                raise RuntimeError("Test exception")
        except RuntimeError:
            pass

        # Should be cleaned up even after exception
        assert os.environ.get("BASIC_MEMORY_FORCE_LOCAL") is None
