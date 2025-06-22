# JWT Authentication Implementation

This document describes the implementation of OAuth 2.1 + JWT authentication for the Basic Memory MCP server using Stytch B2B as the identity provider.

## Overview

The authentication flow enables secure access to Basic Memory's MCP (Model Context Protocol) server through a complete OAuth 2.1 + PKCE flow with JWT token validation. The implementation supports:

- OAuth 2.1 with PKCE (Proof Key for Code Exchange)
- Dynamic client registration 
- JWT bearer token authentication
- Integration with Stytch B2B identity provider
- MCP Inspector compatibility

## Architecture

```
┌─────────────────┐    ┌──────────────────┐    ┌──────────────────┐    ┌─────────────┐
│  MCP Inspector  │    │  Basic Memory    │    │ basic-memory-    │    │   Stytch    │
│   (Client)      │◄──►│   MCP Server     │◄──►│     cloud        │◄──►│  B2B Auth   │
│                 │    │   (Port 8000)    │    │  (Port 3000)     │    │             │
└─────────────────┘    └──────────────────┘    └──────────────────┘    └─────────────┘
```

### Authentication Flow

1. **OAuth Discovery**: Client discovers auth endpoints via `/.well-known/oauth-authorization-server`
2. **Client Registration**: Dynamic client registration via `/api/oauth/register`
3. **Authorization**: User authorizes via Stytch-hosted auth flow
4. **Token Exchange**: Authorization code exchanged for JWT access token
5. **MCP Access**: JWT bearer token validates access to MCP endpoints

## Implementation Details

### Custom OAuth Proxy Routes

The Basic Memory MCP server uses FastMCP's `@mcp.custom_route` decorator to override built-in OAuth endpoints and proxy requests to basic-memory-cloud:

```python
# src/basic_memory/mcp/http/main.py

@mcp.custom_route("/.well-known/oauth-authorization-server", methods=["GET"])
async def oauth_authorization_server(request):
    """Proxy OAuth authorization server metadata to basic-memory-cloud."""
    # Forwards to basic-memory-cloud for Stytch integration

@mcp.custom_route("/api/oauth/register", methods=["POST"])  
async def oauth_client_registration(request):
    """Proxy OAuth client registration to basic-memory-cloud."""
    # Enables dynamic client registration support
```

### Custom Stytch Authentication Provider

FastMCP's built-in `BearerAuthProvider` expects HTTP(S) URLs for the JWT issuer, but Stytch uses URN-style identifiers like `stytch.com/project-...`. We created a custom auth provider to handle this:

```python
# src/basic_memory/mcp/auth.py

class StytchBearerAuthProvider(EnvBearerAuthProvider):
    """Bearer auth provider that supports Stytch's URN-style issuer format."""
    
    def __init__(self, settings: Optional[StytchBearerAuthSettings] = None):
        # Override issuer validation to support URN format
        self.issuer = settings.issuer_urn
```

### Environment Configuration

The authentication is configured via environment variables:

```bash
# .env

# FastMCP server settings
FASTMCP_SERVER_DEFAULT_AUTH_PROVIDER="bearer_env"

# Stytch JWT Authentication Settings
FASTMCP_AUTH_BEARER_JWKS_URI="https://test.stytch.com/v1/public/project-test-6d902c62-fc8f-40ea-a4b1-c260966d4655/.well-known/jwks.json"
FASTMCP_AUTH_BEARER_AUDIENCE="project-test-6d902c62-fc8f-40ea-a4b1-c260966d4655"
FASTMCP_AUTH_BEARER_ISSUER_URN="stytch.com/project-test-6d902c62-fc8f-40ea-a4b1-c260966d4655"
FASTMCP_AUTH_BEARER_REQUIRED_SCOPES='[]'  # Scope validation disabled for now
```

## JWT Token Structure

Stytch issues JWTs with the following structure:

```json
{
  "aud": ["project-test-6d902c62-fc8f-40ea-a4b1-c260966d4655"],
  "exp": 1750551286,
  "iss": "stytch.com/project-test-6d902c62-fc8f-40ea-a4b1-c260966d4655",
  "sub": "user-test-20a1df65-a62d-4a99-8feb-df834c5be8e7",
  "scope": "openid profile email",
  "https://stytch.com/session": {
    "id": "session-test-8d63c5de-7ee0-4177-930a-c99a85a0f217",
    // ... session details
  }
}
```

