# Authentication with Stytch + Supabase

This document outlines the authentication architecture for Basic Memory Cloud using Stytch as the OAuth identity provider and Supabase for user management and database operations.

## Overview

Basic Memory Cloud implements a multi-layered authentication system:

1. **Web Authentication**: Supabase Auth with magic link email verification (existing)
2. **OAuth Provider**: Stytch OAuth 2.1 server for MCP client authentication (new)
3. **JWT Validation**: Basic Memory instances validate JWTs with tenant claims
4. **Zero Trust**: Each tenant instance independently validates authentication

## Current State vs Target State

### Current Implementation (To Be Replaced)

**Custom OAuth Provider** (in basic-memory-cloud):
- `/apps/web/pages/auth/login.vue` - OAuth flow detection and magic link authentication
- `/apps/web/pages/auth/oauth-callback.vue` - Custom authorization code generation
- `/apps/web/server/api/validate-auth-code.post.ts` - Custom auth code validation
- Session/localStorage based auth code management

**Basic Memory Auth** (in basic-memory):
- Full OAuth 2.1 identity provider implementation
- Custom user management and token generation
- Direct MCP client authentication

### Target Implementation (Stytch + Supabase)

**Stytch OAuth Provider**:
- Replace custom OAuth implementation with Stytch OAuth 2.1 endpoints
- Handle authorization codes, token exchange, and PKCE validation
- Integrate with existing Supabase user records

**Simplified Basic Memory**:
- Remove OAuth identity provider code
- Keep only JWT validation using FastMCP BearerAuth
- Validate tokens issued by Stytch with tenant claims

## Architecture Flow

```
Claude Desktop ──OAuth 2.1──▶ Stytch ──JWT──▶ Basic Memory Instance
                              (Identity)     (JWT Validator)
                                  │
                              Supabase
                              (User DB)
```

### Authentication Flow

1. **MCP Client Authorization**: Claude Desktop initiates OAuth flow with Stytch
2. **User Authentication**: User authenticates via existing Supabase magic link flow
3. **Stytch Integration**: Link Supabase user to Stytch OAuth session
4. **Token Generation**: Stytch issues JWT with tenant claims from Supabase
5. **MCP Access**: Basic Memory validates JWT and authorizes tenant-specific operations

## Implementation Plan

### Phase 1: Stytch Setup

1. **Stytch Account Configuration**:
   - Create Stytch project for Basic Memory Cloud
   - Configure OAuth 2.1 endpoints and PKCE support
   - Set up redirect URIs for MCP client flow

https://stytch.com/docs/guides/connected-apps/mcp-servers

b2b
https://stytch.com/docs/b2b/guides/connected-apps/mcp-servers

example:
https://github.com/stytchauth/mcp-stytch-b2b-okr-manager

Project ID
project-test-c7aa87d9-d9b3-4fe0-b582-62d062ab0cd0
Project Domain
handy-pint-3688.customers.stytch.dev
Workspace ID
workspace-prod-90696553-a386-4144-859a-78c802731ec1

authorization url: http://localhost:3000/auth/stytch-authorize

https://stytch.com/dashboard/settings/management-api
workspace management key: 
workspace-key-prod-b7e2a740-a478-4dda-a78b-65138c449ab1

secret:
7o0p1UI7hFHJxOFH4yE8SP0tLLG-jf53eulBzwiL7MGVfR_o

org:
Basic Machines
https://stytch.com/dashboard/organizations?organization_id=organization-test-b0db136f-97cd-44a8-bba7-71a58b64dbc0&project_id=project-test-c7aa87d9-d9b3-4fe0-b582-62d062ab0cd0


2. **OAuth Application Registration**:
   - Register Claude Desktop as OAuth client
   - Configure scopes: `basic_memory:read`, `basic_memory:write`
   - Set up dynamic client registration for future MCP clients
   
### Phase 2: Supabase Integration

1. **User Linking Strategy**:
   - Map Supabase user IDs to Stytch user identities
   - Add Stytch user ID field to Supabase user profiles
   - Implement user linking during first OAuth flow

2. **Tenant Claims**:
   - Store tenant information in Supabase user metadata
   - Configure Stytch to include tenant claims in JWTs
   - Implement tenant-based access control

### Phase 3: Basic Memory Simplification

1. **Remove OAuth Provider Code**:
   - Remove `/src/basic_memory/mcp/auth_provider.py` OAuth implementation
   - Keep JWT validation in `/src/basic_memory/mcp/server.py`
   - Simplify to FastMCP BearerAuth with Stytch JWKS validation

2. **Update MCP Server Configuration**:
   ```python
   # Simplified auth configuration
   FASTMCP_AUTH_ENABLED=true
   FASTMCP_AUTH_PROVIDER=cloud
   FASTMCP_AUTH_JWKS_URI=https://api.stytch.com/v1/sessions/jwks/{project_id}
   FASTMCP_AUTH_ISSUER=https://api.stytch.com
   ```

### Phase 4: Cloud Integration

1. **Replace Custom OAuth Pages**:
   - Update `/apps/web/pages/auth/login.vue` to redirect to Stytch OAuth
   - Remove `/apps/web/pages/auth/oauth-callback.vue` custom implementation
   - Remove `/apps/web/server/api/validate-auth-code.post.ts` endpoint

