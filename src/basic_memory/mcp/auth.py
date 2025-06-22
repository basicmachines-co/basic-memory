"""Custom authentication providers for Basic Memory MCP server."""

from typing import Optional
from fastmcp.server.auth.providers.bearer import BearerAuthProvider
from fastmcp.server.auth.providers.bearer_env import (
    EnvBearerAuthProviderSettings,
    EnvBearerAuthProvider,
)


class StytchBearerAuthSettings(EnvBearerAuthProviderSettings):
    """Settings for Stytch-compatible bearer auth that allows URN-style issuers."""

    issuer_urn: str | None = None


class StytchBearerAuthProvider(EnvBearerAuthProvider):
    """Bearer auth provider that supports Stytch's URN-style issuer format."""

    def __init__(self, settings: Optional[StytchBearerAuthSettings] = None):
        """Initialize with custom settings that don't validate issuer as URL."""
        if settings is None:
            settings = StytchBearerAuthSettings()

        self.stytch_settings = settings

        super().__init__(
            jwks_uri=settings.jwks_uri,
            audience=settings.audience,
            required_scopes=settings.required_scopes,
        )

        # Override the issuer with issuer_urn
        self.issuer = settings.issuer_urn 
