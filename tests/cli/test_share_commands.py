"""Tests for cloud share CLI commands.

Issue #880: Tests for share create, list, update, revoke commands that surface
the cloud /api/shares endpoints.
"""

from unittest.mock import Mock, patch

import httpx
from typer.testing import CliRunner

from basic_memory.cli.app import app
from basic_memory.cli.commands.cloud.api_client import (
    CloudAPIError,
    SubscriptionRequiredError,
)

SHARE_RESPONSE = {
    "id": "11111111-1111-1111-1111-111111111111",
    "token": "abc123",
    "project_name": "my-project",
    "note_permalink": "notes/my-idea",
    "note_external_id": "ext-1",
    "enabled": True,
    "expires_at": None,
    "share_url": "https://share.example.com/abc123",
    "view_count": 0,
    "last_viewed_at": None,
    "created_at": "2025-01-18T12:00:00Z",
}


def _mock_config_manager():
    mock_config = Mock()
    mock_config.cloud_host = "https://cloud.example.com"
    mock_config_manager = Mock()
    mock_config_manager.config = mock_config
    return mock_config_manager


class TestShareCreateCommand:
    """Tests for 'bm cloud share create' command."""

    def test_create_share_success(self):
        runner = CliRunner()

        mock_response = Mock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = SHARE_RESPONSE

        captured = {}

        async def mock_make_api_request(*args, **kwargs):
            captured["json_data"] = kwargs.get("json_data")
            return mock_response

        with patch(
            "basic_memory.cli.commands.cloud.shares.make_api_request",
            side_effect=mock_make_api_request,
        ):
            with patch(
                "basic_memory.cli.commands.cloud.shares.ConfigManager",
                return_value=_mock_config_manager(),
            ):
                result = runner.invoke(
                    app, ["cloud", "share", "create", "my-project", "notes/my-idea"]
                )

                assert result.exit_code == 0
                assert "Share link created successfully" in result.stdout
                assert "abc123" in result.stdout
                assert "https://share.example.com/abc123" in result.stdout
                # Payload should match the cloud CreateShareRequest contract.
                assert captured["json_data"] == {
                    "project_name": "my-project",
                    "note_permalink": "notes/my-idea",
                }

    def test_create_share_with_expires_at(self):
        runner = CliRunner()

        mock_response = Mock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = SHARE_RESPONSE

        captured = {}

        async def mock_make_api_request(*args, **kwargs):
            captured["json_data"] = kwargs.get("json_data")
            return mock_response

        with patch(
            "basic_memory.cli.commands.cloud.shares.make_api_request",
            side_effect=mock_make_api_request,
        ):
            with patch(
                "basic_memory.cli.commands.cloud.shares.ConfigManager",
                return_value=_mock_config_manager(),
            ):
                result = runner.invoke(
                    app,
                    [
                        "cloud",
                        "share",
                        "create",
                        "my-project",
                        "notes/my-idea",
                        "--expires-at",
                        "2025-12-31",
                    ],
                )

                assert result.exit_code == 0
                assert captured["json_data"]["expires_at"].startswith("2025-12-31")

    def test_create_share_invalid_expires_at(self):
        runner = CliRunner()

        async def mock_make_api_request(*args, **kwargs):  # pragma: no cover
            raise AssertionError("API should not be called on invalid input")

        with patch(
            "basic_memory.cli.commands.cloud.shares.make_api_request",
            side_effect=mock_make_api_request,
        ):
            with patch(
                "basic_memory.cli.commands.cloud.shares.ConfigManager",
                return_value=_mock_config_manager(),
            ):
                result = runner.invoke(
                    app,
                    [
                        "cloud",
                        "share",
                        "create",
                        "my-project",
                        "notes/my-idea",
                        "--expires-at",
                        "not-a-date",
                    ],
                )

                assert result.exit_code == 1
                assert "Invalid --expires-at" in result.stdout
                # A parse error must produce a single clean message, not a
                # spurious "Unexpected error: 1" from the broad handler
                # re-catching typer.Exit. See issue #880 review.
                assert "Unexpected error" not in result.stdout

    def test_create_share_note_not_found(self):
        runner = CliRunner()

        async def mock_make_api_request(*args, **kwargs):
            raise CloudAPIError("Not found", status_code=404)

        with patch(
            "basic_memory.cli.commands.cloud.shares.make_api_request",
            side_effect=mock_make_api_request,
        ):
            with patch(
                "basic_memory.cli.commands.cloud.shares.ConfigManager",
                return_value=_mock_config_manager(),
            ):
                result = runner.invoke(
                    app, ["cloud", "share", "create", "my-project", "notes/missing"]
                )

                assert result.exit_code == 1
                assert "Note not found" in result.stdout

    def test_create_share_subscription_required(self):
        runner = CliRunner()

        async def mock_make_api_request(*args, **kwargs):
            raise SubscriptionRequiredError(
                message="Active subscription required",
                subscribe_url="https://basicmemory.com/subscribe",
            )

        with patch(
            "basic_memory.cli.commands.cloud.shares.make_api_request",
            side_effect=mock_make_api_request,
        ):
            with patch(
                "basic_memory.cli.commands.cloud.shares.ConfigManager",
                return_value=_mock_config_manager(),
            ):
                result = runner.invoke(
                    app, ["cloud", "share", "create", "my-project", "notes/my-idea"]
                )

                assert result.exit_code == 1
                assert "Subscription Required" in result.stdout

    def test_create_share_api_error(self):
        runner = CliRunner()

        async def mock_make_api_request(*args, **kwargs):
            raise CloudAPIError("Server error", status_code=500)

        with patch(
            "basic_memory.cli.commands.cloud.shares.make_api_request",
            side_effect=mock_make_api_request,
        ):
            with patch(
                "basic_memory.cli.commands.cloud.shares.ConfigManager",
                return_value=_mock_config_manager(),
            ):
                result = runner.invoke(
                    app, ["cloud", "share", "create", "my-project", "notes/my-idea"]
                )

                assert result.exit_code == 1
                assert "Failed to create share link" in result.stdout