2. **Stytch Callback Handling**:
   - Implement Stytch OAuth callback in web app
   - Link Stytch session to Supabase user
   - Store tenant claims for future JWT generation

## Technical Specifications

### JWT Token Structure

```json
{
  "iss": "https://api.stytch.com",
  "sub": "user-uuid-from-stytch",
  "aud": "basic-memory-mcp",
  "exp": 1735689600,
  "iat": 1735686000,
  "custom_claims": {
    "tenant_id": "tenant-uuid-from-supabase",
    "supabase_user_id": "user-uuid-from-supabase",
    "scopes": ["basic_memory:read", "basic_memory:write"]
  }
}
```

### Stytch Configuration

**OAuth 2.1 Settings**:
- Authorization endpoint: `https://api.stytch.com/v1/oauth/authorize`
- Token endpoint: `https://api.stytch.com/v1/oauth/token`
- JWKS URI: `https://api.stytch.com/v1/sessions/jwks/{project_id}`
- Supported flows: Authorization Code with PKCE
- Token lifetime: 1 hour (configurable)

**Scopes**:
- `basic_memory:read` - Read access to knowledge base
- `basic_memory:write` - Write access to knowledge base
- `profile` - Basic user profile information

### Migration Strategy

1. **Parallel Implementation**: Deploy Stytch alongside existing custom OAuth
2. **Feature Flag**: Use environment variable to switch between implementations
3. **User Migration**: Gradually migrate existing users to Stytch OAuth
4. **Cleanup**: Remove custom OAuth code after successful migration

## Security Considerations

### Token Validation

- **JWKS Rotation**: Automatically fetch updated signing keys from Stytch
- **Tenant Isolation**: Validate tenant claims match requested resources
- **Scope Validation**: Enforce read/write permissions based on token scopes
- **Token Expiry**: Implement proper token refresh flow

### Zero Trust Architecture

- **Independent Validation**: Each Basic Memory instance validates tokens independently
- **No Shared State**: No dependency on central authentication state
- **Fail Secure**: Invalid or missing tokens result in access denial

## Benefits of Stytch Integration

1. **Reduced Complexity**: Eliminate custom OAuth implementation and maintenance
2. **Enterprise Features**: Built-in security, compliance, and monitoring
3. **Scalability**: Handle OAuth flows for thousands of concurrent users
4. **Developer Experience**: Well-documented APIs and SDKs
5. **Security**: Professional-grade OAuth 2.1 implementation with PKCE
6. **Integration**: Native support for JWT generation with custom claims

## Basic Memory Auth Integration Requirements

### 1. Remove Custom OAuth Identity Provider
- Basic Memory currently acts as a full OAuth 2.1 identity provider
- **Remove**: All OAuth server code (authorization endpoints, token generation, user management)
- **Keep**: Only JWT validation capabilities

### 2. Implement JWT Validation Only
- Configure FastMCP BearerAuthProvider to validate JWTs from basic-memory-cloud
- Point to basic-memory-cloud's JWKS endpoint for token verification
- Validate tenant claims to ensure users only access their own data

### 3. OAuth Proxy Endpoints
- Add discovery endpoints that proxy OAuth metadata to basic-memory-cloud
- Route MCP Inspector OAuth registration requests to basic-memory-cloud
- Maintain local MCP discovery endpoint with OAuth authentication metadata

### 4. Configuration
```python
# Target auth setup - validate tokens from basic-memory-cloud
auth_provider = BearerAuthProvider(
    jwks_uri="http://localhost:3000/.well-known/jwks.json",  # basic-memory-cloud
    issuer="http://localhost:3000",  # basic-memory-cloud 
    audience="basic-memory-mcp"
)
```

### 5. Authentication Flow
1. MCP Inspector discovers Basic Memory via `/mcp` endpoint
2. OAuth registration/authorization proxied to basic-memory-cloud  
3. basic-memory-cloud handles Stytch B2B OAuth + Supabase user linking
4. basic-memory-cloud issues JWT with tenant claims
5. Basic Memory validates JWT and authorizes tenant-specific operations

**Key Insight**: Basic Memory becomes a **JWT validator** instead of an **OAuth identity provider**, with basic-memory-cloud handling all OAuth complexity via Stytch.

## Next Steps

1. **Simplify Basic Memory**: Remove OAuth identity provider code, keep only JWT validation
2. **Add OAuth Proxy**: Implement discovery and registration endpoints that proxy to basic-memory-cloud
3. **Configure FastMCP**: Setup BearerAuthProvider to validate basic-memory-cloud JWTs
4. **Test Integration**: Verify MCP Inspector can complete full OAuth flow via proxy
5. **Deploy**: Update both basic-memory and basic-memory-cloud with new auth architecture

## Related Documentation

- [Stytch OAuth 2.1 Documentation](https://stytch.com/docs/oauth)
- [Supabase Auth Integration](https://supabase.com/docs/guides/auth)
- [MCP Authentication Specification](https://modelcontextprotocol.io/specification/2025-06-18/authentication)
- [FastMCP BearerAuth Configuration](https://github.com/jlowin/fastmcp#authentication)