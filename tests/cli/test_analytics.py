"""Tests for CLI analytics module."""

import json
from unittest.mock import patch, MagicMock

import pytest

from basic_memory.cli.analytics import (
    track,
    _analytics_disabled,
    _is_configured,
    EVENT_PROMO_SHOWN,
    EVENT_CLOUD_LOGIN_STARTED,
    EVENT_CLOUD_LOGIN_SUCCESS,
    EVENT_CLOUD_LOGIN_SUB_REQUIRED,
    EVENT_PROMO_OPTED_OUT,
)


class TestAnalyticsDisabled:
    def test_disabled_when_env_set(self, monkeypatch):
        monkeypatch.setenv("BASIC_MEMORY_NO_PROMOS", "1")
        assert _analytics_disabled() is True

    def test_disabled_when_env_true(self, monkeypatch):
        monkeypatch.setenv("BASIC_MEMORY_NO_PROMOS", "true")
        assert _analytics_disabled() is True

    def test_not_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("BASIC_MEMORY_NO_PROMOS", raising=False)
        assert _analytics_disabled() is False


class TestIsConfigured:
    def test_configured_when_both_set(self, monkeypatch):
        monkeypatch.setenv("BASIC_MEMORY_UMAMI_HOST", "https://analytics.example.com")
        monkeypatch.setenv("BASIC_MEMORY_UMAMI_SITE_ID", "abc-123")
        assert _is_configured() is True

    def test_not_configured_when_host_missing(self, monkeypatch):
        monkeypatch.delenv("BASIC_MEMORY_UMAMI_HOST", raising=False)
        monkeypatch.setenv("BASIC_MEMORY_UMAMI_SITE_ID", "abc-123")
        assert _is_configured() is False

    def test_not_configured_when_site_id_missing(self, monkeypatch):
        monkeypatch.setenv("BASIC_MEMORY_UMAMI_HOST", "https://analytics.example.com")
        monkeypatch.delenv("BASIC_MEMORY_UMAMI_SITE_ID", raising=False)
        assert _is_configured() is False

    def test_not_configured_when_empty_strings(self, monkeypatch):
        monkeypatch.setenv("BASIC_MEMORY_UMAMI_HOST", "")
        monkeypatch.setenv("BASIC_MEMORY_UMAMI_SITE_ID", "")
        assert _is_configured() is False


class TestTrack:
    def test_no_op_when_disabled(self, monkeypatch):
        monkeypatch.setenv("BASIC_MEMORY_NO_PROMOS", "1")
        with patch("basic_memory.cli.analytics.threading.Thread") as mock_thread:
            track("test-event")
            mock_thread.assert_not_called()

    def test_no_op_when_not_configured(self, monkeypatch):
        monkeypatch.delenv("BASIC_MEMORY_NO_PROMOS", raising=False)
        monkeypatch.delenv("BASIC_MEMORY_UMAMI_HOST", raising=False)
        monkeypatch.delenv("BASIC_MEMORY_UMAMI_SITE_ID", raising=False)
        with patch("basic_memory.cli.analytics.threading.Thread") as mock_thread:
            track("test-event")
            mock_thread.assert_not_called()

    def test_sends_event_when_configured(self, monkeypatch):
        monkeypatch.delenv("BASIC_MEMORY_NO_PROMOS", raising=False)
        monkeypatch.setenv("BASIC_MEMORY_UMAMI_HOST", "https://analytics.example.com")
        monkeypatch.setenv("BASIC_MEMORY_UMAMI_SITE_ID", "test-site-id")

        captured_target = None

        def fake_thread(target, daemon):
            nonlocal captured_target
            captured_target = target
            mock = MagicMock()
            return mock

        with patch("basic_memory.cli.analytics.threading.Thread", side_effect=fake_thread):
            track(EVENT_PROMO_SHOWN, {"trigger": "first_run"})

        assert captured_target is not None

    def test_send_hits_correct_url(self, monkeypatch):
        monkeypatch.delenv("BASIC_MEMORY_NO_PROMOS", raising=False)
        monkeypatch.setenv("BASIC_MEMORY_UMAMI_HOST", "https://analytics.example.com")
        monkeypatch.setenv("BASIC_MEMORY_UMAMI_SITE_ID", "test-site-id")

        captured_request = None

        def fake_urlopen(req, timeout=None):
            nonlocal captured_request
            captured_request = req
            return MagicMock()

        # Run the send function directly instead of in a thread
        with patch("basic_memory.cli.analytics.urllib.request.urlopen", fake_urlopen):
            with patch("basic_memory.cli.analytics.threading.Thread") as mock_thread:
                # Capture the target function and call it directly
                def run_target(target, daemon):
                    target()  # Execute synchronously
                    return MagicMock()

                mock_thread.side_effect = run_target
                track(EVENT_CLOUD_LOGIN_STARTED)

        assert captured_request is not None
        assert captured_request.full_url == "https://analytics.example.com/api/send"
        body = json.loads(captured_request.data)
        assert body["payload"]["name"] == "cli-cloud-login-started"
        assert body["payload"]["website"] == "test-site-id"
        assert body["payload"]["hostname"] == "cli.basicmemory.com"
        assert "version" in body["payload"]["data"]

    def test_send_failure_is_silent(self, monkeypatch):
        monkeypatch.delenv("BASIC_MEMORY_NO_PROMOS", raising=False)
        monkeypatch.setenv("BASIC_MEMORY_UMAMI_HOST", "https://analytics.example.com")
        monkeypatch.setenv("BASIC_MEMORY_UMAMI_SITE_ID", "test-site-id")

        def fake_urlopen(req, timeout=None):
            raise ConnectionError("Network down")

        with patch("basic_memory.cli.analytics.urllib.request.urlopen", fake_urlopen):
            with patch("basic_memory.cli.analytics.threading.Thread") as mock_thread:

                def run_target(target, daemon):
                    target()  # Should not raise
                    return MagicMock()

                mock_thread.side_effect = run_target
                # Should not raise
                track("test-event")


class TestEventConstants:
    """Verify event name constants exist and are kebab-case strings."""

    @pytest.mark.parametrize(
        "event",
        [
            EVENT_PROMO_SHOWN,
            EVENT_PROMO_OPTED_OUT,
            EVENT_CLOUD_LOGIN_STARTED,
            EVENT_CLOUD_LOGIN_SUCCESS,
            EVENT_CLOUD_LOGIN_SUB_REQUIRED,
        ],
    )
    def test_event_names_are_kebab_case(self, event):
        assert isinstance(event, str)
        assert event == event.lower()
        assert " " not in event
        assert event.startswith("cli-")
