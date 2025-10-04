"""Tests for cloud authentication and subscription validation."""

from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest
from typer.testing import CliRunner

from basic_memory.cli.app import app
from basic_memory.cli.commands.cloud.api_client import (
    CloudAPIError,
    SubscriptionRequiredError,
    make_api_request,
)


class TestAPIClientErrorHandling:
    """Tests for API client error handling."""

    @pytest.mark.asyncio
    async def test_parse_subscription_required_error(self):
        """Test parsing 403 subscription_required error response."""
        # Mock httpx response with subscription error
        mock_response = Mock(spec=httpx.Response)
        mock_response.status_code = 403
        mock_response.json.return_value = {
            "detail": {
                "error": "subscription_required",
                "message": "Active subscription required for CLI access",
                "subscribe_url": "https://basicmemory.com/subscribe",
            }
        }
        mock_response.headers = {}

        # Create HTTPStatusError with the mock response
        http_error = httpx.HTTPStatusError("403 Forbidden", request=Mock(), response=mock_response)

        # Mock httpx client to raise the error
        with patch("basic_memory.cli.commands.cloud.api_client.httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.request = AsyncMock(side_effect=http_error)
            mock_client.return_value.__aenter__.return_value = mock_instance

            # Mock auth to return a token
            with patch(
                "basic_memory.cli.commands.cloud.api_client.get_authenticated_headers",
                return_value={"Authorization": "Bearer test-token"},
            ):
                # Should raise SubscriptionRequiredError
                with pytest.raises(SubscriptionRequiredError) as exc_info:
                    await make_api_request("GET", "https://test.com/api/endpoint")

                # Verify exception details
                error = exc_info.value
                assert error.status_code == 403
                assert error.subscribe_url == "https://basicmemory.com/subscribe"
                assert "Active subscription required" in str(error)

    @pytest.mark.asyncio
    async def test_parse_subscription_required_error_flat_format(self):
        """Test parsing 403 subscription_required error in flat format (backward compatibility)."""
        # Mock httpx response with subscription error in flat format
        mock_response = Mock(spec=httpx.Response)
        mock_response.status_code = 403
        mock_response.json.return_value = {
            "error": "subscription_required",
            "message": "Active subscription required",
            "subscribe_url": "https://basicmemory.com/subscribe",
        }
        mock_response.headers = {}

        # Create HTTPStatusError with the mock response
        http_error = httpx.HTTPStatusError("403 Forbidden", request=Mock(), response=mock_response)

        # Mock httpx client to raise the error
        with patch("basic_memory.cli.commands.cloud.api_client.httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.request = AsyncMock(side_effect=http_error)
            mock_client.return_value.__aenter__.return_value = mock_instance

            # Mock auth to return a token
            with patch(
                "basic_memory.cli.commands.cloud.api_client.get_authenticated_headers",
                return_value={"Authorization": "Bearer test-token"},
            ):
                # Should raise SubscriptionRequiredError
                with pytest.raises(SubscriptionRequiredError) as exc_info:
                    await make_api_request("GET", "https://test.com/api/endpoint")

                # Verify exception details
                error = exc_info.value
                assert error.status_code == 403
                assert error.subscribe_url == "https://basicmemory.com/subscribe"

    @pytest.mark.asyncio
    async def test_parse_generic_403_error(self):
        """Test parsing 403 error without subscription_required flag."""
        # Mock httpx response with generic 403 error
        mock_response = Mock(spec=httpx.Response)
        mock_response.status_code = 403
        mock_response.json.return_value = {
            "error": "forbidden",
            "message": "Access denied",
        }
        mock_response.headers = {}

        # Create HTTPStatusError with the mock response
        http_error = httpx.HTTPStatusError("403 Forbidden", request=Mock(), response=mock_response)

        # Mock httpx client to raise the error
        with patch("basic_memory.cli.commands.cloud.api_client.httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.request = AsyncMock(side_effect=http_error)
            mock_client.return_value.__aenter__.return_value = mock_instance

            # Mock auth to return a token
            with patch(
                "basic_memory.cli.commands.cloud.api_client.get_authenticated_headers",
                return_value={"Authorization": "Bearer test-token"},
            ):
                # Should raise generic CloudAPIError
                with pytest.raises(CloudAPIError) as exc_info:
                    await make_api_request("GET", "https://test.com/api/endpoint")

                # Should not be a SubscriptionRequiredError
                error = exc_info.value
                assert not isinstance(error, SubscriptionRequiredError)
                assert error.status_code == 403


class TestLoginCommand:
    """Tests for cloud login command with subscription validation."""

    def test_login_without_subscription_shows_error(self):
        """Test login command displays error when subscription is required."""
        runner = CliRunner()

        # Mock successful OAuth login
        mock_auth = AsyncMock()
        mock_auth.login = AsyncMock(return_value=True)

        # Mock API request to raise SubscriptionRequiredError
        async def mock_make_api_request(*args, **kwargs):
            raise SubscriptionRequiredError(
                message="Active subscription required for CLI access",
                subscribe_url="https://basicmemory.com/subscribe",
            )

        with patch("basic_memory.cli.commands.cloud.core_commands.CLIAuth", return_value=mock_auth):
            with patch(
                "basic_memory.cli.commands.cloud.core_commands.make_api_request",
                side_effect=mock_make_api_request,
            ):
                with patch(
                    "basic_memory.cli.commands.cloud.core_commands.get_cloud_config",
                    return_value=("client_id", "domain", "https://cloud.example.com"),
                ):
                    # Run login command
                    result = runner.invoke(app, ["cloud", "login"])

                    # Should exit with error
                    assert result.exit_code == 1

                    # Should display subscription error
                    assert "Subscription Required" in result.stdout
                    assert "Active subscription required" in result.stdout
                    assert "https://basicmemory.com/subscribe" in result.stdout
                    assert "bm cloud login" in result.stdout

    def test_login_with_subscription_succeeds(self):
        """Test login command succeeds when user has active subscription."""
        runner = CliRunner()

        # Mock successful OAuth login
        mock_auth = AsyncMock()
        mock_auth.login = AsyncMock(return_value=True)

        # Mock successful API request (subscription valid)
        mock_response = Mock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "healthy"}

        async def mock_make_api_request(*args, **kwargs):
            return mock_response

        with patch("basic_memory.cli.commands.cloud.core_commands.CLIAuth", return_value=mock_auth):
            with patch(
                "basic_memory.cli.commands.cloud.core_commands.make_api_request",
                side_effect=mock_make_api_request,
            ):
                with patch(
                    "basic_memory.cli.commands.cloud.core_commands.get_cloud_config",
                    return_value=("client_id", "domain", "https://cloud.example.com"),
                ):
                    # Mock ConfigManager to avoid writing to real config
                    mock_config_manager = Mock()
                    mock_config = Mock()
                    mock_config.cloud_mode = False
                    mock_config_manager.load_config.return_value = mock_config
                    mock_config_manager.config = mock_config

                    with patch(
                        "basic_memory.cli.commands.cloud.core_commands.ConfigManager",
                        return_value=mock_config_manager,
                    ):
                        # Run login command
                        result = runner.invoke(app, ["cloud", "login"])

                        # Should succeed
                        assert result.exit_code == 0

                        # Should enable cloud mode
                        assert mock_config.cloud_mode is True
                        mock_config_manager.save_config.assert_called_once()

                        # Should display success message
                        assert "Cloud mode enabled" in result.stdout

    def test_login_authentication_failure(self):
        """Test login command handles authentication failure."""
        runner = CliRunner()

        # Mock failed OAuth login
        mock_auth = AsyncMock()
        mock_auth.login = AsyncMock(return_value=False)

        with patch("basic_memory.cli.commands.cloud.core_commands.CLIAuth", return_value=mock_auth):
            with patch(
                "basic_memory.cli.commands.cloud.core_commands.get_cloud_config",
                return_value=("client_id", "domain", "https://cloud.example.com"),
            ):
                # Run login command
                result = runner.invoke(app, ["cloud", "login"])

                # Should exit with error
                assert result.exit_code == 1

                # Should display login failed message
                assert "Login failed" in result.stdout
