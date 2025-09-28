"""Test minimum 1-day timeframe enforcement for timezone handling."""

from datetime import datetime, timedelta
import pytest
from freezegun import freeze_time

from basic_memory.schemas.base import parse_timeframe


class TestTimeframeMinimum:
    """Test that parse_timeframe enforces a minimum 1-day lookback."""

    @freeze_time("2025-01-15 15:00:00")
    def test_today_returns_one_day_ago(self):
        """Test that 'today' returns 1 day ago instead of start of today."""
        result = parse_timeframe("today")
        now = datetime.now()
        one_day_ago = now - timedelta(days=1)

        # Should be approximately 1 day ago (within a second for test tolerance)
        diff = abs((result.replace(tzinfo=None) - one_day_ago).total_seconds())
        assert diff < 1, f"Expected ~1 day ago, got {result}"

    @freeze_time("2025-01-15 15:00:00")
    def test_one_hour_returns_one_day_minimum(self):
        """Test that '1h' returns 1 day ago due to minimum enforcement."""
        result = parse_timeframe("1h")
        now = datetime.now()
        one_day_ago = now - timedelta(days=1)

        # Should be approximately 1 day ago, not 1 hour ago
        diff = abs((result.replace(tzinfo=None) - one_day_ago).total_seconds())
        assert diff < 1, f"Expected ~1 day ago for '1h', got {result}"

    @freeze_time("2025-01-15 15:00:00")
    def test_six_hours_returns_one_day_minimum(self):
        """Test that '6h' returns 1 day ago due to minimum enforcement."""
        result = parse_timeframe("6h")
        now = datetime.now()
        one_day_ago = now - timedelta(days=1)

        # Should be approximately 1 day ago, not 6 hours ago
        diff = abs((result.replace(tzinfo=None) - one_day_ago).total_seconds())
        assert diff < 1, f"Expected ~1 day ago for '6h', got {result}"

    @freeze_time("2025-01-15 15:00:00")
    def test_one_day_returns_one_day(self):
        """Test that '1d' correctly returns approximately 1 day ago."""
        result = parse_timeframe("1d")
        now = datetime.now()
        one_day_ago = now - timedelta(days=1)

        # Should be approximately 1 day ago (within 24 hours)
        diff_hours = abs((result.replace(tzinfo=None) - one_day_ago).total_seconds()) / 3600
        assert diff_hours < 24, (
            f"Expected ~1 day ago for '1d', got {result} (diff: {diff_hours} hours)"
        )

    @freeze_time("2025-01-15 15:00:00")
    def test_two_days_returns_two_days(self):
        """Test that '2d' correctly returns approximately 2 days ago (not affected by minimum)."""
        result = parse_timeframe("2d")
        now = datetime.now()
        two_days_ago = now - timedelta(days=2)

        # Should be approximately 2 days ago (within 24 hours)
        diff_hours = abs((result.replace(tzinfo=None) - two_days_ago).total_seconds()) / 3600
        assert diff_hours < 24, (
            f"Expected ~2 days ago for '2d', got {result} (diff: {diff_hours} hours)"
        )

    @freeze_time("2025-01-15 15:00:00")
    def test_one_week_returns_one_week(self):
        """Test that '1 week' correctly returns approximately 1 week ago (not affected by minimum)."""
        result = parse_timeframe("1 week")
        now = datetime.now()
        one_week_ago = now - timedelta(weeks=1)

        # Should be approximately 1 week ago (within 24 hours)
        diff_hours = abs((result.replace(tzinfo=None) - one_week_ago).total_seconds()) / 3600
        assert diff_hours < 24, (
            f"Expected ~1 week ago for '1 week', got {result} (diff: {diff_hours} hours)"
        )

    @freeze_time("2025-01-15 15:00:00")
    def test_zero_days_returns_one_day_minimum(self):
        """Test that '0d' returns 1 day ago due to minimum enforcement."""
        result = parse_timeframe("0d")
        now = datetime.now()
        one_day_ago = now - timedelta(days=1)

        # Should be approximately 1 day ago, not now
        diff = abs((result.replace(tzinfo=None) - one_day_ago).total_seconds())
        assert diff < 1, f"Expected ~1 day ago for '0d', got {result}"

    def test_timezone_awareness(self):
        """Test that returned datetime is timezone-aware."""
        result = parse_timeframe("1d")
        assert result.tzinfo is not None, "Expected timezone-aware datetime"

    def test_invalid_timeframe_raises_error(self):
        """Test that invalid timeframe strings raise ValueError."""
        with pytest.raises(ValueError, match="Could not parse timeframe"):
            parse_timeframe("invalid_timeframe")
