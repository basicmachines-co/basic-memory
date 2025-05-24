# OAuth Quick Start

Basic Memory supports OAuth authentication for secure access control. For detailed documentation, see [OAuth Authentication Guide](docs/OAuth%20Authentication%20Guide.md).

## Quick Test with MCP Inspector

```bash
# 1. Set a consistent secret key
export FASTMCP_AUTH_SECRET_KEY="test-secret-key"

# 2. Start server with OAuth
FASTMCP_AUTH_ENABLED=true basic-memory mcp --transport streamable-http

# 3. In another terminal, get a test token
export FASTMCP_AUTH_SECRET_KEY="test-secret-key"  # Same key!
basic-memory auth test-auth

# 4. Copy the access token and use in MCP Inspector:
#    - Server URL: http://localhost:8000/mcp 
#    - Transport: streamable-http
#    - Custom Headers:
#      Authorization: Bearer YOUR_ACCESS_TOKEN
#      Accept: application/json, text/event-stream
```

## OAuth Endpoints

- `GET /authorize` - Authorization endpoint
- `POST /token` - Token exchange endpoint
- `GET /.well-known/oauth-authorization-server` - OAuth metadata

## Common Issues

1. **401 Unauthorized**: Make sure you're using the same secret key for both server and client
2. **404 Not Found**: Use `/authorize` not `/auth/authorize`
3. **Token Invalid**: Tokens don't persist across server restarts with basic provider

## Documentation

- [OAuth Authentication Guide](docs/OAuth%20Authentication%20Guide.md) - Complete setup guide
- [Supabase OAuth Setup](docs/Supabase%20OAuth%20Setup.md) - Production deployment
- [External OAuth Providers](docs/External%20OAuth%20Providers.md) - GitHub, Google integration