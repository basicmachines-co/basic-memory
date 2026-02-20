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

    def test_local_sets_env_vars(self):
        """Local flag should set BASIC_MEMORY_FORCE_LOCAL and EXPLICIT_ROUTING."""
        os.environ.pop("BASIC_MEMORY_FORCE_LOCAL", None)
        os.environ.pop("BASIC_MEMORY_FORCE_CLOUD", None)
        os.environ.pop("BASIC_MEMORY_EXPLICIT_ROUTING", None)

        with force_routing(local=True):
            assert os.environ.get("BASIC_MEMORY_FORCE_LOCAL") == "true"
            assert os.environ.get("BASIC_MEMORY_FORCE_CLOUD") is None
            assert os.environ.get("BASIC_MEMORY_EXPLICIT_ROUTING") == "true"

        # Should be cleaned up after context exits
        assert os.environ.get("BASIC_MEMORY_FORCE_LOCAL") is None
        assert os.environ.get("BASIC_MEMORY_FORCE_CLOUD") is None
        assert os.environ.get("BASIC_MEMORY_EXPLICIT_ROUTING") is None

    def test_cloud_sets_explicit_routing(self):
        """Cloud flag should set FORCE_CLOUD + EXPLICIT_ROUTING and clear FORCE_LOCAL."""
        os.environ["BASIC_MEMORY_FORCE_LOCAL"] = "true"
        os.environ.pop("BASIC_MEMORY_FORCE_CLOUD", None)
        os.environ.pop("BASIC_MEMORY_EXPLICIT_ROUTING", None)

        with force_routing(cloud=True):
            assert os.environ.get("BASIC_MEMORY_FORCE_LOCAL") is None
            assert os.environ.get("BASIC_MEMORY_FORCE_CLOUD") == "true"
            assert os.environ.get("BASIC_MEMORY_EXPLICIT_ROUTING") == "true"

        # Should restore original values after context exits
        assert os.environ.get("BASIC_MEMORY_FORCE_LOCAL") == "true"
        assert os.environ.get("BASIC_MEMORY_FORCE_CLOUD") is None
        assert os.environ.get("BASIC_MEMORY_EXPLICIT_ROUTING") is None

        # Cleanup
        os.environ.pop("BASIC_MEMORY_FORCE_LOCAL", None)
        os.environ.pop("BASIC_MEMORY_FORCE_CLOUD", None)

    def test_neither_flag_no_change(self):
        """Neither flag should not change env vars."""
        os.environ.pop("BASIC_MEMORY_FORCE_LOCAL", None)
        os.environ.pop("BASIC_MEMORY_FORCE_CLOUD", None)
        os.environ.pop("BASIC_MEMORY_EXPLICIT_ROUTING", None)

        with force_routing():
            assert os.environ.get("BASIC_MEMORY_FORCE_LOCAL") is None
            assert os.environ.get("BASIC_MEMORY_FORCE_CLOUD") is None
            assert os.environ.get("BASIC_MEMORY_EXPLICIT_ROUTING") is None

        assert os.environ.get("BASIC_MEMORY_FORCE_LOCAL") is None
        assert os.environ.get("BASIC_MEMORY_FORCE_CLOUD") is None
        assert os.environ.get("BASIC_MEMORY_EXPLICIT_ROUTING") is None

    def test_preserves_original_env_vars(self):
        """Should restore original env var values after context exits."""
        os.environ["BASIC_MEMORY_FORCE_LOCAL"] = "original"
        os.environ["BASIC_MEMORY_FORCE_CLOUD"] = "original"
        os.environ["BASIC_MEMORY_EXPLICIT_ROUTING"] = "original"

        with force_routing(local=True):
            assert os.environ.get("BASIC_MEMORY_FORCE_LOCAL") == "true"
            assert os.environ.get("BASIC_MEMORY_FORCE_CLOUD") is None
            assert os.environ.get("BASIC_MEMORY_EXPLICIT_ROUTING") == "true"

        # Should restore original values
        assert os.environ.get("BASIC_MEMORY_FORCE_LOCAL") == "original"
        assert os.environ.get("BASIC_MEMORY_FORCE_CLOUD") == "original"
        assert os.environ.get("BASIC_MEMORY_EXPLICIT_ROUTING") == "original"

        # Cleanup
        os.environ.pop("BASIC_MEMORY_FORCE_LOCAL", None)
        os.environ.pop("BASIC_MEMORY_FORCE_CLOUD", None)
        os.environ.pop("BASIC_MEMORY_EXPLICIT_ROUTING", None)

    def test_both_flags_raises(self):
        """Should raise ValueError when both flags are set."""
        with pytest.raises(ValueError, match="Cannot specify both --local and --cloud"):
            with force_routing(local=True, cloud=True):
                pass

    def test_restores_on_exception(self):
        """Should restore env vars even when exception is raised."""
        os.environ.pop("BASIC_MEMORY_FORCE_LOCAL", None)
        os.environ.pop("BASIC_MEMORY_FORCE_CLOUD", None)
        os.environ.pop("BASIC_MEMORY_EXPLICIT_ROUTING", None)

        try:
            with force_routing(local=True):
                assert os.environ.get("BASIC_MEMORY_FORCE_LOCAL") == "true"
                assert os.environ.get("BASIC_MEMORY_FORCE_CLOUD") is None
                assert os.environ.get("BASIC_MEMORY_EXPLICIT_ROUTING") == "true"
                raise RuntimeError("Test exception")
        except RuntimeError:
            pass

        # Should be cleaned up even after exception
        assert os.environ.get("BASIC_MEMORY_FORCE_LOCAL") is None
        assert os.environ.get("BASIC_MEMORY_FORCE_CLOUD") is None
        assert os.environ.get("BASIC_MEMORY_EXPLICIT_ROUTING") is None