### Key Validation Points

- **Issuer**: URN format `stytch.com/project-...` (not HTTP URL)
- **Audience**: Stytch project ID 
- **Scopes**: Space-separated string (not array)
- **Signature**: Validated against Stytch's JWKS endpoint

## Challenges Solved

### 1. FastMCP Issuer URL Validation

**Problem**: FastMCP's `BearerAuthProvider` requires issuer to be a valid HTTP(S) URL, but Stytch uses URN format.

**Solution**: Created `StytchBearerAuthProvider` that extends `EnvBearerAuthProvider` and overrides issuer validation to accept URN format.

### 2. Dynamic Client Registration

**Problem**: FastMCP's built-in OAuth routes don't support dynamic client registration.

**Solution**: Used `@mcp.custom_route` decorator to override OAuth endpoints and proxy registration requests to basic-memory-cloud.

### 3. Scope Format Mismatch  

**Problem**: Stytch returns scopes as space-separated string (`"openid profile email"`), but FastMCP expects array format.

**Solution**: Temporarily disabled scope validation. Future enhancement could implement custom scope parsing.

### 4. Mock vs Real JWT Tokens

**Problem**: basic-memory-cloud was initially returning mock tokens instead of real JWTs.

**Solution**: Configured basic-memory-cloud to issue proper Stytch JWTs with correct claims and signature.

## Testing the Implementation

### 1. Start the Services

```bash
# Terminal 1: Start basic-memory-cloud (OAuth server)
cd ../basic-memory-cloud
npm run dev  # Runs on port 3000

# Terminal 2: Start Basic Memory MCP server  
cd basic-memory
python -m basic_memory.mcp.http.main  # Runs on port 8000
```

### 2. Test with MCP Inspector

1. Open MCP Inspector at http://localhost:6274
2. Add server: `http://localhost:8000/mcp`
3. Choose "OAuth" transport type
4. Complete OAuth flow (redirects to Stytch)
5. Verify successful MCP connection

### 3. Manual Testing

```bash
# Test OAuth discovery
curl http://localhost:8000/.well-known/oauth-authorization-server

# Test protected resource metadata
curl http://localhost:8000/.well-known/oauth-protected-resource

# Test MCP endpoint (requires valid JWT)
curl -H "Authorization: Bearer <jwt_token>" \
     -X POST http://localhost:8000/mcp/
```

## Future Enhancements

### Scope Validation

Implement proper scope parsing to handle Stytch's space-separated format:

```python
def parse_stytch_scopes(token_scope: str) -> list[str]:
    """Convert space-separated scopes to array format."""
    return token_scope.split() if isinstance(token_scope, str) else []
```

### Production Configuration

- Use production Stytch project (remove `-test-` from IDs)
- Configure proper CORS origins (remove wildcard `*`)
- Add rate limiting and monitoring
- Implement token refresh logic

### Error Handling

- Add detailed error responses for auth failures
- Implement proper logging for security events
- Add metrics for auth success/failure rates

## Security Considerations

- **HTTPS Required**: All OAuth flows must use HTTPS in production
- **Token Expiration**: JWTs have limited lifetime (configurable in Stytch)
- **Audience Validation**: Ensures tokens are intended for this service
- **Signature Verification**: Validates against Stytch's rotating keys via JWKS
- **CORS Policy**: Configure restrictive CORS in production

## Related Files

- `src/basic_memory/mcp/auth.py` - Custom Stytch auth provider
- `src/basic_memory/mcp/server.py` - FastMCP server with auth
- `src/basic_memory/mcp/http/main.py` - OAuth proxy routes
- `.env` - Authentication configuration (not committed)
- `docs/AUTH_JWT.md` - This documentation

## References

- [OAuth 2.1 RFC](https://datatracker.ietf.org/doc/draft-ietf-oauth-v2-1/)
- [FastMCP Authentication](https://gofastmcp.com/servers/auth/bearer)
- [Stytch B2B Documentation](https://stytch.com/docs/b2b)
- [MCP Authentication Specification](https://spec.modelcontextprotocol.io/specification/server/authentication/)