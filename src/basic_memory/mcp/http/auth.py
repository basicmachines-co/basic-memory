"""Custom authentication providers for Basic Memory MCP server."""

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

    issuer_urn: str
    oauth_server_base_url: str
    mcp_server_url: str


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

        super().__init__(
            jwks_uri=settings.jwks_uri,
            audience=settings.audience,
            required_scopes=settings.required_scopes,
        )

        # Override the issuer with issuer_urn
        self.issuer = settings.issuer_urn
