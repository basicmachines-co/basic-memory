# Cloud Authentication (SPEC-13)

**Status**: New Feature
**PR**: #327
**Requires**: Active Basic Memory subscription

## What's New

v0.15.0 introduces **JWT-based cloud authentication** with automatic subscription validation. This enables secure access to Basic Memory Cloud features including bidirectional sync, cloud storage, and multi-device access.

## Quick Start

### Login to Cloud

```bash
# Authenticate with Basic Memory Cloud
bm cloud login

# Opens browser for OAuth flow
# Validates subscription status
# Stores JWT token locally
```

### Check Authentication Status

```bash
# View current authentication status
bm cloud status
```

### Logout

```bash
# Clear authentication session
bm cloud logout
```

## How It Works

### Authentication Flow

1. **Initiate Login**: `bm cloud login`
2. **Browser Opens**: OAuth 2.1 flow with PKCE
3. **Authorize**: Login with your Basic Memory account
4. **Subscription Check**: Validates active subscription
5. **Token Storage**: JWT stored in `~/.basic-memory/cloud-auth.json`
6. **Auto-Refresh**: Token automatically refreshed when needed

### Subscription Validation

All cloud commands validate your subscription status:

**Active Subscription:**
```bash
$ bm cloud sync
✓ Syncing with cloud...
```

**No Active Subscription:**
```bash
$ bm cloud sync
✗ Active subscription required
Subscribe at: https://basicmemory.com/subscribe
```

## Authentication Commands

### bm cloud login

Authenticate with Basic Memory Cloud.

```bash
# Basic login
bm cloud login

# Login opens browser automatically
# Redirects to: https://eloquent-lotus-05.authkit.app/...
```

**What happens:**
- Opens OAuth authorization in browser
- Handles PKCE challenge/response
- Validates subscription
- Stores JWT token
- Displays success message

**Error cases:**
- No subscription: Shows subscribe URL
- Network error: Retries with exponential backoff
- Invalid credentials: Prompts to try again

### bm cloud logout

Clear authentication session.

```bash
bm cloud logout
```

**What happens:**
- Removes `~/.basic-memory/cloud-auth.json`
- Clears cached credentials
- Requires re-authentication for cloud commands

### bm cloud status

View authentication and sync status.

```bash
bm cloud status
```

**Shows:**
- Authentication status (logged in/out)
- Subscription status (active/expired)
- Last sync time
- Cloud project count
- Tenant information

## Token Management

### Automatic Token Refresh

The CLI automatically handles token refresh:

```python
# Internal - happens automatically
async def get_authenticated_headers():
    # Checks token expiration
    # Refreshes if needed
    # Returns valid Bearer token
    return {"Authorization": f"Bearer {token}"}
```

### Token Storage

Location: `~/.basic-memory/cloud-auth.json`

```json
{
  "access_token": "eyJ0eXAiOiJKV1QiLCJhbGc...",
  "refresh_token": "eyJ0eXAiOiJKV1QiLCJhbGc...",
  "expires_at": 1234567890,
  "tenant_id": "org_abc123"
}
```

**Security:**
- File permissions: 600 (user read/write only)
- Tokens expire after 1 hour
- Refresh tokens valid for 30 days
- Never commit this file to git

### Manual Token Revocation

To revoke access:
1. `bm cloud logout` (clears local token)
2. Visit account settings to revoke all sessions

## Subscription Management

### Check Subscription Status

```bash
# View current subscription
bm cloud status

# Shows:
# - Subscription tier
# - Expiration date
# - Features enabled
```

### Subscribe

If you don't have a subscription:

```bash
# Displays subscribe URL
bm cloud login
# > Active subscription required
# > Subscribe at: https://basicmemory.com/subscribe
```

### Subscription Tiers

| Feature | Free | Pro | Team |
|---------|------|-----|------|
| Cloud Authentication | ✓ | ✓ | ✓ |
| Cloud Sync | - | ✓ | ✓ |
| Cloud Storage | - | 10GB | 100GB |
| Multi-device | - | ✓ | ✓ |
| API Access | - | ✓ | ✓ |

## Using Authenticated APIs

### In CLI Commands

Authentication is automatic for all cloud commands:

```bash
# These all use stored JWT automatically
bm cloud sync
bm cloud mount
bm cloud check
bm cloud bisync
```

