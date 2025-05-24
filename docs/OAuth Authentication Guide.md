# OAuth Authentication Guide

Basic Memory MCP server supports OAuth 2.1 authentication for secure access control. This guide covers setup, testing, and production deployment.

## Quick Start

### 1. Enable OAuth

```bash
# Set environment variable
export FASTMCP_AUTH_ENABLED=true

# Or use .env file
echo "FASTMCP_AUTH_ENABLED=true" >> .env
```

### 2. Start the Server

```bash
basic-memory mcp --transport streamable-http
```

### 3. Test with MCP Inspector

Since the basic auth provider uses in-memory storage with per-instance secret keys, you'll need to use a consistent approach:

#### Option A: Use Environment Variable for Secret Key

```bash
# Set a fixed secret key for testing
export FASTMCP_AUTH_SECRET_KEY="your-test-secret-key"

# Start the server
FASTMCP_AUTH_ENABLED=true basic-memory mcp --transport streamable-http

# In another terminal, register a client
basic-memory auth register-client --client-id=test-client

# Get a token using the same secret key
basic-memory auth test-auth
```

#### Option B: Use the Built-in Test Endpoint

```bash
# Start server with OAuth
FASTMCP_AUTH_ENABLED=true basic-memory mcp --transport streamable-http

# Register a client and get token in one step
curl -X POST http://localhost:8000/register \
  -H "Content-Type: application/json" \
  -d '{"client_metadata": {"client_name": "Test Client"}}'

# Use the returned client_id and client_secret
curl -X POST http://localhost:8000/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=client_credentials&client_id=YOUR_CLIENT_ID&client_secret=YOUR_CLIENT_SECRET"
```

### 4. Configure MCP Inspector

1. Open MCP Inspector
2. Configure:
   - Server URL: `http://localhost:8000/mcp/` (note the trailing slash!)
   - Transport: `streamable-http`
   - Custom Headers:
     ```
     Authorization: Bearer YOUR_ACCESS_TOKEN
     Accept: application/json, text/event-stream
     ```

## OAuth Endpoints

The server provides these OAuth endpoints automatically:

- `GET /authorize` - Authorization endpoint
- `POST /token` - Token exchange endpoint
- `GET /.well-known/oauth-authorization-server` - OAuth metadata
- `POST /register` - Client registration (if enabled)
- `POST /revoke` - Token revocation (if enabled)

## OAuth Flow

### Standard Authorization Code Flow

1. **Get Authorization Code**:
   ```bash
   curl "http://localhost:8000/authorize?client_id=YOUR_CLIENT_ID&redirect_uri=http://localhost:8000/callback&response_type=code&code_challenge=YOUR_CHALLENGE&code_challenge_method=S256"
   ```

2. **Exchange Code for Token**:
   ```bash
   curl -X POST http://localhost:8000/token \
     -H "Content-Type: application/x-www-form-urlencoded" \
     -d "grant_type=authorization_code&code=AUTH_CODE&client_id=CLIENT_ID&client_secret=CLIENT_SECRET&code_verifier=YOUR_VERIFIER"
   ```

3. **Use Access Token**:
   ```bash
   curl http://localhost:8000/mcp \
     -H "Authorization: Bearer ACCESS_TOKEN"
   ```

## Production Deployment

### Using Supabase Auth

For production, use Supabase for persistent auth storage:

```bash
# Configure environment
FASTMCP_AUTH_ENABLED=true
FASTMCP_AUTH_PROVIDER=supabase
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_ANON_KEY=your-anon-key
SUPABASE_SERVICE_KEY=your-service-key

# Start server
basic-memory mcp --transport streamable-http --host 0.0.0.0
```

### Security Requirements

1. **HTTPS Required**: OAuth requires HTTPS in production (localhost exception for testing)
2. **PKCE Support**: Claude.ai requires PKCE for authorization
3. **Token Expiration**: Access tokens expire after 1 hour
4. **Scopes**: Supported scopes are `read`, `write`, and `admin`

## Connecting from Claude.ai

1. **Deploy with HTTPS**:
   ```bash
   # Use ngrok for testing
   ngrok http 8000
   
   # Or deploy to cloud provider
   ```

2. **Configure in Claude.ai**:
   - Go to Settings → Integrations
   - Click "Add More"
   - Enter: `https://your-server.com/mcp`
   - Click "Connect"
   - Authorize in the popup window

## Debugging

### Common Issues

1. **401 Unauthorized**: 
   - Check token is valid and not expired
   - Verify secret key consistency
   - Ensure bearer token format: `Authorization: Bearer TOKEN`

2. **404 on Auth Endpoints**:
   - Endpoints are at root, not under `/auth`
   - Use `/authorize` not `/auth/authorize`

3. **Token Validation Fails**:
   - Basic provider uses in-memory storage
   - Tokens don't persist across server restarts
   - Use same secret key for testing

### Debug Commands

```bash
# Check OAuth metadata
curl http://localhost:8000/.well-known/oauth-authorization-server

# Enable debug logging
export FASTMCP_LOG_LEVEL=DEBUG

# Test token directly
curl http://localhost:8000/mcp \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -v
```

## Provider Options

- **basic**: In-memory storage (development only)
- **supabase**: Recommended for production
- **github**: GitHub OAuth integration
- **google**: Google OAuth integration

## Example Test Script

```python
import httpx
import asyncio
from urllib.parse import urlparse, parse_qs

async def test_oauth_flow():
    """Test the full OAuth flow"""
    client_id = "test-client"
    client_secret = "test-secret"
    
    async with httpx.AsyncClient() as client:
        # 1. Get authorization code
        auth_response = await client.get(
            "http://localhost:8000/authorize",
            params={
                "client_id": client_id,
                "redirect_uri": "http://localhost:8000/callback",
                "response_type": "code",
                "code_challenge": "test-challenge",
                "code_challenge_method": "S256",
                "state": "test-state"
            }
        )
        
        # Extract code from redirect URL
        redirect_url = auth_response.headers.get("Location")
        parsed = urlparse(redirect_url)
        code = parse_qs(parsed.query)["code"][0]
        
        # 2. Exchange for token
        token_response = await client.post(
            "http://localhost:8000/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "code_verifier": "test-verifier",
                "redirect_uri": "http://localhost:8000/callback"
            }
        )
        
        tokens = token_response.json()
        print(f"Access token: {tokens['access_token']}")
        
        # 3. Test MCP endpoint
        mcp_response = await client.post(
            "http://localhost:8000/mcp",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
            json={"method": "initialize", "params": {}}
        )
        
        print(f"MCP Response: {mcp_response.status_code}")

asyncio.run(test_oauth_flow())
```

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `FASTMCP_AUTH_ENABLED` | Enable OAuth authentication | `false` |
| `FASTMCP_AUTH_PROVIDER` | OAuth provider type | `basic` |
| `FASTMCP_AUTH_SECRET_KEY` | JWT signing key (basic provider) | Random |
| `FASTMCP_AUTH_ISSUER_URL` | OAuth issuer URL | `http://localhost:8000` |
| `FASTMCP_AUTH_REQUIRED_SCOPES` | Required scopes (comma-separated) | `read,write` |

## Next Steps

- [Supabase OAuth Setup](./Supabase%20OAuth%20Setup.md) - Production auth setup
- [External OAuth Providers](./External%20OAuth%20Providers.md) - GitHub, Google integration
- [MCP OAuth Specification](https://modelcontextprotocol.io/specification/2025-03-26/basic/authorization) - Official spec