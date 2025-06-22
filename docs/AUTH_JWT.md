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
│  MCP Inspector  │    │ basic-memory-    │    │  Basic Memory    │    │   Stytch    │
│   (Client)      │◄──►│     cloud        │◄──►│   MCP Server     │◄──►│  B2B Auth   │
│                 │    │ (OAuth Server)   │    │ (Resource Server)│    │             │
│                 │    │  (Port 3000)     │    │   (Port 8000)    │    │             │
└─────────────────┘    └──────────────────┘    └──────────────────┘    └─────────────┘
```

### Authentication Flow

1. **Resource Discovery**: Client discovers protected resource at basic-memory (port 8000)
2. **OAuth Discovery**: Client follows `authorization_servers` to basic-memory-cloud (port 3000)
3. **Client Registration**: Dynamic client registration via Stytch (proxied through basic-memory-cloud)
4. **Authorization**: User authorizes via basic-memory-cloud UI with Stytch authentication
5. **Token Exchange**: Authorization code exchanged for JWT access token via basic-memory-cloud
6. **MCP Access**: JWT bearer token validates access to basic-memory MCP endpoints

## Implementation Details

### OAuth Protected Resource Server

The Basic Memory MCP server acts as an OAuth 2.1 protected resource server. It provides discovery metadata that points clients to the authorization server (basic-memory-cloud):

```python
# src/basic_memory/mcp/http/main.py

@mcp.custom_route("/.well-known/oauth-protected-resource", methods=["GET"])
async def oauth_protected_resource(request):
    """OAuth 2.0 protected resource metadata (RFC 8707)."""
    return JSONResponse({
        "resource": auth_settings.mcp_server_url,
        "authorization_servers": [auth_settings.oauth_server_base_url],  # Points to basic-memory-cloud
        "scopes_supported": ["basic_memory:read", "basic_memory:write"],
        "bearer_methods_supported": ["header"],
        "resource_documentation": "https://github.com/basicmachines-co/basic-memory",
    })
```

### Custom Stytch Authentication Provider

FastMCP's built-in `BearerAuthProvider` expects HTTP(S) URLs for the JWT issuer, but Stytch uses URN-style identifiers like `stytch.com/project-...`. We created a custom auth provider to handle this:

```python
# src/basic_memory/mcp/http/auth.py

class BasicMemoryBearerAuthProvider(EnvBearerAuthProvider):
    """Bearer auth provider that supports Stytch's URN-style issuer format."""
    
    def __init__(self):
        settings = AuthSettings()
        self.auth_settings = settings
        
        super().__init__(
            jwks_uri=settings.jwks_uri,
            audience=settings.audience,
            required_scopes=settings.required_scopes,
        )
        
        # Override the issuer with issuer_urn (URN format)
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

# OAuth Server Configuration (points to basic-memory-cloud)
FASTMCP_AUTH_BEARER_OAUTH_SERVER_BASE_URL="http://localhost:3000"
FASTMCP_AUTH_BEARER_MCP_SERVER_URL="http://localhost:8000"
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
    "id": "session-test-8d63c5de-7ee0-4177-930a-c99a85a0f217"
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

### 2. OAuth Server Discovery

**Problem**: MCP clients need to discover OAuth endpoints for authentication.

**Solution**: basic-memory acts as a protected resource server that points clients to basic-memory-cloud as the authorization server via the `authorization_servers` field in protected resource metadata.

### 3. Scope Format Mismatch  

**Problem**: Stytch returns scopes as space-separated string (`"openid profile email"`), but FastMCP expects array format.

**Solution**: Temporarily disabled scope validation. Future enhancement could implement custom scope parsing.

### 4. Stytch Registration Endpoint

**Problem**: basic-memory-cloud needed to expose Stytch's dynamic client registration endpoint.

**Solution**: basic-memory-cloud's OAuth metadata includes `registration_endpoint` pointing to Stytch's OAuth registration API, enabling dynamic client registration for MCP Inspector.

## Testing the Implementation

### 1. Start the Services

```bash
# Terminal 1: Start basic-memory-cloud (OAuth authorization server)
cd ../basic-memory-cloud/web
export NUXT_STYTCH_PROJECT_ID="project-test-6d902c62-fc8f-40ea-a4b1-c260966d4655"
npm run dev  # Runs on port 3000

# Terminal 2: Start Basic Memory MCP server (protected resource server)
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
# Test protected resource discovery (basic-memory)
curl http://localhost:8000/.well-known/oauth-protected-resource

# Test OAuth authorization server discovery (basic-memory-cloud)
curl http://localhost:3000/.well-known/oauth-authorization-server

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

- `src/basic_memory/mcp/http/auth.py` - Custom Stytch auth provider
- `src/basic_memory/mcp/server.py` - FastMCP server with auth
- `src/basic_memory/mcp/http/main.py` - Protected resource metadata endpoint
- `.env` - Authentication configuration (not committed)
- `docs/AUTH_JWT.md` - This documentation
- `../basic-memory-cloud/web/server/routes/.well-known/oauth-authorization-server.get.ts` - OAuth server metadata

## References

- [OAuth 2.1 RFC](https://datatracker.ietf.org/doc/draft-ietf-oauth-v2-1/)
- [FastMCP Authentication](https://gofastmcp.com/servers/auth/bearer)
- [Stytch B2B Documentation](https://stytch.com/docs/b2b)
- [MCP Authentication Specification](https://spec.modelcontextprotocol.io/specification/server/authentication/)