class TestShareListCommand:
    """Tests for 'bm cloud share list' command."""

    def test_list_shares_success(self):
        # Wide terminal so the rich table doesn't truncate cell contents.
        runner = CliRunner(env={"COLUMNS": "200"})

        mock_response = Mock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "shares": [
                SHARE_RESPONSE,
                {
                    **SHARE_RESPONSE,
                    "token": "def456",
                    "note_permalink": "notes/second",
                    "enabled": False,
                    "expires_at": "2025-12-31T00:00:00Z",
                    "view_count": 7,
                },
            ],
            "total": 2,
        }

        async def mock_make_api_request(*args, **kwargs):
            return mock_response

        with patch(
            "basic_memory.cli.commands.cloud.shares.make_api_request",
            side_effect=mock_make_api_request,
        ):
            with patch(
                "basic_memory.cli.commands.cloud.shares.ConfigManager",
                return_value=_mock_config_manager(),
            ):
                result = runner.invoke(app, ["cloud", "share", "list"])

                assert result.exit_code == 0
                assert "abc123" in result.stdout
                assert "def456" in result.stdout
                assert "notes/second" in result.stdout

    def test_list_shares_empty(self):
        runner = CliRunner()

        mock_response = Mock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {"shares": [], "total": 0}

        async def mock_make_api_request(*args, **kwargs):
            return mock_response

        with patch(
            "basic_memory.cli.commands.cloud.shares.make_api_request",
            side_effect=mock_make_api_request,
        ):
            with patch(
                "basic_memory.cli.commands.cloud.shares.ConfigManager",
                return_value=_mock_config_manager(),
            ):
                result = runner.invoke(app, ["cloud", "share", "list"])

                assert result.exit_code == 0
                assert "No share links found" in result.stdout

    def test_list_shares_with_project_filter(self):
        runner = CliRunner()

        mock_response = Mock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {"shares": [SHARE_RESPONSE], "total": 1}

        captured = {}

        async def mock_make_api_request(*args, **kwargs):
            captured["url"] = kwargs.get("url", args[1] if len(args) > 1 else "")
            return mock_response

        with patch(
            "basic_memory.cli.commands.cloud.shares.make_api_request",
            side_effect=mock_make_api_request,
        ):
            with patch(
                "basic_memory.cli.commands.cloud.shares.ConfigManager",
                return_value=_mock_config_manager(),
            ):
                result = runner.invoke(app, ["cloud", "share", "list", "--project", "my-project"])

                assert result.exit_code == 0
                assert "project_name=my-project" in captured["url"]

    def test_list_shares_api_error(self):
        runner = CliRunner()

        async def mock_make_api_request(*args, **kwargs):
            raise CloudAPIError("Server error", status_code=500)

        with patch(
            "basic_memory.cli.commands.cloud.shares.make_api_request",
            side_effect=mock_make_api_request,
        ):
            with patch(
                "basic_memory.cli.commands.cloud.shares.ConfigManager",
                return_value=_mock_config_manager(),
            ):
                result = runner.invoke(app, ["cloud", "share", "list"])

                assert result.exit_code == 1
                assert "Failed to list share links" in result.stdout


