# Supabase OAuth Setup for Basic Memory

This guide explains how to set up Supabase as the OAuth provider for Basic Memory MCP server in production.

## Prerequisites

1. A Supabase project (create one at [supabase.com](https://supabase.com))
2. Basic Memory MCP server deployed
3. Environment variables configuration

## Overview

The Supabase OAuth provider offers:
- Production-ready authentication with persistent storage
- User management through Supabase Auth
- JWT token validation
- Integration with Supabase's security features
- Support for social logins (GitHub, Google, etc.)

## Setup Steps

### 1. Get Supabase Credentials

From your Supabase project dashboard:

1. Go to Settings > API
2. Copy these values:
   - `Project URL` → `SUPABASE_URL`
   - `anon public` key → `SUPABASE_ANON_KEY`
   - `service_role` key → `SUPABASE_SERVICE_KEY` (keep this secret!)
   - JWT secret → `SUPABASE_JWT_SECRET` (under Settings > API > JWT Settings)

### 2. Configure Environment Variables

Create a `.env` file:

```bash
# Enable OAuth
FASTMCP_AUTH_ENABLED=true
FASTMCP_AUTH_PROVIDER=supabase

# Your MCP server URL
FASTMCP_AUTH_ISSUER_URL=https://your-mcp-server.com

# Supabase configuration
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_ANON_KEY=your-anon-key
SUPABASE_SERVICE_KEY=your-service-key
SUPABASE_JWT_SECRET=your-jwt-secret

# Allowed OAuth clients (comma-separated)
SUPABASE_ALLOWED_CLIENTS=web-app,mobile-app,cli-tool

# Required scopes
FASTMCP_AUTH_REQUIRED_SCOPES=read,write
```

### 3. Create OAuth Clients Table (Optional)

For production, create a table to store OAuth clients in Supabase:

```sql
CREATE TABLE oauth_clients (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  client_id TEXT UNIQUE NOT NULL,
  client_secret TEXT NOT NULL,
  name TEXT,
  redirect_uris TEXT[],
  allowed_scopes TEXT[],
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Create an index for faster lookups
CREATE INDEX idx_oauth_clients_client_id ON oauth_clients(client_id);

-- RLS policies
ALTER TABLE oauth_clients ENABLE ROW LEVEL SECURITY;

-- Only service role can manage clients
CREATE POLICY "Service role can manage clients" ON oauth_clients
  FOR ALL USING (auth.jwt()->>'role' = 'service_role');
```

### 4. Set Up Auth Flow

The Supabase OAuth provider handles the following flow:

1. **Client Authorization Request**
   ```
   GET /authorize?client_id=web-app&redirect_uri=https://app.com/callback
   ```

2. **Redirect to Supabase Auth**
   - User authenticates with Supabase (email/password, magic link, or social login)
   - Supabase redirects back to your MCP server

3. **Token Exchange**
   ```
   POST /token
   Content-Type: application/x-www-form-urlencoded
   
   grant_type=authorization_code&code=xxx&client_id=web-app
   ```

4. **Access Protected Resources**
   ```
   GET /mcp
   Authorization: Bearer <access_token>
   ```

### 5. Enable Social Logins (Optional)

In Supabase dashboard:

1. Go to Authentication > Providers
2. Enable desired providers (GitHub, Google, etc.)
3. Configure OAuth apps for each provider
4. Users can now log in via social providers

### 6. User Management

Supabase provides:
- User registration and login
- Password reset flows
- Email verification
- User metadata storage
- Admin APIs for user management

Access user data in your MCP tools:

```python
# In your MCP tool
async def get_user_info(ctx: Context):
    # The token is already validated by the OAuth middleware
    user_id = ctx.auth.user_id
    email = ctx.auth.email
    
    # Use Supabase client to get more user data if needed
    user = await supabase.auth.admin.get_user_by_id(user_id)
    return user
```

### 7. Production Deployment

1. **Environment Security**
   - Never expose `SUPABASE_SERVICE_KEY`
   - Use environment variables, not hardcoded values
   - Rotate keys periodically

2. **HTTPS Required**
   - Always use HTTPS in production
   - Configure proper SSL certificates

3. **Rate Limiting**
   - Implement rate limiting for auth endpoints
   - Use Supabase's built-in rate limiting

4. **Monitoring**
   - Monitor auth logs in Supabase dashboard
   - Set up alerts for suspicious activity

## Testing

### Local Development

For local testing with Supabase:

```bash
# Start MCP server with Supabase auth
FASTMCP_AUTH_ENABLED=true \
FASTMCP_AUTH_PROVIDER=supabase \
SUPABASE_URL=http://localhost:54321 \
SUPABASE_ANON_KEY=your-local-anon-key \
bm mcp --transport streamable-http
```

### Test Authentication Flow

```python
import httpx
import asyncio

async def test_supabase_auth():
    # 1. Register/login with Supabase directly
    supabase_url = "https://your-project.supabase.co"
    
    # 2. Get MCP authorization URL
    response = await httpx.get(
        "http://localhost:8000/authorize",
        params={
            "client_id": "web-app",
            "redirect_uri": "http://localhost:3000/callback",
            "response_type": "code",
        }
    )
    
    # 3. User logs in via Supabase
    # 4. Exchange code for MCP tokens
    # 5. Access protected resources

asyncio.run(test_supabase_auth())
```

## Advanced Configuration

### Custom User Metadata

Store additional user data in Supabase:

```sql
-- Add custom fields to auth.users
ALTER TABLE auth.users 
ADD COLUMN IF NOT EXISTS metadata JSONB DEFAULT '{}';

-- Or create a separate profiles table
CREATE TABLE profiles (
  id UUID REFERENCES auth.users PRIMARY KEY,
  username TEXT UNIQUE,
  avatar_url TEXT,
  bio TEXT,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
```

### Row Level Security (RLS)

Protect user data with RLS:

```sql
-- Users can only access their own data
CREATE POLICY "Users can view own profile" ON profiles
  FOR SELECT USING (auth.uid() = id);

CREATE POLICY "Users can update own profile" ON profiles
  FOR UPDATE USING (auth.uid() = id);
```

### Custom Claims

Add custom claims to JWT tokens:

```sql
-- Function to add custom claims
CREATE OR REPLACE FUNCTION custom_jwt_claims()
RETURNS JSON AS $$
BEGIN
  RETURN json_build_object(
    'user_role', current_setting('request.jwt.claims')::json->>'user_role',
    'permissions', current_setting('request.jwt.claims')::json->>'permissions'
  );
END;
$$ LANGUAGE plpgsql;
```

## Troubleshooting

### Common Issues

1. **Invalid JWT Secret**
   - Ensure `SUPABASE_JWT_SECRET` matches your Supabase project
   - Check Settings > API > JWT Settings in Supabase dashboard

2. **CORS Errors**
   - Configure CORS in your MCP server
   - Add allowed origins in Supabase dashboard

3. **Token Validation Fails**
   - Verify tokens are being passed correctly
   - Check token expiration times
   - Ensure scopes match requirements

4. **User Not Found**
   - Confirm user exists in Supabase Auth
   - Check if email is verified (if required)
   - Verify client permissions

### Debug Mode

Enable debug logging:

```bash
export FASTMCP_LOG_LEVEL=DEBUG
export SUPABASE_LOG_LEVEL=debug
```

## Security Best Practices

1. **Secure Keys**: Never commit secrets to version control
2. **Least Privilege**: Use minimal required scopes
3. **Token Rotation**: Implement refresh token rotation
4. **Audit Logs**: Monitor authentication events
5. **Rate Limiting**: Protect against brute force attacks
6. **HTTPS Only**: Always use encrypted connections

## Migration from Basic Auth

To migrate from the basic auth provider:

1. Export existing user data
2. Import users into Supabase Auth
3. Update client applications to use new auth flow
4. Gradually transition users to Supabase login

## Next Steps

- Set up email templates in Supabase
- Configure password policies
- Implement MFA (multi-factor authentication)
- Add social login providers
- Create admin dashboard for user management