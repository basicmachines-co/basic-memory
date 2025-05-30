"""Tests for CLI auth commands."""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from typer.testing import CliRunner
from pydantic import AnyHttpUrl

from basic_memory.cli.commands.auth import auth_app
from mcp.shared.auth import OAuthClientInformationFull


class TestAuthCommands:
    """Test CLI auth commands."""

    @pytest.fixture
    def runner(self):
        """Create a CLI test runner."""
        return CliRunner()

    @pytest.fixture
    def mock_provider(self):
        """Create a mock OAuth provider."""
        provider = MagicMock()
        provider.register_client = AsyncMock()
        provider.get_client = AsyncMock()
        provider.authorize = AsyncMock()
        provider.load_authorization_code = AsyncMock()
        provider.exchange_authorization_code = AsyncMock()
        provider.load_access_token = AsyncMock()
        return provider

    def test_register_client_default_values(self, runner, mock_provider):
        """Test client registration with default values."""
        with patch(
            "basic_memory.cli.commands.auth.BasicMemoryOAuthProvider"
        ) as mock_provider_class:
            mock_provider_class.return_value = mock_provider

            # Mock the client info to capture what gets passed to register_client
            captured_client_info = None
            original_client_id = None
            original_client_secret = None

            async def capture_register_client(client_info):
                nonlocal captured_client_info, original_client_id, original_client_secret
                captured_client_info = client_info
                # Capture original values before modification
                original_client_id = client_info.client_id
                original_client_secret = client_info.client_secret
                # Simulate auto-generation of IDs
                client_info.client_id = "auto-generated-id"
                client_info.client_secret = "auto-generated-secret"

            mock_provider.register_client.side_effect = capture_register_client

            result = runner.invoke(auth_app, ["register-client"])

            assert result.exit_code == 0
            assert "Client registered successfully!" in result.stdout
            assert "Client ID: auto-generated-id" in result.stdout
            assert "Client Secret: auto-generated-secret" in result.stdout
            assert "Save these credentials securely" in result.stdout

            # Verify provider was created with default issuer URL
            mock_provider_class.assert_called_once_with(issuer_url="http://localhost:8000")

            # Verify register_client was called
            mock_provider.register_client.assert_called_once()

            # Verify the client info had correct defaults (using captured original values)
            assert captured_client_info is not None
            assert original_client_id == ""  # Empty string for auto-generation
            assert original_client_secret == ""  # Empty string for auto-generation
            assert captured_client_info.redirect_uris == [
                AnyHttpUrl("http://localhost:8000/callback")
            ]
            assert captured_client_info.client_name == "Basic Memory OAuth Client"
            assert captured_client_info.grant_types == ["authorization_code", "refresh_token"]

    def test_register_client_custom_values(self, runner, mock_provider):
        """Test client registration with custom values."""
        with patch(
            "basic_memory.cli.commands.auth.BasicMemoryOAuthProvider"
        ) as mock_provider_class:
            mock_provider_class.return_value = mock_provider

            captured_client_info = None

            async def capture_register_client(client_info):
                nonlocal captured_client_info
                captured_client_info = client_info
                # Don't modify the provided IDs

            mock_provider.register_client.side_effect = capture_register_client

            result = runner.invoke(
                auth_app,
                [
                    "register-client",
                    "--client-id",
                    "custom-client-id",
                    "--client-secret",
                    "custom-client-secret",
                    "--issuer-url",
                    "https://custom.example.com",
                ],
            )

            assert result.exit_code == 0
            assert "Client registered successfully!" in result.stdout
            assert "Client ID: custom-client-id" in result.stdout
            assert "Client Secret: custom-client-secret" in result.stdout

            # Verify provider was created with custom issuer URL
            mock_provider_class.assert_called_once_with(issuer_url="https://custom.example.com")

            # Verify the client info had custom values
            assert captured_client_info is not None
            assert captured_client_info.client_id == "custom-client-id"
            assert captured_client_info.client_secret == "custom-client-secret"

    def test_register_client_exception_handling(self, runner, mock_provider):
        """Test client registration error handling."""
        with patch(
            "basic_memory.cli.commands.auth.BasicMemoryOAuthProvider"
        ) as mock_provider_class:
            mock_provider_class.return_value = mock_provider
            mock_provider.register_client.side_effect = Exception("Registration failed")

            result = runner.invoke(auth_app, ["register-client"])

            # Should fail with exception
            assert result.exit_code != 0

    def test_test_auth_success_flow(self, runner, mock_provider):
        """Test successful OAuth test flow."""
        with patch(
            "basic_memory.cli.commands.auth.BasicMemoryOAuthProvider"
        ) as mock_provider_class:
            mock_provider_class.return_value = mock_provider

            # Mock successful flow
            test_client = OAuthClientInformationFull(
                client_id="test-client-id",
                client_secret="test-secret",
                redirect_uris=[AnyHttpUrl("http://localhost:8000/callback")],
                client_name="Test OAuth Client",
                grant_types=["authorization_code", "refresh_token"],
            )

            async def register_client_side_effect(client_info):
                # Simulate setting the client_id after registration
                client_info.client_id = "test-client-id"
                client_info.client_secret = "test-secret"

            mock_provider.register_client.side_effect = register_client_side_effect
            mock_provider.get_client.return_value = test_client
            mock_provider.authorize.return_value = (
                "http://localhost:8000/callback?code=test-auth-code&state=test-state"
            )

            # Mock authorization code object
            mock_auth_code = MagicMock()
            mock_provider.load_authorization_code.return_value = mock_auth_code

            # Mock token response
            mock_token = MagicMock()
            mock_token.access_token = "test-access-token"
            mock_token.refresh_token = "test-refresh-token"
            mock_token.expires_in = 3600
            mock_provider.exchange_authorization_code.return_value = mock_token

            # Mock access token validation
            mock_access_token_obj = MagicMock()
            mock_access_token_obj.client_id = "test-client-id"
            mock_access_token_obj.scopes = ["read", "write"]
            mock_provider.load_access_token.return_value = mock_access_token_obj

            result = runner.invoke(auth_app, ["test-auth"])

            assert result.exit_code == 0
            assert "Registered test client:" in result.stdout
            assert "Authorization URL:" in result.stdout
            assert "Access token: test-access-token" in result.stdout
            assert "Refresh token: test-refresh-token" in result.stdout
            assert "Expires in: 3600 seconds" in result.stdout
            assert "Access token validated successfully!" in result.stdout
            assert "Client ID: test-client-id" in result.stdout
            assert "Scopes: ['read', 'write']" in result.stdout

            # Verify all the expected calls were made
            mock_provider.register_client.assert_called_once()
            mock_provider.get_client.assert_called_once()
            mock_provider.authorize.assert_called_once()
            mock_provider.load_authorization_code.assert_called_once()
            mock_provider.exchange_authorization_code.assert_called_once()
            mock_provider.load_access_token.assert_called_once()

    def test_test_auth_custom_issuer_url(self, runner, mock_provider):
        """Test OAuth test flow with custom issuer URL."""
        with patch(
            "basic_memory.cli.commands.auth.BasicMemoryOAuthProvider"
        ) as mock_provider_class:
            mock_provider_class.return_value = mock_provider

            # Setup minimal mocks to avoid errors
            async def register_client_side_effect(client_info):
                client_info.client_id = "test-client-id"

            mock_provider.register_client.side_effect = register_client_side_effect
            mock_provider.get_client.return_value = None  # This will cause early exit

            result = runner.invoke(
                auth_app, ["test-auth", "--issuer-url", "https://custom-issuer.com"]
            )

            # Should create provider with custom URL
            mock_provider_class.assert_called_once_with(issuer_url="https://custom-issuer.com")

            # Should exit early due to client not found
            assert "Error: Client not found after registration" in result.stderr

    def test_test_auth_client_not_found(self, runner, mock_provider):
        """Test OAuth test flow when client is not found after registration."""
        with patch(
            "basic_memory.cli.commands.auth.BasicMemoryOAuthProvider"
        ) as mock_provider_class:
            mock_provider_class.return_value = mock_provider

            async def register_client_side_effect(client_info):
                client_info.client_id = "test-client-id"

            mock_provider.register_client.side_effect = register_client_side_effect
            mock_provider.get_client.return_value = None

            result = runner.invoke(auth_app, ["test-auth"])

            assert result.exit_code == 0  # Command completes but with error message
            assert "Error: Client not found after registration" in result.stderr

    def test_test_auth_no_auth_code_in_url(self, runner, mock_provider):
        """Test OAuth test flow when no auth code in URL."""
        with patch(
            "basic_memory.cli.commands.auth.BasicMemoryOAuthProvider"
        ) as mock_provider_class:
            mock_provider_class.return_value = mock_provider

            test_client = OAuthClientInformationFull(
                client_id="test-client-id",
                client_secret="test-secret",
                redirect_uris=[AnyHttpUrl("http://localhost:8000/callback")],
                client_name="Test OAuth Client",
                grant_types=["authorization_code", "refresh_token"],
            )

            async def register_client_side_effect(client_info):
                client_info.client_id = "test-client-id"

            mock_provider.register_client.side_effect = register_client_side_effect
            mock_provider.get_client.return_value = test_client
            mock_provider.authorize.return_value = (
                "http://localhost:8000/callback?state=test-state"  # No code parameter
            )

            result = runner.invoke(auth_app, ["test-auth"])

            assert result.exit_code == 0
            assert "Error: No authorization code in URL" in result.stderr

    def test_test_auth_invalid_auth_code(self, runner, mock_provider):
        """Test OAuth test flow when authorization code is invalid."""
        with patch(
            "basic_memory.cli.commands.auth.BasicMemoryOAuthProvider"
        ) as mock_provider_class:
            mock_provider_class.return_value = mock_provider

            test_client = OAuthClientInformationFull(
                client_id="test-client-id",
                client_secret="test-secret",
                redirect_uris=[AnyHttpUrl("http://localhost:8000/callback")],
                client_name="Test OAuth Client",
                grant_types=["authorization_code", "refresh_token"],
            )

            async def register_client_side_effect(client_info):
                client_info.client_id = "test-client-id"

            mock_provider.register_client.side_effect = register_client_side_effect
            mock_provider.get_client.return_value = test_client
            mock_provider.authorize.return_value = (
                "http://localhost:8000/callback?code=invalid-code&state=test-state"
            )
            mock_provider.load_authorization_code.return_value = None  # Invalid code

            result = runner.invoke(auth_app, ["test-auth"])

            assert result.exit_code == 0
            assert "Error: Invalid authorization code" in result.stderr

    def test_test_auth_invalid_access_token(self, runner, mock_provider):
        """Test OAuth test flow when access token validation fails."""
        with patch(
            "basic_memory.cli.commands.auth.BasicMemoryOAuthProvider"
        ) as mock_provider_class:
            mock_provider_class.return_value = mock_provider

            test_client = OAuthClientInformationFull(
                client_id="test-client-id",
                client_secret="test-secret",
                redirect_uris=[AnyHttpUrl("http://localhost:8000/callback")],
                client_name="Test OAuth Client",
                grant_types=["authorization_code", "refresh_token"],
            )

            async def register_client_side_effect(client_info):
                client_info.client_id = "test-client-id"

            mock_provider.register_client.side_effect = register_client_side_effect
            mock_provider.get_client.return_value = test_client
            mock_provider.authorize.return_value = (
                "http://localhost:8000/callback?code=test-auth-code&state=test-state"
            )

            mock_auth_code = MagicMock()
            mock_provider.load_authorization_code.return_value = mock_auth_code

            mock_token = MagicMock()
            mock_token.access_token = "test-access-token"
            mock_token.refresh_token = "test-refresh-token"
            mock_token.expires_in = 3600
            mock_provider.exchange_authorization_code.return_value = mock_token

            mock_provider.load_access_token.return_value = None  # Invalid token

            result = runner.invoke(auth_app, ["test-auth"])

            assert result.exit_code == 0
            assert "Access token: test-access-token" in result.stdout
            assert "Error: Invalid access token" in result.stderr

    def test_test_auth_exception_handling(self, runner, mock_provider):
        """Test OAuth test flow exception handling."""
        with patch(
            "basic_memory.cli.commands.auth.BasicMemoryOAuthProvider"
        ) as mock_provider_class:
            mock_provider_class.return_value = mock_provider
            mock_provider.register_client.side_effect = Exception("Test exception")

            result = runner.invoke(auth_app, ["test-auth"])

            # Should fail with exception
            assert result.exit_code != 0
