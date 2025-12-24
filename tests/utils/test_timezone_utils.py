"""Tests for timezone utilities."""

from datetime import datetime, timezone


from basic_memory.utils import ensure_timezone_aware


class TestEnsureTimezoneAware:
    """Tests for ensure_timezone_aware function."""

    def test_already_timezone_aware_returns_unchanged(self):
        """Timezone-aware datetime should be returned unchanged."""
        dt = datetime(2024, 1, 15, 12, 30, 0, tzinfo=timezone.utc)
        result = ensure_timezone_aware(dt)
        assert result == dt
        assert result.tzinfo == timezone.utc

    def test_naive_datetime_cloud_mode_true_interprets_as_utc(self):
        """In cloud mode, naive datetimes should be interpreted as UTC."""
        naive_dt = datetime(2024, 1, 15, 12, 30, 0)
        result = ensure_timezone_aware(naive_dt, cloud_mode=True)

        # Should have UTC timezone
        assert result.tzinfo == timezone.utc
        # Time values should be unchanged (just tagged as UTC)
        assert result.year == 2024
        assert result.month == 1
        assert result.day == 15
        assert result.hour == 12
        assert result.minute == 30

    def test_naive_datetime_cloud_mode_false_interprets_as_local(self):
        """In local mode, naive datetimes should be interpreted as local time."""
        naive_dt = datetime(2024, 1, 15, 12, 30, 0)
        result = ensure_timezone_aware(naive_dt, cloud_mode=False)

        # Should have some timezone info (local)
        assert result.tzinfo is not None
        # The datetime should be converted to local timezone
        # We can't assert exact timezone as it depends on system

    def test_cloud_mode_true_does_not_shift_time(self):
        """Cloud mode should use replace() not astimezone() - time values unchanged."""
        naive_dt = datetime(2024, 6, 15, 18, 0, 0)  # Summer time
        result = ensure_timezone_aware(naive_dt, cloud_mode=True)

        # Hour should remain 18, not be shifted by timezone offset
        assert result.hour == 18
        assert result.tzinfo == timezone.utc

    def test_explicit_cloud_mode_skips_config_loading(self):
        """When cloud_mode is explicitly passed, config should not be loaded."""
        # This test verifies we can call ensure_timezone_aware without
        # triggering ConfigManager import when cloud_mode is explicit
        naive_dt = datetime(2024, 1, 15, 12, 30, 0)

        # Should work without any config setup
        result_cloud = ensure_timezone_aware(naive_dt, cloud_mode=True)
        assert result_cloud.tzinfo == timezone.utc

        result_local = ensure_timezone_aware(naive_dt, cloud_mode=False)
        assert result_local.tzinfo is not None

    def test_none_cloud_mode_falls_back_to_config(self):
        """When cloud_mode is None, should load from config."""
        from unittest.mock import patch, MagicMock

        naive_dt = datetime(2024, 1, 15, 12, 30, 0)

        # Mock ConfigManager to return cloud_mode_enabled=True
        mock_config = MagicMock()
        mock_config.cloud_mode_enabled = True

        with patch("basic_memory.config.ConfigManager") as mock_manager:
            mock_manager.return_value.config = mock_config
            result = ensure_timezone_aware(naive_dt, cloud_mode=None)

        # Should have used cloud mode (UTC)
        assert result.tzinfo == timezone.utc

    def test_asyncpg_naive_utc_scenario(self):
        """Simulate asyncpg returning naive datetime that's actually UTC.

        asyncpg binary protocol returns timestamps in UTC but as naive datetimes.
        In cloud mode, we interpret these as UTC rather than local time.
        """
        # Simulate what asyncpg returns: a naive datetime that's actually UTC
        asyncpg_result = datetime(2024, 1, 15, 18, 30, 0)  # 6:30 PM UTC

        # In cloud mode, interpret as UTC
        cloud_result = ensure_timezone_aware(asyncpg_result, cloud_mode=True)
        assert cloud_result == datetime(2024, 1, 15, 18, 30, 0, tzinfo=timezone.utc)

        # The hour should remain 18, not shifted
        assert cloud_result.hour == 18