### In Custom Scripts

```python
from basic_memory.cli.auth import CLIAuth

# Get authenticated headers
client_id, domain, _ = get_cloud_config()
auth = CLIAuth(client_id=client_id, authkit_domain=domain)
token = await auth.get_valid_token()

headers = {"Authorization": f"Bearer {token}"}

# Use with httpx or requests
import httpx
async with httpx.AsyncClient() as client:
    response = await client.get(
        "https://api.basicmemory.cloud/tenant/projects",
        headers=headers
    )
```

### Error Handling

```python
from basic_memory.cli.commands.cloud.api_client import (
    CloudAPIError,
    SubscriptionRequiredError
)

try:
    response = await make_api_request("GET", url)
except SubscriptionRequiredError as e:
    print(f"Subscription required: {e.message}")
    print(f"Subscribe at: {e.subscribe_url}")
except CloudAPIError as e:
    print(f"API error: {e.status_code} - {e.detail}")
```

## OAuth Configuration

### Default Settings

```python
# From config.py
cloud_client_id = "client_01K6KWQPW6J1M8VV7R3TZP5A6M"
cloud_domain = "https://eloquent-lotus-05.authkit.app"
cloud_host = "https://api.basicmemory.cloud"
```

### Custom Configuration

Override via environment variables:

```bash
export BASIC_MEMORY_CLOUD_CLIENT_ID="your_client_id"
export BASIC_MEMORY_CLOUD_DOMAIN="https://your-authkit.app"
export BASIC_MEMORY_CLOUD_HOST="https://your-api.example.com"

bm cloud login
```

Or in `~/.basic-memory/config.json`:

```json
{
  "cloud_client_id": "your_client_id",
  "cloud_domain": "https://your-authkit.app",
  "cloud_host": "https://your-api.example.com"
}
```

## Troubleshooting

### "Not authenticated" Error

```bash
$ bm cloud sync
[red]Not authenticated. Please run 'bm cloud login' first.[/red]
```

**Solution**: Run `bm cloud login`

### Token Expired

```bash
$ bm cloud status
Token expired, refreshing...
✓ Authenticated
```

**Automatic**: Token refresh happens automatically

### Subscription Expired

```bash
$ bm cloud sync
Active subscription required
Subscribe at: https://basicmemory.com/subscribe
```

**Solution**: Renew subscription at provided URL

### Browser Not Opening

```bash
$ bm cloud login
# If browser doesn't open automatically:
# Visit this URL: https://eloquent-lotus-05.authkit.app/...
```

**Manual**: Copy/paste URL into browser

### Network Issues

```bash
$ bm cloud login
Connection error, retrying in 2s...
Connection error, retrying in 4s...
```

**Automatic**: Exponential backoff with retries

## Security Best Practices

1. **Never share tokens**: Keep `cloud-auth.json` private
2. **Use logout**: Always logout on shared machines
3. **Monitor sessions**: Check `bm cloud status` regularly
4. **Revoke access**: Use account settings to revoke compromised tokens
5. **Use HTTPS only**: Cloud commands enforce HTTPS

## Related Commands

- `bm cloud sync` - Bidirectional cloud sync (see `cloud-bisync.md`)
- `bm cloud mount` - Mount cloud storage (see `cloud-mount.md`)
- `bm cloud check` - Verify cloud integrity
- `bm cloud status` - View authentication and sync status

## Technical Details

### JWT Claims

```json
{
  "sub": "user_abc123",
  "org_id": "org_xyz789",
  "tenant_id": "org_xyz789",
  "subscription_status": "active",
  "subscription_tier": "pro",
  "exp": 1234567890,
  "iat": 1234564290
}
```

### API Integration

The cloud API validates JWT on every request:

```python
# Middleware validates JWT and extracts tenant context
@app.middleware("http")
async def tenant_middleware(request: Request, call_next):
    token = request.headers.get("Authorization")
    claims = verify_jwt(token)
    request.state.tenant_id = claims["tenant_id"]
    request.state.subscription = claims["subscription_status"]
    # ...
```

## See Also

- SPEC-13: CLI Authentication with Subscription Validation
- `cloud-bisync.md` - Using authenticated sync
- `cloud-mode-usage.md` - Working with cloud APIs
