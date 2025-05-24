"""External OAuth provider integration for Basic Memory MCP server."""

import os
from typing import Optional, Dict, Any
from dataclasses import dataclass

import httpx
from loguru import logger
from mcp.server.auth.provider import (
    OAuthAuthorizationServerProvider,
    AuthorizationParams,
    AuthorizationCode,
    RefreshToken,
    AccessToken,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken


@dataclass
class ExternalAuthorizationCode(AuthorizationCode):
    """Authorization code with external provider metadata."""

    external_code: Optional[str] = None
    state: Optional[str] = None


@dataclass
class ExternalRefreshToken(RefreshToken):
    """Refresh token with external provider metadata."""

    external_token: Optional[str] = None


@dataclass
class ExternalAccessToken(AccessToken):
    """Access token with external provider metadata."""

    external_token: Optional[str] = None


class ExternalOAuthProvider(
    OAuthAuthorizationServerProvider[
        ExternalAuthorizationCode, ExternalRefreshToken, ExternalAccessToken
    ]
):
    """OAuth provider that delegates to external OAuth providers.

    This provider can integrate with services like:
    - GitHub OAuth
    - Google OAuth
    - Auth0
    - Okta
    """

    def __init__(
        self,
        issuer_url: str,
        external_provider: str,
        external_client_id: str,
        external_client_secret: str,
        external_authorize_url: str,
        external_token_url: str,
        external_userinfo_url: Optional[str] = None,
    ):
        self.issuer_url = issuer_url
        self.external_provider = external_provider
        self.external_client_id = external_client_id
        self.external_client_secret = external_client_secret
        self.external_authorize_url = external_authorize_url
        self.external_token_url = external_token_url
        self.external_userinfo_url = external_userinfo_url

        # In-memory storage - in production, use a database
        self.clients: Dict[str, OAuthClientInformationFull] = {}
        self.codes: Dict[str, ExternalAuthorizationCode] = {}
        self.tokens: Dict[str, Any] = {}

        self.http_client = httpx.AsyncClient()

    async def get_client(self, client_id: str) -> Optional[OAuthClientInformationFull]:
        """Get a client by ID."""
        return self.clients.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        """Register a new OAuth client."""
        self.clients[client_info.client_id] = client_info
        logger.info(f"Registered external OAuth client: {client_info.client_id}")

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        """Create authorization URL redirecting to external provider."""
        # Store authorization request
        import secrets

        state = secrets.token_urlsafe(32)

        self.codes[state] = ExternalAuthorizationCode(
            code=state,
            scopes=params.scopes or [],
            expires_at=0,  # Will be set by external provider
            client_id=client.client_id,
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            state=params.state,
        )

        # Build external provider URL
        external_params = {
            "client_id": self.external_client_id,
            "redirect_uri": f"{self.issuer_url}/callback",
            "response_type": "code",
            "state": state,
            "scope": " ".join(params.scopes or []),
        }

        return construct_redirect_uri(self.external_authorize_url, **external_params)

    async def handle_callback(self, code: str, state: str) -> str:
        """Handle callback from external provider."""
        # Get original authorization request
        auth_code = self.codes.get(state)
        if not auth_code:
            raise ValueError("Invalid state parameter")

        # Exchange code with external provider
        token_data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": f"{self.issuer_url}/callback",
            "client_id": self.external_client_id,
            "client_secret": self.external_client_secret,
        }

        response = await self.http_client.post(
            self.external_token_url,
            data=token_data,
        )
        response.raise_for_status()
        external_tokens = response.json()

        # Store external tokens
        import secrets

        internal_code = secrets.token_urlsafe(32)

        self.codes[internal_code] = ExternalAuthorizationCode(
            code=internal_code,
            scopes=auth_code.scopes,
            expires_at=0,
            client_id=auth_code.client_id,
            code_challenge=auth_code.code_challenge,
            redirect_uri=auth_code.redirect_uri,
            redirect_uri_provided_explicitly=auth_code.redirect_uri_provided_explicitly,
            external_code=code,
            state=auth_code.state,
        )

        self.tokens[internal_code] = external_tokens

        # Redirect to original client
        return construct_redirect_uri(
            str(auth_code.redirect_uri),
            code=internal_code,
            state=auth_code.state,
        )

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> Optional[ExternalAuthorizationCode]:
        """Load an authorization code."""
        code = self.codes.get(authorization_code)
        if code and code.client_id == client.client_id:
            return code
        return None

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: ExternalAuthorizationCode
    ) -> OAuthToken:
        """Exchange authorization code for tokens."""
        # Get stored external tokens
        external_tokens = self.tokens.get(authorization_code.code)
        if not external_tokens:
            raise ValueError("No tokens found for authorization code")

        # Map external tokens to MCP tokens
        access_token = external_tokens.get("access_token")
        refresh_token = external_tokens.get("refresh_token")
        expires_in = external_tokens.get("expires_in", 3600)

        # Store the mapping
        self.tokens[access_token] = {
            "client_id": client.client_id,
            "external_token": access_token,
            "scopes": authorization_code.scopes,
        }

        if refresh_token:
            self.tokens[refresh_token] = {
                "client_id": client.client_id,
                "external_token": refresh_token,
                "scopes": authorization_code.scopes,
            }

        # Clean up authorization code
        del self.codes[authorization_code.code]

        return OAuthToken(
            access_token=access_token,
            token_type="bearer",
            expires_in=expires_in,
            refresh_token=refresh_token,
            scope=" ".join(authorization_code.scopes) if authorization_code.scopes else None,
        )

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> Optional[ExternalRefreshToken]:
        """Load a refresh token."""
        token_info = self.tokens.get(refresh_token)
        if token_info and token_info["client_id"] == client.client_id:
            return ExternalRefreshToken(
                token=refresh_token,
                client_id=client.client_id,
                scopes=token_info["scopes"],
                external_token=token_info.get("external_token"),
            )
        return None

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: ExternalRefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        """Exchange refresh token for new tokens."""
        # Exchange with external provider
        token_data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token.external_token or refresh_token.token,
            "client_id": self.external_client_id,
            "client_secret": self.external_client_secret,
        }

        response = await self.http_client.post(
            self.external_token_url,
            data=token_data,
        )
        response.raise_for_status()
        external_tokens = response.json()

        # Update stored tokens
        new_access_token = external_tokens.get("access_token")
        new_refresh_token = external_tokens.get("refresh_token", refresh_token.token)
        expires_in = external_tokens.get("expires_in", 3600)

        self.tokens[new_access_token] = {
            "client_id": client.client_id,
            "external_token": new_access_token,
            "scopes": scopes or refresh_token.scopes,
        }

        if new_refresh_token != refresh_token.token:
            self.tokens[new_refresh_token] = {
                "client_id": client.client_id,
                "external_token": new_refresh_token,
                "scopes": scopes or refresh_token.scopes,
            }
            del self.tokens[refresh_token.token]

        return OAuthToken(
            access_token=new_access_token,
            token_type="bearer",
            expires_in=expires_in,
            refresh_token=new_refresh_token,
            scope=" ".join(scopes or refresh_token.scopes),
        )

    async def load_access_token(self, token: str) -> Optional[ExternalAccessToken]:
        """Load and validate an access token."""
        token_info = self.tokens.get(token)
        if token_info:
            return ExternalAccessToken(
                token=token,
                client_id=token_info["client_id"],
                scopes=token_info["scopes"],
                external_token=token_info.get("external_token"),
            )
        return None

    async def revoke_token(self, token: ExternalAccessToken | ExternalRefreshToken) -> None:
        """Revoke a token."""
        self.tokens.pop(token.token, None)


def create_github_provider() -> ExternalOAuthProvider:
    """Create an OAuth provider for GitHub integration."""
    return ExternalOAuthProvider(
        issuer_url=os.getenv("FASTMCP_AUTH_ISSUER_URL", "http://localhost:8000"),
        external_provider="github",
        external_client_id=os.getenv("GITHUB_CLIENT_ID", ""),
        external_client_secret=os.getenv("GITHUB_CLIENT_SECRET", ""),
        external_authorize_url="https://github.com/login/oauth/authorize",
        external_token_url="https://github.com/login/oauth/access_token",
        external_userinfo_url="https://api.github.com/user",
    )


def create_google_provider() -> ExternalOAuthProvider:
    """Create an OAuth provider for Google integration."""
    return ExternalOAuthProvider(
        issuer_url=os.getenv("FASTMCP_AUTH_ISSUER_URL", "http://localhost:8000"),
        external_provider="google",
        external_client_id=os.getenv("GOOGLE_CLIENT_ID", ""),
        external_client_secret=os.getenv("GOOGLE_CLIENT_SECRET", ""),
        external_authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
        external_token_url="https://oauth2.googleapis.com/token",
        external_userinfo_url="https://www.googleapis.com/oauth2/v1/userinfo",
    )
