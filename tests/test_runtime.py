"""Tests for runtime mode resolution."""


from basic_memory.runtime import RuntimeMode, resolve_runtime_mode


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
        """Test environment takes precedence over cloud mode."""
        mode = resolve_runtime_mode(cloud_mode_enabled=True, is_test_env=True)
        assert mode == RuntimeMode.TEST

    def test_resolves_to_cloud_when_enabled(self):
        """Cloud mode is used when enabled and not in test env."""
        mode = resolve_runtime_mode(cloud_mode_enabled=True, is_test_env=False)
        assert mode == RuntimeMode.CLOUD

    def test_resolves_to_local_by_default(self):
        """Local mode is the default when no other modes apply."""
        mode = resolve_runtime_mode(cloud_mode_enabled=False, is_test_env=False)
        assert mode == RuntimeMode.LOCAL

    def test_test_env_overrides_cloud_mode(self):
        """Test environment should override cloud mode."""
        # When both are enabled, test takes precedence
        mode = resolve_runtime_mode(cloud_mode_enabled=True, is_test_env=True)
        assert mode == RuntimeMode.TEST
        assert mode.is_test is True
        assert mode.is_cloud is False