class TestShareUpdateCommand:
    """Tests for 'bm cloud share update' command."""

    def test_update_disable(self):
        runner = CliRunner()

        mock_response = Mock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {**SHARE_RESPONSE, "enabled": False}

        captured = {}

        async def mock_make_api_request(*args, **kwargs):
            captured["json_data"] = kwargs.get("json_data")
            captured["method"] = kwargs.get("method")
            return mock_response

        with patch(
            "basic_memory.cli.commands.cloud.shares.make_api_request",
            side_effect=mock_make_api_request,
        ):
            with patch(
                "basic_memory.cli.commands.cloud.shares.ConfigManager",
                return_value=_mock_config_manager(),
            ):
                result = runner.invoke(app, ["cloud", "share", "update", "abc123", "--disable"])

                assert result.exit_code == 0
                assert "updated successfully" in result.stdout
                assert captured["method"] == "PATCH"
                assert captured["json_data"] == {"enabled": False}

    def test_update_enable(self):
        runner = CliRunner()

        mock_response = Mock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = SHARE_RESPONSE

        captured = {}

        async def mock_make_api_request(*args, **kwargs):
            captured["json_data"] = kwargs.get("json_data")
            return mock_response

        with patch(
            "basic_memory.cli.commands.cloud.shares.make_api_request",
            side_effect=mock_make_api_request,
        ):
            with patch(
                "basic_memory.cli.commands.cloud.shares.ConfigManager",
                return_value=_mock_config_manager(),
            ):
                result = runner.invoke(app, ["cloud", "share", "update", "abc123", "--enable"])

                assert result.exit_code == 0
                assert captured["json_data"] == {"enabled": True}

    def test_update_expires_at(self):
        runner = CliRunner()

        mock_response = Mock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = SHARE_RESPONSE

        captured = {}

        async def mock_make_api_request(*args, **kwargs):
            captured["json_data"] = kwargs.get("json_data")
            return mock_response

        with patch(
            "basic_memory.cli.commands.cloud.shares.make_api_request",
            side_effect=mock_make_api_request,
        ):
            with patch(
                "basic_memory.cli.commands.cloud.shares.ConfigManager",
                return_value=_mock_config_manager(),
            ):
                result = runner.invoke(
                    app,
                    ["cloud", "share", "update", "abc123", "--expires-at", "2026-01-01"],
                )

                assert result.exit_code == 0
                assert captured["json_data"]["expires_at"].startswith("2026-01-01")

    def test_update_clear_expires_at(self):
        runner = CliRunner()

        mock_response = Mock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = SHARE_RESPONSE

        captured = {}

        async def mock_make_api_request(*args, **kwargs):
            captured["json_data"] = kwargs.get("json_data")
            return mock_response

        with patch(
            "basic_memory.cli.commands.cloud.shares.make_api_request",
            side_effect=mock_make_api_request,
        ):
            with patch(
                "basic_memory.cli.commands.cloud.shares.ConfigManager",
                return_value=_mock_config_manager(),
            ):
                result = runner.invoke(
                    app, ["cloud", "share", "update", "abc123", "--expires-at", "none"]
                )

                assert result.exit_code == 0
                assert captured["json_data"] == {"expires_at": None}

    def test_update_enable_and_disable_conflict(self):
        runner = CliRunner()

        async def mock_make_api_request(*args, **kwargs):  # pragma: no cover
            raise AssertionError("API should not be called on conflicting flags")

        with patch(
            "basic_memory.cli.commands.cloud.shares.make_api_request",
            side_effect=mock_make_api_request,
        ):
            with patch(
                "basic_memory.cli.commands.cloud.shares.ConfigManager",
                return_value=_mock_config_manager(),
            ):
                result = runner.invoke(
                    app,
                    ["cloud", "share", "update", "abc123", "--enable", "--disable"],
                )

                assert result.exit_code == 1
                assert "Cannot use --enable and --disable together" in result.stdout

    def test_update_nothing_to_change(self):
        runner = CliRunner()

        async def mock_make_api_request(*args, **kwargs):  # pragma: no cover
            raise AssertionError("API should not be called with empty update")

        with patch(
            "basic_memory.cli.commands.cloud.shares.make_api_request",
            side_effect=mock_make_api_request,
        ):
            with patch(
                "basic_memory.cli.commands.cloud.shares.ConfigManager",
                return_value=_mock_config_manager(),
            ):
                result = runner.invoke(app, ["cloud", "share", "update", "abc123"])

                assert result.exit_code == 1
                assert "Nothing to update" in result.stdout

    def test_update_not_found(self):
        runner = CliRunner()

        async def mock_make_api_request(*args, **kwargs):
            raise CloudAPIError("Not found", status_code=404)

        with patch(
            "basic_memory.cli.commands.cloud.shares.make_api_request",
            side_effect=mock_make_api_request,
        ):
            with patch(
                "basic_memory.cli.commands.cloud.shares.ConfigManager",
                return_value=_mock_config_manager(),
            ):
                result = runner.invoke(app, ["cloud", "share", "update", "missing", "--disable"])

                assert result.exit_code == 1
                assert "Share not found" in result.stdout


