"""OAuth authentication provider for Basic Memory MCP server."""

import secrets
from datetime import datetime, timedelta
from typing import Dict, Optional

import jwt
from mcp.server.auth.provider import (
    OAuthAuthorizationServerProvider,
    AuthorizationParams,
    AuthorizationCode,
    RefreshToken,
    AccessToken,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from loguru import logger


class BasicMemoryAuthorizationCode(AuthorizationCode):
    """Extended authorization code with additional metadata."""

    issuer_state: Optional[str] = None


class BasicMemoryRefreshToken(RefreshToken):
    """Extended refresh token with additional metadata."""

    pass


class BasicMemoryAccessToken(AccessToken):
    """Extended access token with additional metadata."""

    pass


class BasicMemoryOAuthProvider(
    OAuthAuthorizationServerProvider[
        BasicMemoryAuthorizationCode, BasicMemoryRefreshToken, BasicMemoryAccessToken
    ]
):
    """OAuth provider for Basic Memory MCP server.

    This is a simple in-memory implementation that can be extended
    to integrate with external OAuth providers or use persistent storage.
    """

    def __init__(self, issuer_url: str = "http://localhost:8000", secret_key: Optional[str] = None):
        self.issuer_url = issuer_url
        # Use environment variable for secret key if available, otherwise generate
        import os

        self.secret_key = (
            secret_key or os.getenv("FASTMCP_AUTH_SECRET_KEY") or secrets.token_urlsafe(32)
        )

        # In-memory storage - in production, use a proper database
        self.clients: Dict[str, OAuthClientInformationFull] = {}
        self.authorization_codes: Dict[str, BasicMemoryAuthorizationCode] = {}
        self.refresh_tokens: Dict[str, BasicMemoryRefreshToken] = {}
        self.access_tokens: Dict[str, BasicMemoryAccessToken] = {}

    async def get_client(self, client_id: str) -> Optional[OAuthClientInformationFull]:
        """Get a client by ID."""
        return self.clients.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        """Register a new OAuth client."""
        # Generate client ID if not provided
        if not client_info.client_id:
            client_info.client_id = secrets.token_urlsafe(16)

        # Generate client secret if not provided
        if not client_info.client_secret:
            client_info.client_secret = secrets.token_urlsafe(32)

        self.clients[client_info.client_id] = client_info
        logger.info(f"Registered OAuth client: {client_info.client_id}")

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        """Create an authorization URL for the OAuth flow.

        For basic-memory, we'll implement a simple authorization flow.
        In production, this might redirect to an external provider.
        """
        # Generate authorization code
        auth_code = secrets.token_urlsafe(32)

        # Store authorization code with metadata
        self.authorization_codes[auth_code] = BasicMemoryAuthorizationCode(
            code=auth_code,
            scopes=params.scopes or [],
            expires_at=(datetime.utcnow() + timedelta(minutes=10)).timestamp(),
            client_id=client.client_id,
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            issuer_state=params.state,
        )

        # In a real implementation, we'd redirect to an authorization page
        # For now, we'll just return the redirect URL with the code
        redirect_uri = str(params.redirect_uri)
        separator = "&" if "?" in redirect_uri else "?"

        auth_url = f"{redirect_uri}{separator}code={auth_code}"
        if params.state:
            auth_url += f"&state={params.state}"

        return auth_url

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> Optional[BasicMemoryAuthorizationCode]:
        """Load an authorization code."""
        code = self.authorization_codes.get(authorization_code)

        if code and code.client_id == client.client_id:
            # Check if expired
            if datetime.utcnow().timestamp() > code.expires_at:
                del self.authorization_codes[authorization_code]
                return None
            return code

        return None

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: BasicMemoryAuthorizationCode
    ) -> OAuthToken:
        """Exchange an authorization code for tokens."""
        # Generate tokens
        access_token = self._generate_access_token(client.client_id, authorization_code.scopes)
        refresh_token = secrets.token_urlsafe(32)

        # Store tokens
        expires_at = (datetime.utcnow() + timedelta(hours=1)).timestamp()

        self.access_tokens[access_token] = BasicMemoryAccessToken(
            token=access_token,
            client_id=client.client_id,
            scopes=authorization_code.scopes,
            expires_at=int(expires_at),
        )

        self.refresh_tokens[refresh_token] = BasicMemoryRefreshToken(
            token=refresh_token,
            client_id=client.client_id,
            scopes=authorization_code.scopes,
        )

        # Remove used authorization code
        del self.authorization_codes[authorization_code.code]

        return OAuthToken(
            access_token=access_token,
            token_type="bearer",
            expires_in=3600,  # 1 hour
            refresh_token=refresh_token,
            scope=" ".join(authorization_code.scopes) if authorization_code.scopes else None,
        )

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> Optional[BasicMemoryRefreshToken]:
        """Load a refresh token."""
        token = self.refresh_tokens.get(refresh_token)

        if token and token.client_id == client.client_id:
            return token

        return None

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: BasicMemoryRefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        """Exchange a refresh token for new tokens."""
        # Use requested scopes or original scopes
        token_scopes = scopes if scopes else refresh_token.scopes

        # Generate new tokens
        new_access_token = self._generate_access_token(client.client_id, token_scopes)
        new_refresh_token = secrets.token_urlsafe(32)

        # Store new tokens
        expires_at = (datetime.utcnow() + timedelta(hours=1)).timestamp()

        self.access_tokens[new_access_token] = BasicMemoryAccessToken(
            token=new_access_token,
            client_id=client.client_id,
            scopes=token_scopes,
            expires_at=int(expires_at),
        )

        self.refresh_tokens[new_refresh_token] = BasicMemoryRefreshToken(
            token=new_refresh_token,
            client_id=client.client_id,
            scopes=token_scopes,
        )

        # Remove old tokens
        del self.refresh_tokens[refresh_token.token]

        return OAuthToken(
            access_token=new_access_token,
            token_type="bearer",
            expires_in=3600,  # 1 hour
            refresh_token=new_refresh_token,
            scope=" ".join(token_scopes) if token_scopes else None,
        )

    async def load_access_token(self, token: str) -> Optional[BasicMemoryAccessToken]:
        """Load and validate an access token."""
        logger.debug("Loading access token, checking in-memory store first")
        access_token = self.access_tokens.get(token)

        if access_token:
            # Check if expired
            if access_token.expires_at and datetime.utcnow().timestamp() > access_token.expires_at:
                logger.debug("Token found in memory but expired, removing")
                del self.access_tokens[token]
                return None
            logger.debug("Token found in memory and valid")
            return access_token

        # Try to decode as JWT
        logger.debug("Token not in memory, attempting JWT decode with secret key")
        try:
            # Decode with audience verification - PyJWT expects the audience to match
            payload = jwt.decode(
                token,
                self.secret_key,
                algorithms=["HS256"],
                audience="basic-memory",  # Expecting this audience
                issuer=self.issuer_url,  # And this issuer
            )
            logger.debug(f"JWT decoded successfully: {payload}")
            return BasicMemoryAccessToken(
                token=token,
                client_id=payload.get("sub", ""),
                scopes=payload.get("scopes", []),
                expires_at=payload.get("exp"),
            )
        except jwt.InvalidTokenError as e:
            logger.error(f"JWT decode failed: {e}")
            return None

    async def revoke_token(self, token: BasicMemoryAccessToken | BasicMemoryRefreshToken) -> None:
        """Revoke an access or refresh token."""
        if isinstance(token, BasicMemoryAccessToken):
            self.access_tokens.pop(token.token, None)
        else:
            self.refresh_tokens.pop(token.token, None)

    def _generate_access_token(self, client_id: str, scopes: list[str]) -> str:
        """Generate a JWT access token."""
        payload = {
            "iss": self.issuer_url,
            "sub": client_id,
            "aud": "basic-memory",
            "exp": datetime.utcnow() + timedelta(hours=1),
            "iat": datetime.utcnow(),
            "scopes": scopes,
        }

        return jwt.encode(payload, self.secret_key, algorithm="HS256")
