"""Tests for async_client force_local_mode functionality."""

import os


from basic_memory.mcp.async_client import _force_local_mode


class TestForceLocalMode:
    """Tests for _force_local_mode function."""

    def test_returns_false_when_not_set(self):
        """Should return False when env var is not set."""
        os.environ.pop("BASIC_MEMORY_FORCE_LOCAL", None)
        assert _force_local_mode() is False

    def test_returns_true_for_true(self):
        """Should return True when env var is 'true'."""
        os.environ["BASIC_MEMORY_FORCE_LOCAL"] = "true"
        try:
            assert _force_local_mode() is True
        finally:
            os.environ.pop("BASIC_MEMORY_FORCE_LOCAL", None)

    def test_returns_true_for_1(self):
        """Should return True when env var is '1'."""
        os.environ["BASIC_MEMORY_FORCE_LOCAL"] = "1"
        try:
            assert _force_local_mode() is True
        finally:
            os.environ.pop("BASIC_MEMORY_FORCE_LOCAL", None)

    def test_returns_true_for_yes(self):
        """Should return True when env var is 'yes'."""
        os.environ["BASIC_MEMORY_FORCE_LOCAL"] = "yes"
        try:
            assert _force_local_mode() is True
        finally:
            os.environ.pop("BASIC_MEMORY_FORCE_LOCAL", None)

    def test_returns_true_for_TRUE_uppercase(self):
        """Should return True when env var is 'TRUE' (case insensitive)."""
        os.environ["BASIC_MEMORY_FORCE_LOCAL"] = "TRUE"
        try:
            assert _force_local_mode() is True
        finally:
            os.environ.pop("BASIC_MEMORY_FORCE_LOCAL", None)

    def test_returns_false_for_false(self):
        """Should return False when env var is 'false'."""
        os.environ["BASIC_MEMORY_FORCE_LOCAL"] = "false"
        try:
            assert _force_local_mode() is False
        finally:
            os.environ.pop("BASIC_MEMORY_FORCE_LOCAL", None)

    def test_returns_false_for_0(self):
        """Should return False when env var is '0'."""
        os.environ["BASIC_MEMORY_FORCE_LOCAL"] = "0"
        try:
            assert _force_local_mode() is False
        finally:
            os.environ.pop("BASIC_MEMORY_FORCE_LOCAL", None)

    def test_returns_false_for_empty(self):
        """Should return False when env var is empty string."""
        os.environ["BASIC_MEMORY_FORCE_LOCAL"] = ""
        try:
            assert _force_local_mode() is False
        finally:
            os.environ.pop("BASIC_MEMORY_FORCE_LOCAL", None)

    def test_returns_false_for_random_string(self):
        """Should return False when env var is random string."""
        os.environ["BASIC_MEMORY_FORCE_LOCAL"] = "random"
        try:
            assert _force_local_mode() is False
        finally:
            os.environ.pop("BASIC_MEMORY_FORCE_LOCAL", None)
