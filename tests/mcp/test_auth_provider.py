"""Tests for OAuth authentication provider."""

import pytest
from datetime import datetime, timedelta
from mcp.server.auth.provider import AuthorizationParams
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyHttpUrl

from basic_memory.mcp.auth_provider import BasicMemoryOAuthProvider


class TestBasicMemoryOAuthProvider:
    """Test the BasicMemoryOAuthProvider."""

    @pytest.fixture
    def provider(self):
        """Create a test OAuth provider."""
        return BasicMemoryOAuthProvider(issuer_url="http://localhost:8000")

    @pytest.fixture
    async def client(self, provider):
        """Create and register a test client."""
        client_info = OAuthClientInformationFull(
            client_id="test-client",
            client_secret="test-secret",
        )
        await provider.register_client(client_info)
        return client_info

    async def test_register_client(self, provider):
        """Test client registration."""
        # Register without ID/secret (auto-generated)
        client_info = OAuthClientInformationFull()
        await provider.register_client(client_info)

        assert client_info.client_id is not None
        assert client_info.client_secret is not None

        # Verify client is stored
        stored_client = await provider.get_client(client_info.client_id)
        assert stored_client is not None
        assert stored_client.client_id == client_info.client_id

    async def test_authorization_flow(self, provider, client):
        """Test the complete authorization flow."""
        # Create authorization request
        auth_params = AuthorizationParams(
            state="test-state",
            scopes=["read", "write"],
            code_challenge="test-challenge",
            redirect_uri=AnyHttpUrl("http://localhost:3000/callback"),
            redirect_uri_provided_explicitly=True,
        )

        # Get authorization URL
        auth_url = await provider.authorize(client, auth_params)

        # Verify URL format
        assert "code=" in auth_url
        assert "state=test-state" in auth_url
        assert auth_url.startswith("http://localhost:3000/callback")

        # Extract auth code
        from urllib.parse import urlparse, parse_qs

        parsed = urlparse(auth_url)
        params = parse_qs(parsed.query)
        auth_code = params.get("code", [None])[0]

        assert auth_code is not None

        # Load authorization code
        code_obj = await provider.load_authorization_code(client, auth_code)
        assert code_obj is not None
        assert code_obj.client_id == client.client_id
        assert code_obj.scopes == ["read", "write"]

        # Exchange for tokens
        token = await provider.exchange_authorization_code(client, code_obj)

        assert token.access_token is not None
        assert token.refresh_token is not None
        assert token.expires_in == 3600
        assert token.scope == "read write"

        # Verify authorization code is removed
        code_obj2 = await provider.load_authorization_code(client, auth_code)
        assert code_obj2 is None

    async def test_access_token_validation(self, provider, client):
        """Test access token validation."""
        # Get a valid token through the flow
        auth_params = AuthorizationParams(
            state="test",
            scopes=["read"],
            code_challenge="challenge",
            redirect_uri=AnyHttpUrl("http://localhost:3000/callback"),
            redirect_uri_provided_explicitly=True,
        )

        auth_url = await provider.authorize(client, auth_params)
        auth_code = auth_url.split("code=")[1].split("&")[0]
        code_obj = await provider.load_authorization_code(client, auth_code)
        token = await provider.exchange_authorization_code(client, code_obj)

        # Validate access token
        access_token_obj = await provider.load_access_token(token.access_token)
        assert access_token_obj is not None
        assert access_token_obj.client_id == client.client_id
        assert access_token_obj.scopes == ["read"]

        # Test invalid token
        invalid_token = await provider.load_access_token("invalid-token")
        assert invalid_token is None

    async def test_refresh_token_flow(self, provider, client):
        """Test refresh token exchange."""
        # Get initial tokens
        auth_params = AuthorizationParams(
            state="test",
            scopes=["read", "write"],
            code_challenge="challenge",
            redirect_uri=AnyHttpUrl("http://localhost:3000/callback"),
            redirect_uri_provided_explicitly=True,
        )

        auth_url = await provider.authorize(client, auth_params)
        auth_code = auth_url.split("code=")[1].split("&")[0]
        code_obj = await provider.load_authorization_code(client, auth_code)
        initial_token = await provider.exchange_authorization_code(client, code_obj)

        # Load refresh token
        refresh_token_obj = await provider.load_refresh_token(client, initial_token.refresh_token)
        assert refresh_token_obj is not None

        # Exchange for new tokens
        new_token = await provider.exchange_refresh_token(
            client,
            refresh_token_obj,
            ["read"],  # Request fewer scopes
        )

        assert new_token.access_token != initial_token.access_token
        assert new_token.refresh_token != initial_token.refresh_token
        assert new_token.scope == "read"

        # Old refresh token should be invalid
        old_refresh = await provider.load_refresh_token(client, initial_token.refresh_token)
        assert old_refresh is None

    async def test_token_revocation(self, provider, client):
        """Test token revocation."""
        # Get tokens
        auth_params = AuthorizationParams(
            state="test",
            scopes=["read"],
            code_challenge="challenge",
            redirect_uri=AnyHttpUrl("http://localhost:3000/callback"),
            redirect_uri_provided_explicitly=True,
        )

        auth_url = await provider.authorize(client, auth_params)
        auth_code = auth_url.split("code=")[1].split("&")[0]
        code_obj = await provider.load_authorization_code(client, auth_code)
        token = await provider.exchange_authorization_code(client, code_obj)

        # Verify token is valid
        access_token_obj = await provider.load_access_token(token.access_token)
        assert access_token_obj is not None

        # Revoke token
        await provider.revoke_token(access_token_obj)

        # Verify token is invalid
        revoked_token = await provider.load_access_token(token.access_token)
        assert revoked_token is None

    async def test_expired_authorization_code(self, provider, client):
        """Test expired authorization code handling."""
        # Create auth code with past expiration
        auth_code = "expired-code"
        provider.authorization_codes[auth_code] = provider.BasicMemoryAuthorizationCode(
            code=auth_code,
            scopes=["read"],
            expires_at=(datetime.utcnow() - timedelta(minutes=1)).timestamp(),
            client_id=client.client_id,
            code_challenge="challenge",
            redirect_uri=AnyHttpUrl("http://localhost:3000/callback"),
            redirect_uri_provided_explicitly=True,
        )

        # Try to load expired code
        code_obj = await provider.load_authorization_code(client, auth_code)
        assert code_obj is None

        # Verify code was cleaned up
        assert auth_code not in provider.authorization_codes

    async def test_jwt_access_token(self, provider, client):
        """Test JWT access token generation and validation."""
        # Generate access token directly
        token = provider._generate_access_token(client.client_id, ["read", "write"])

        # Decode and validate
        import jwt

        payload = jwt.decode(token, provider.secret_key, algorithms=["HS256"])

        assert payload["sub"] == client.client_id
        assert payload["scopes"] == ["read", "write"]
        assert payload["aud"] == "basic-memory"
        assert payload["iss"] == provider.issuer_url

    async def test_invalid_client(self, provider):
        """Test operations with invalid client."""
        # Try to get non-existent client
        client = await provider.get_client("invalid-client")
        assert client is None

        # Try to load auth code for invalid client
        fake_client = OAuthClientInformationFull(
            client_id="fake-client",
            client_secret="fake-secret",
        )

        code = await provider.load_authorization_code(fake_client, "some-code")
        assert code is None
