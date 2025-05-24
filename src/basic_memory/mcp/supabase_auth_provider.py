"""Supabase OAuth provider for Basic Memory MCP server."""

import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

import httpx
import jwt
from loguru import logger
from mcp.server.auth.provider import (
    OAuthAuthorizationServerProvider,
    AuthorizationParams,
    AuthorizationCode,
    RefreshToken,
    AccessToken,
    TokenError,
    AuthorizeError,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken


@dataclass
class SupabaseAuthorizationCode(AuthorizationCode):
    """Authorization code with Supabase metadata."""

    user_id: Optional[str] = None
    email: Optional[str] = None


@dataclass
class SupabaseRefreshToken(RefreshToken):
    """Refresh token with Supabase metadata."""

    supabase_refresh_token: Optional[str] = None
    user_id: Optional[str] = None


@dataclass
class SupabaseAccessToken(AccessToken):
    """Access token with Supabase metadata."""

    supabase_access_token: Optional[str] = None
    user_id: Optional[str] = None
    email: Optional[str] = None


class SupabaseOAuthProvider(
    OAuthAuthorizationServerProvider[
        SupabaseAuthorizationCode, SupabaseRefreshToken, SupabaseAccessToken
    ]
):
    """OAuth provider that integrates with Supabase Auth.

    This provider uses Supabase as the authentication backend while
    maintaining compatibility with MCP's OAuth requirements.
    """

    def __init__(
        self,
        supabase_url: str,
        supabase_anon_key: str,
        supabase_service_key: Optional[str] = None,
        issuer_url: str = "http://localhost:8000",
    ):
        self.supabase_url = supabase_url.rstrip("/")
        self.supabase_anon_key = supabase_anon_key
        self.supabase_service_key = supabase_service_key or supabase_anon_key
        self.issuer_url = issuer_url

        # HTTP client for Supabase API calls
        self.http_client = httpx.AsyncClient()

        # Temporary storage for auth flows (in production, use Supabase DB)
        self.pending_auth_codes: Dict[str, SupabaseAuthorizationCode] = {}
        self.mcp_to_supabase_tokens: Dict[str, Dict[str, Any]] = {}

    async def get_client(self, client_id: str) -> Optional[OAuthClientInformationFull]:
        """Get a client from Supabase.

        In production, this would query a clients table in Supabase.
        """
        # For now, we'll validate against a configured list of allowed clients
        # In production, query Supabase DB for client info
        allowed_clients = os.getenv("SUPABASE_ALLOWED_CLIENTS", "").split(",")

        if client_id in allowed_clients:
            return OAuthClientInformationFull(
                client_id=client_id,
                client_secret="",  # Supabase handles secrets
                redirect_uris=[],  # Supabase handles redirect URIs
            )

        return None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        """Register a new OAuth client in Supabase.

        In production, this would insert into a clients table.
        """
        # For development, we just log the registration
        logger.info(f"Would register client {client_info.client_id} in Supabase")

        # In production:
        # await self.supabase.table('oauth_clients').insert({
        #     'client_id': client_info.client_id,
        #     'client_secret': client_info.client_secret,
        #     'metadata': client_info.client_metadata,
        # }).execute()

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        """Create authorization URL redirecting to Supabase Auth.

        This initiates the OAuth flow with Supabase as the identity provider.
        """
        # Generate state for this auth request
        state = secrets.token_urlsafe(32)

        # Store the authorization request
        self.pending_auth_codes[state] = SupabaseAuthorizationCode(
            code=state,
            scopes=params.scopes or [],
            expires_at=(datetime.utcnow() + timedelta(minutes=10)).timestamp(),
            client_id=client.client_id,
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
        )

        # Build Supabase auth URL
        auth_params = {
            "redirect_to": f"{self.issuer_url}/auth/callback",
            "scopes": " ".join(params.scopes or ["openid", "email"]),
            "state": state,
        }

        # Use Supabase's OAuth endpoint
        auth_url = f"{self.supabase_url}/auth/v1/authorize"
        query_string = "&".join(f"{k}={v}" for k, v in auth_params.items())

        return f"{auth_url}?{query_string}"

    async def handle_supabase_callback(self, code: str, state: str) -> str:
        """Handle callback from Supabase after user authentication."""
        # Get the original auth request
        auth_request = self.pending_auth_codes.get(state)
        if not auth_request:
            raise AuthorizeError(
                error="invalid_request",
                error_description="Invalid state parameter",
            )

        # Exchange code with Supabase for tokens
        token_response = await self.http_client.post(
            f"{self.supabase_url}/auth/v1/token",
            json={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": f"{self.issuer_url}/auth/callback",
            },
            headers={
                "apikey": self.supabase_anon_key,
                "Authorization": f"Bearer {self.supabase_anon_key}",
            },
        )

        if not token_response.is_success:
            raise AuthorizeError(
                error="server_error",
                error_description="Failed to exchange code with Supabase",
            )

        supabase_tokens = token_response.json()

        # Get user info from Supabase
        user_response = await self.http_client.get(
            f"{self.supabase_url}/auth/v1/user",
            headers={
                "apikey": self.supabase_anon_key,
                "Authorization": f"Bearer {supabase_tokens['access_token']}",
            },
        )

        user_data = user_response.json() if user_response.is_success else {}

        # Generate MCP authorization code
        mcp_code = secrets.token_urlsafe(32)

        # Update auth request with user info
        auth_request.code = mcp_code
        auth_request.user_id = user_data.get("id")
        auth_request.email = user_data.get("email")

        # Store mapping
        self.pending_auth_codes[mcp_code] = auth_request
        self.mcp_to_supabase_tokens[mcp_code] = {
            "supabase_tokens": supabase_tokens,
            "user": user_data,
        }

        # Clean up old state
        del self.pending_auth_codes[state]

        # Redirect back to client
        redirect_uri = str(auth_request.redirect_uri)
        separator = "&" if "?" in redirect_uri else "?"

        return f"{redirect_uri}{separator}code={mcp_code}&state={state}"

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> Optional[SupabaseAuthorizationCode]:
        """Load an authorization code."""
        code = self.pending_auth_codes.get(authorization_code)

        if code and code.client_id == client.client_id:
            # Check expiration
            if datetime.utcnow().timestamp() > code.expires_at:
                del self.pending_auth_codes[authorization_code]
                return None
            return code

        return None

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: SupabaseAuthorizationCode
    ) -> OAuthToken:
        """Exchange authorization code for tokens."""
        # Get stored Supabase tokens
        token_data = self.mcp_to_supabase_tokens.get(authorization_code.code)
        if not token_data:
            raise TokenError(error="invalid_grant", error_description="Invalid authorization code")

        supabase_tokens = token_data["supabase_tokens"]
        user = token_data["user"]

        # Generate MCP tokens that wrap Supabase tokens
        access_token = self._generate_mcp_token(
            client_id=client.client_id,
            user_id=user.get("id", ""),
            email=user.get("email", ""),
            scopes=authorization_code.scopes,
            supabase_access_token=supabase_tokens["access_token"],
        )

        refresh_token = secrets.token_urlsafe(32)

        # Store the token mapping
        self.mcp_to_supabase_tokens[access_token] = {
            "client_id": client.client_id,
            "user_id": user.get("id"),
            "email": user.get("email"),
            "supabase_access_token": supabase_tokens["access_token"],
            "supabase_refresh_token": supabase_tokens["refresh_token"],
            "scopes": authorization_code.scopes,
        }

        # Store refresh token mapping
        self.mcp_to_supabase_tokens[refresh_token] = {
            "client_id": client.client_id,
            "user_id": user.get("id"),
            "supabase_refresh_token": supabase_tokens["refresh_token"],
            "scopes": authorization_code.scopes,
        }

        # Clean up authorization code
        del self.pending_auth_codes[authorization_code.code]
        del self.mcp_to_supabase_tokens[authorization_code.code]

        return OAuthToken(
            access_token=access_token,
            token_type="bearer",
            expires_in=supabase_tokens.get("expires_in", 3600),
            refresh_token=refresh_token,
            scope=" ".join(authorization_code.scopes) if authorization_code.scopes else None,
        )

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> Optional[SupabaseRefreshToken]:
        """Load a refresh token."""
        token_data = self.mcp_to_supabase_tokens.get(refresh_token)

        if token_data and token_data["client_id"] == client.client_id:
            return SupabaseRefreshToken(
                token=refresh_token,
                client_id=client.client_id,
                scopes=token_data["scopes"],
                supabase_refresh_token=token_data["supabase_refresh_token"],
                user_id=token_data.get("user_id"),
            )

        return None

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: SupabaseRefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        """Exchange refresh token for new tokens using Supabase."""
        # Refresh with Supabase
        token_response = await self.http_client.post(
            f"{self.supabase_url}/auth/v1/token",
            json={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token.supabase_refresh_token,
            },
            headers={
                "apikey": self.supabase_anon_key,
                "Authorization": f"Bearer {self.supabase_anon_key}",
            },
        )

        if not token_response.is_success:
            raise TokenError(
                error="invalid_grant",
                error_description="Failed to refresh with Supabase",
            )

        supabase_tokens = token_response.json()

        # Get updated user info
        user_response = await self.http_client.get(
            f"{self.supabase_url}/auth/v1/user",
            headers={
                "apikey": self.supabase_anon_key,
                "Authorization": f"Bearer {supabase_tokens['access_token']}",
            },
        )

        user_data = user_response.json() if user_response.is_success else {}

        # Generate new MCP tokens
        new_access_token = self._generate_mcp_token(
            client_id=client.client_id,
            user_id=user_data.get("id", ""),
            email=user_data.get("email", ""),
            scopes=scopes or refresh_token.scopes,
            supabase_access_token=supabase_tokens["access_token"],
        )

        new_refresh_token = secrets.token_urlsafe(32)

        # Update token mappings
        self.mcp_to_supabase_tokens[new_access_token] = {
            "client_id": client.client_id,
            "user_id": user_data.get("id"),
            "email": user_data.get("email"),
            "supabase_access_token": supabase_tokens["access_token"],
            "supabase_refresh_token": supabase_tokens["refresh_token"],
            "scopes": scopes or refresh_token.scopes,
        }

        self.mcp_to_supabase_tokens[new_refresh_token] = {
            "client_id": client.client_id,
            "user_id": user_data.get("id"),
            "supabase_refresh_token": supabase_tokens["refresh_token"],
            "scopes": scopes or refresh_token.scopes,
        }

        # Clean up old tokens
        del self.mcp_to_supabase_tokens[refresh_token.token]

        return OAuthToken(
            access_token=new_access_token,
            token_type="bearer",
            expires_in=supabase_tokens.get("expires_in", 3600),
            refresh_token=new_refresh_token,
            scope=" ".join(scopes or refresh_token.scopes),
        )

    async def load_access_token(self, token: str) -> Optional[SupabaseAccessToken]:
        """Load and validate an access token."""
        # First check our mapping
        token_data = self.mcp_to_supabase_tokens.get(token)
        if token_data:
            return SupabaseAccessToken(
                token=token,
                client_id=token_data["client_id"],
                scopes=token_data["scopes"],
                supabase_access_token=token_data.get("supabase_access_token"),
                user_id=token_data.get("user_id"),
                email=token_data.get("email"),
            )

        # Try to decode as JWT
        try:
            # Verify with Supabase's JWT secret
            payload = jwt.decode(
                token,
                os.getenv("SUPABASE_JWT_SECRET", ""),
                algorithms=["HS256"],
                audience="authenticated",
            )

            return SupabaseAccessToken(
                token=token,
                client_id=payload.get("client_id", ""),
                scopes=payload.get("scopes", []),
                user_id=payload.get("sub"),
                email=payload.get("email"),
            )
        except jwt.InvalidTokenError:
            pass

        # Validate with Supabase
        user_response = await self.http_client.get(
            f"{self.supabase_url}/auth/v1/user",
            headers={
                "apikey": self.supabase_anon_key,
                "Authorization": f"Bearer {token}",
            },
        )

        if user_response.is_success:
            user_data = user_response.json()
            return SupabaseAccessToken(
                token=token,
                client_id="",  # Unknown client for direct Supabase tokens
                scopes=[],
                supabase_access_token=token,
                user_id=user_data.get("id"),
                email=user_data.get("email"),
            )

        return None

    async def revoke_token(self, token: SupabaseAccessToken | SupabaseRefreshToken) -> None:
        """Revoke a token."""
        # Remove from our mapping
        self.mcp_to_supabase_tokens.pop(token.token, None)

        # In production, also revoke in Supabase:
        # await self.supabase.auth.admin.sign_out(token.user_id)

    def _generate_mcp_token(
        self,
        client_id: str,
        user_id: str,
        email: str,
        scopes: list[str],
        supabase_access_token: str,
    ) -> str:
        """Generate an MCP token that wraps Supabase authentication."""
        payload = {
            "iss": self.issuer_url,
            "sub": user_id,
            "client_id": client_id,
            "email": email,
            "scopes": scopes,
            "supabase_token": supabase_access_token[:10] + "...",  # Reference only
            "exp": datetime.utcnow() + timedelta(hours=1),
            "iat": datetime.utcnow(),
        }

        # Use Supabase JWT secret if available
        secret = os.getenv("SUPABASE_JWT_SECRET", secrets.token_urlsafe(32))

        return jwt.encode(payload, secret, algorithm="HS256")
