---
title: 'SPEC-9: Signed Header Tenant Information'
type: spec
permalink: specs/spec-9-signed-header-tenant-information
tags:
- authentication
- tenant-isolation
- proxy
- security
- mcp
---

# SPEC-9: Signed Header Tenant Information

## Why

WorkOS JWT templates don't work with MCP's dynamic client registration requirement, preventing us from getting tenant information directly in JWT tokens. We need an alternative secure method to pass tenant context from the Cloud Proxy Service to tenant instances.

**Problem Context:**
- MCP spec requires dynamic client registration
- WorkOS JWT templates only apply to statically configured clients
- Without tenant information, we can't properly route requests or isolate tenant data
- Current JWT tokens only contain standard OIDC claims (sub, email, etc.)

**Affected Areas:**
- Cloud Proxy Service (`apps/cloud`) - request forwarding
- Tenant API instances (`apps/api`) - tenant context validation
- MCP Gateway (`apps/mcp`) - authentication flow
- Overall tenant isolation security model

## What

Implement HMAC-signed headers that the Cloud Proxy Service adds when forwarding requests to tenant instances. This provides secure, tamper-proof tenant information without relying on JWT custom claims.

**Components:**
- Header signing utility in Cloud Proxy Service
- Header validation middleware in Tenant API instances
- Shared secret configuration across services
- Fallback mechanisms for development and error cases

## How (High Level)

### 1. Header Format
Add these signed headers to all proxied requests:
```
X-BM-Tenant-ID: {tenant_id}
X-BM-Timestamp: {unix_timestamp}
X-BM-Signature: {hmac_sha256_signature}
```

### 2. Signature Algorithm
```python
# Canonical message format
message = f"{tenant_id}:{timestamp}"

# HMAC-SHA256 signature
signature = hmac.new(
    key=shared_secret.encode('utf-8'),
    msg=message.encode('utf-8'),
    digestmod=hashlib.sha256
).hexdigest()
```

### 3. Implementation Flow

#### Cloud Proxy Service (`apps/cloud`)
1. Extract `tenant_id` from authenticated user profile
2. Generate timestamp and canonical message
3. Sign message with shared secret
4. Add headers to request before forwarding to tenant instance

#### Tenant API Instances (`apps/api`)
1. Middleware validates headers on all incoming requests
2. Extract tenant_id, timestamp from headers
3. Verify timestamp is within acceptable window (5 minutes)
4. Recompute signature and compare in constant time
5. If valid, make tenant context available to Basic Memory tools

### 4. Security Properties
- **Authenticity**: Only services with shared secret can create valid signatures
- **Integrity**: Header tampering invalidates signature
- **Replay Protection**: Timestamp prevents reuse of old signatures
- **Non-repudiation**: Each request is cryptographically tied to specific tenant

### 5. Configuration
```bash
# Shared across Cloud Proxy and Tenant instances
BM_TENANT_HEADER_SECRET=randomly-generated-256-bit-secret

# Tenant API configuration
BM_TENANT_HEADER_VALIDATION=true  # true (production) | false (dev only)
```

## How to Evaluate

### Unit Tests
- [ ] Header signing utility generates correct signatures
- [ ] Header validation correctly accepts/rejects signatures
- [ ] Timestamp validation within acceptable windows
- [ ] Constant-time signature comparison prevents timing attacks

### Integration Tests
- [ ] End-to-end request flow from MCP client → proxy → tenant
- [ ] Tenant isolation verified with signed headers
- [ ] Error handling for missing/invalid headers
- [ ] Disabled validation in development environment

### Security Validation
- [ ] Shared secret rotation procedure
- [ ] Header tampering detection
- [ ] Clock skew tolerance testing
- [ ] Performance impact measurement

### Production Readiness
- [ ] Logging and monitoring of header validation
- [ ] Graceful degradation for header validation failures
- [ ] Documentation for secret management
- [ ] Deployment configuration templates

## Implementation Notes

### Shared Secret Management
- Generate cryptographically secure 256-bit secret
- Same secret deployed to Cloud Proxy and all Tenant instances
- Consider secret rotation strategy for production

### Error Handling
```python
# Strict mode (production)
if not validate_headers(request):
    raise HTTPException(status_code=401, detail="Invalid tenant headers")

# Fallback mode (development)
if not validate_headers(request):
    logger.warning("Invalid headers, falling back to default tenant")
    tenant_id = "default"
```

### Performance Considerations
- HMAC-SHA256 computation is fast (~microseconds)
- Headers add ~200 bytes to each request
- Validation happens once per request in middleware

## Benefits

✅ **Works with MCP dynamic client registration** - No dependency on JWT custom claims
✅ **Simple and reliable** - Standard HMAC signature approach
✅ **Secure by design** - Cryptographic authenticity and integrity
✅ **Infrastructure controlled** - No external service dependencies
✅ **Easy to implement** - Clear signature algorithm and validation

## Trade-offs

⚠️ **Shared secret management** - Need secure distribution and rotation
⚠️ **Clock synchronization** - Timestamp validation requires reasonably synced clocks
⚠️ **Header visibility** - Headers visible in logs (tenant_id not sensitive)
⚠️ **Additional complexity** - More moving parts in proxy forwarding

## Implementation Tasks

### Cloud Service (Header Signing)
- [ ] Create `utils/header_signing.py` with HMAC-SHA256 signing function
- [ ] Add `bm_tenant_header_secret` to Cloud service configuration
- [ ] Update `ProxyService.forward_request()` to call signing utility
- [ ] Add signed headers (X-BM-Tenant-ID, X-BM-Timestamp, X-BM-Signature)

### Tenant API (Header Validation)
- [ ] Create `utils/header_validation.py` with signature verification
- [ ] Add `bm_tenant_header_secret` to API service configuration
- [ ] Create `TenantHeaderValidationMiddleware` class
- [ ] Add middleware to FastAPI app (before other middleware)
- [ ] Skip validation for `/health` endpoint
- [ ] Store validated tenant_id in request.state

### Testing
- [ ] Unit test for header signing utility
- [ ] Unit test for header validation utility
- [ ] Integration test for proxy → tenant flow
- [ ] Test invalid/missing header handling
- [ ] Test timestamp window validation
- [ ] Test signature tampering detection

### Configuration & Deployment
- [ ] Update `.env.example` with BM_TENANT_HEADER_SECRET
- [ ] Generate secure 256-bit secret for production
- [ ] Update Fly.io secrets for both services
- [ ] Document secret rotation procedure

## Status

- [x] **Specification Complete** - Design finalized and documented
- [ ] **Implementation Started** - Header signing utility development
- [ ] **Cloud Proxy Updated** - ProxyService adds signed headers
- [ ] **Tenant Validation Added** - Middleware validates headers
- [ ] **Testing Complete** - All validation criteria met
- [ ] **Production Deployed** - Live with tenant isolation via headers