class TestShareRevokeCommand:
    """Tests for 'bm cloud share revoke' command."""

    def test_revoke_success_with_force(self):
        runner = CliRunner()

        mock_response = Mock(spec=httpx.Response)
        mock_response.status_code = 204
        mock_response.json.return_value = {}

        captured = {}

        async def mock_make_api_request(*args, **kwargs):
            captured["method"] = kwargs.get("method")
            captured["url"] = kwargs.get("url")
            return mock_response

        with patch(
            "basic_memory.cli.commands.cloud.shares.make_api_request",
            side_effect=mock_make_api_request,
        ):
            with patch(
                "basic_memory.cli.commands.cloud.shares.ConfigManager",
                return_value=_mock_config_manager(),
            ):
                result = runner.invoke(app, ["cloud", "share", "revoke", "abc123", "--force"])

                assert result.exit_code == 0
                assert "revoked successfully" in result.stdout
                assert captured["method"] == "DELETE"
                assert captured["url"].endswith("/api/shares/abc123")

    def test_revoke_cancelled(self):
        runner = CliRunner()

        call_count = 0

        async def mock_make_api_request(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return Mock(spec=httpx.Response)

        with patch(
            "basic_memory.cli.commands.cloud.shares.make_api_request",
            side_effect=mock_make_api_request,
        ):
            with patch(
                "basic_memory.cli.commands.cloud.shares.ConfigManager",
                return_value=_mock_config_manager(),
            ):
                result = runner.invoke(app, ["cloud", "share", "revoke", "abc123"], input="n\n")

                assert result.exit_code == 0
                assert "cancelled" in result.stdout
                assert call_count == 0

    def test_revoke_not_found(self):
        runner = CliRunner()

        async def mock_make_api_request(*args, **kwargs):
            raise CloudAPIError("Not found", status_code=404)

        with patch(
            "basic_memory.cli.commands.cloud.shares.make_api_request",
            side_effect=mock_make_api_request,
        ):
            with patch(
                "basic_memory.cli.commands.cloud.shares.ConfigManager",
                return_value=_mock_config_manager(),
            ):
                result = runner.invoke(app, ["cloud", "share", "revoke", "missing", "--force"])

                assert result.exit_code == 1
                assert "Share not found" in result.stdout

    def test_revoke_subscription_required(self):
        runner = CliRunner()

        async def mock_make_api_request(*args, **kwargs):
            raise SubscriptionRequiredError(
                message="Active subscription required",
                subscribe_url="https://basicmemory.com/subscribe",
            )

        with patch(
            "basic_memory.cli.commands.cloud.shares.make_api_request",
            side_effect=mock_make_api_request,
        ):
            with patch(
                "basic_memory.cli.commands.cloud.shares.ConfigManager",
                return_value=_mock_config_manager(),
            ):
                result = runner.invoke(app, ["cloud", "share", "revoke", "abc123", "--force"])

                assert result.exit_code == 1
                assert "Subscription Required" in result.stdout
