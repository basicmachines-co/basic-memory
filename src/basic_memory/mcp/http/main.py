"""
Basic Memory HTTP MCP Server Entrypoint

This module provides a generic HTTP streamable entrypoint for the Basic Memory MCP server
that enables OAuth authentication via proxy endpoints. It wraps the core MCP server with:

- OAuth 2.1 + PKCE authentication flow
- JWT bearer token validation
- Dynamic client registration support
- Proxy routes for external OAuth providers

The entrypoint is designed to be cloud-ready and configurable via environment variables,
making it suitable for deployment in containerized environments while maintaining
compatibility with local development.

Key Features:
- Proxies OAuth requests to external authentication services
- Validates JWT tokens using JWKS endpoints
- Supports URN-style issuer formats (e.g., Stytch B2B)
- Configurable server URLs for multi-environment deployment
- CORS support for web-based MCP clients

Environment Variables:
- OAUTH_SERVER_BASE_URL: Base URL for OAuth server that supports dynamic client registration
- MCP_SERVER_URL: This server's URL for OAuth metadata
- FASTMCP_AUTH_BEARER_*: JWT validation settings

Usage:
    python -m basic_memory.mcp.http.main

    Or in production:
    uvicorn basic_memory.mcp.http.main:app --host 0.0.0.0 --port 8000
"""

import uvicorn
from dotenv import load_dotenv
from fastapi.middleware.cors import CORSMiddleware

from basic_memory.mcp.http.auth import BasicMemoryBearerAuthProvider

load_dotenv()

from basic_memory.mcp.server import mcp  # noqa: E402

# Import mcp tools to register them
import basic_memory.mcp.tools  # noqa: E402, F401

# Import prompts to register them
import basic_memory.mcp.prompts  # noqa: E402, F401

# Access auth settings from the MCP server's auth provider
auth_provider = BasicMemoryBearerAuthProvider()
mcp.auth = auth_provider


@mcp.custom_route("/.well-known/oauth-protected-resource", methods=["GET"])
async def oauth_protected_resource(request):
    """OAuth 2.0 protected resource metadata (RFC 8707)."""
    from fastapi.responses import JSONResponse

    headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "*",
    }

    return JSONResponse(
        {
            "resource": auth_provider.auth_settings.mcp_server_url,
            "authorization_servers": [auth_provider.auth_settings.oauth_server_base_url],
            "scopes_supported": ["basic_memory:read", "basic_memory:write"],
            "bearer_methods_supported": ["header"],
            "resource_documentation": "https://github.com/basicmachines-co/basic-memory",
        },
        headers=headers,
    )


# Create the FastMCP app 
app = mcp.http_app()  

# Add CORS middleware to the FastMCP app
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

print(f"MCP app type: {type(app)}")
print(f"MCP app routes: {getattr(app, 'routes', 'No routes attribute')}")

if __name__ == "__main__":
    uvicorn.run(app, host=mcp.settings.host, port=mcp.settings.port)