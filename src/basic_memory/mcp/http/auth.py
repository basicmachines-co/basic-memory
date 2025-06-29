"""Custom authentication providers for Basic Memory MCP server."""

from loguru import logger
from mcp.server.auth.provider import AccessToken
from fastmcp.server.auth.providers.bearer_env import (
    EnvBearerAuthProviderSettings,
    EnvBearerAuthProvider,
)


class AuthSettings(EnvBearerAuthProviderSettings):
    """
    Provides authentication settings for configuring access to servers.

    This class is used to store and provide essential authentication settings
    including issuer URN, OAuth server base URL, and the MCP server URL. It
    inherits from the EnvBearerAuthProviderSettings. These settings are
    typically used for managing authenticated communication with servers and
    services.

    Attributes:
        issuer_urn: str
            The unique identifier for the authentication token issuer.
        oauth_server_base_url: str
            The base URL for the OAuth authentication server.
        mcp_server_url: str
            The URL for the MCP (Managed Control Plane) server.
    """

    issuer_urn: str = "default-issuer-urn"
    oauth_server_base_url: str = "https://default-oauth-server.com"
    mcp_server_url: str = "https://default-mcp-server.com"
    stytch_project_id: str = "default-stytch-project-id"



class BasicMemoryBearerAuthProvider(EnvBearerAuthProvider):
    """
    Custom authentication class for managing bearer token-based authentication.

    This class initializes and configures authentication settings with additional security
    measures. It inherits from the EnvBearerAuthProvider superclass and provides an
    implementation that overrides some settings like the issuer

    This enables the issuer to have a urn value like "example.com/basic-memory" instead of a url like "https://example.com/basic-memory"
    """

    def __init__(self):
        """
        Initialize authentication provider with settings from environment variables.
        """
        settings = AuthSettings()  # pyright: ignore [reportCallIssue]
        self.auth_settings = settings

        logger.info(f"Auth Provider initialized with:")
        logger.info(f"  JWKS URI: {settings.jwks_uri}")
        logger.info(f"  Audience: {settings.audience}")
        logger.info(f"  Required Scopes: {settings.required_scopes}")
        logger.info(f"  Issuer URN: {settings.issuer_urn}")
        logger.info(f"  OAuth Server Base URL: {settings.oauth_server_base_url}")
        logger.info(f"  MCP Server URL: {settings.mcp_server_url}")

        super().__init__(
            jwks_uri=settings.jwks_uri,
            audience=settings.audience,
            required_scopes=settings.required_scopes,
            issuer=settings.oauth_server_base_url,
        )

        # Override the issuer with issuer_urn
        self.issuer = settings.issuer_urn
        logger.info(f"Auth Provider issuer set to: {self.issuer}")
        logger.info(f"  Stytch Project ID: {settings.stytch_project_id}")

    def get_stytch_registration_endpoint(self) -> str:
        """Generate Stytch OAuth registration endpoint URL."""
        project_id = self.auth_settings.stytch_project_id
        if project_id.startswith("project-test-"):
                base_url = "https://test.stytch.com/v1/public"
        else:
            base_url = "https://api.stytch.com/v1/public"
        return f"{base_url}/{project_id}/oauth2/register"

    async def load_access_token(self, token: str) -> AccessToken | None:
        """Override to add debug logging for JWT validation."""
        logger.info(f"JWT validation attempt with token: {token[:50]}...")
        
        try:
            result = await super().load_access_token(token)
            if result:
                logger.info(f"JWT validation SUCCESS: client_id={result.client_id}, scopes={result.scopes}")
                return result
            else:
                logger.warning("JWT validation FAILED: token invalid or expired")
                return None
        except Exception as e:
            logger.error(f"JWT validation ERROR: {e}")
            return None
