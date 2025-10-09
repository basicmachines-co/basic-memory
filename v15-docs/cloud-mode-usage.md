# Using CLI Tools in Cloud Mode

**Status**: DEPRECATED - Use `cloud_mode` instead of `api_url`
**Related**: cloud-authentication.md, cloud-bisync.md

## DEPRECATION NOTICE

This document describes the old `api_url` / `BASIC_MEMORY_API_URL` approach which has been replaced by `cloud_mode` / `BASIC_MEMORY_CLOUD_MODE`.

**New approach:** Use `cloud_mode` config or `BASIC_MEMORY_CLOUD_MODE` environment variable instead.

## Quick Start

### Enable Cloud Mode

```bash
# Set cloud API URL
export BASIC_MEMORY_API_URL=https://api.basicmemory.cloud

# Or in config.json
{
  "api_url": "https://api.basicmemory.cloud"
}

# Authenticate
bm cloud login

# Now CLI tools work against cloud
bm sync --project my-cloud-project
bm status
bm tools search --query "notes"
```

## How It Works

### Local vs Cloud Mode

**Local Mode (default):**
```
CLI Tools → Local ASGI Transport → Local API → Local SQLite + Files
```

**Cloud Mode (with api_url set):**
```
CLI Tools → HTTP Client → Cloud API → Cloud SQLite + Cloud Files
```

### Mode Detection

Basic Memory automatically detects mode:

```python
from basic_memory.config import ConfigManager

config = ConfigManager().config

if config.api_url:
    # Cloud mode: use HTTP client
    client = HTTPClient(base_url=config.api_url)
else:
    # Local mode: use ASGI transport
    client = ASGITransport(app=api_app)
```

## Configuration

### Via Environment Variable

```bash
# Set cloud API URL
export BASIC_MEMORY_API_URL=https://api.basicmemory.cloud

# All commands use cloud
bm sync
bm status
```

### Via Config File

Edit `~/.basic-memory/config.json`:

```json
{
  "api_url": "https://api.basicmemory.cloud",
  "cloud_client_id": "client_abc123",
  "cloud_domain": "https://auth.basicmemory.cloud",
  "cloud_host": "https://api.basicmemory.cloud"
}
```

### Temporary Override

```bash
# One-off cloud command
BASIC_MEMORY_API_URL=https://api.basicmemory.cloud bm sync --project notes

# Back to local mode
bm sync --project notes
```

## Available Commands in Cloud Mode

### Sync Commands

```bash
# Sync cloud project
bm sync --project cloud-project

# Sync specific project
bm sync --project work-notes

# Watch mode (cloud sync)
bm sync --watch --project notes
```

### Status Commands

```bash
# Check cloud sync status
bm status

# Shows cloud project status
```

### MCP Tools

```bash
# Search in cloud project
bm tools search \
  --query "authentication" \
  --project cloud-notes

# Continue conversation from cloud
bm tools continue-conversation \
  --topic "search implementation" \
  --project cloud-notes

# Basic Memory guide
bm tools basic-memory-guide
```

### Project Commands

```bash
# List cloud projects
bm project list

# Add cloud project (if permitted)
bm project add notes /app/data/notes

# Switch default project
bm project default notes
```

## Workflows

### Multi-Device Cloud Workflow

**Device A (Primary):**
```bash
# Configure cloud mode
export BASIC_MEMORY_API_URL=https://api.basicmemory.cloud

# Authenticate
bm cloud login

# Use bisync for primary work
bm cloud bisync-setup
bm sync --watch

# Local files in ~/basic-memory-cloud-sync/
# Synced bidirectionally with cloud
```

**Device B (Secondary):**
```bash
# Configure cloud mode
export BASIC_MEMORY_API_URL=https://api.basicmemory.cloud

# Authenticate
bm cloud login

# Work directly with cloud (no local sync)
bm tools search --query "meeting notes" --project work

# Or mount for file access
bm cloud mount
```

### Development vs Production

**Development (local):**
```bash
# Local mode
unset BASIC_MEMORY_API_URL

# Work with local files
bm sync
bm tools search --query "test"
```

**Production (cloud):**
```bash
# Cloud mode
export BASIC_MEMORY_API_URL=https://api.basicmemory.cloud

# Work with cloud data
bm sync --project production-kb
```

### Testing Cloud Integration

```bash
# Test against staging
export BASIC_MEMORY_API_URL=https://staging-api.basicmemory.cloud
bm cloud login
bm sync --project test-project

# Test against production
export BASIC_MEMORY_API_URL=https://api.basicmemory.cloud
bm cloud login
bm sync --project prod-project
```

## MCP Integration

### Local MCP (default)

```json
// claude_desktop_config.json
{
  "mcpServers": {
    "basic-memory": {
      "command": "uvx",
      "args": ["basic-memory", "mcp"]
    }
  }
}
```

Uses local files via ASGI transport.

### Cloud MCP

```json
// claude_desktop_config.json
{
  "mcpServers": {
    "basic-memory-cloud": {
      "command": "uvx",
      "args": ["basic-memory", "mcp"],
      "env": {
        "BASIC_MEMORY_API_URL": "https://api.basicmemory.cloud"
      }
    }
  }
}
```

Uses cloud API via HTTP client.

### Hybrid Setup (Both)

```json
{
  "mcpServers": {
    "basic-memory-local": {
      "command": "uvx",
      "args": ["basic-memory", "mcp"]
    },
    "basic-memory-cloud": {
      "command": "uvx",
      "args": ["basic-memory", "mcp"],
      "env": {
        "BASIC_MEMORY_API_URL": "https://api.basicmemory.cloud"
      }
    }
  }
}
```

Access both local and cloud from same LLM.

## Authentication

### Cloud Mode Requires Authentication

```bash
# Must login first
bm cloud login

# Then cloud commands work
export BASIC_MEMORY_API_URL=https://api.basicmemory.cloud
bm sync --project notes
```

### Token Management

Cloud mode uses JWT authentication:
- Token stored in `~/.basic-memory/cloud-auth.json`
- Auto-refreshed when expired
- Includes subscription validation

### Authentication Flow

```bash
# 1. Login
bm cloud login
# → Opens browser for OAuth
# → Stores JWT token

# 2. Set cloud mode
export BASIC_MEMORY_API_URL=https://api.basicmemory.cloud

# 3. Use tools (automatically authenticated)
bm sync --project notes
# → Sends Authorization: Bearer {token} header
```

## Project Management in Cloud Mode

### Cloud Projects vs Local Projects

**Local mode:**
- Projects are local directories
- Defined in `~/.basic-memory/config.json`
- Full filesystem access

**Cloud mode:**
- Projects are cloud-managed
- Retrieved from cloud API
- Constrained by BASIC_MEMORY_PROJECT_ROOT on server

### Working with Cloud Projects

```bash
# Enable cloud mode
export BASIC_MEMORY_API_URL=https://api.basicmemory.cloud

# List cloud projects
bm project list
# → Fetches from cloud API

# Sync specific cloud project
bm sync --project cloud-notes
# → Syncs cloud project to cloud database

# Search in cloud project
bm tools search --query "auth" --project cloud-notes
# → Searches cloud-indexed content
```

## Switching Between Local and Cloud

### Switch to Cloud Mode

```bash
# Save local state
bm sync  # Ensure local is synced

# Switch to cloud
export BASIC_MEMORY_API_URL=https://api.basicmemory.cloud
bm cloud login

# Work with cloud
bm sync --project cloud-project
```

### Switch to Local Mode

```bash
# Switch back to local
unset BASIC_MEMORY_API_URL

# Work with local files
bm sync --project local-project
```

### Context-Aware Scripts

```bash
#!/bin/bash

if [ -n "$BASIC_MEMORY_API_URL" ]; then
  echo "Cloud mode: $BASIC_MEMORY_API_URL"
  bm cloud login  # Ensure authenticated
else
  echo "Local mode"
fi

bm sync --project notes
```

## Performance Considerations

### Network Latency

Cloud mode requires network:
- API calls over HTTPS
- Latency depends on connection
- Slower than local ASGI transport

### Caching

MCP in cloud mode has limited caching:
- Results not cached locally
- Each request hits cloud API
- Consider using bisync for frequent access

### Best Practices

1. **Use bisync for primary work:**
   ```bash
   # Sync local copy
   bm cloud bisync

   # Work locally (fast)
   unset BASIC_MEMORY_API_URL
   bm tools search --query "notes"
   ```

2. **Use cloud mode for occasional access:**
   ```bash
   # Quick check from another device
   export BASIC_MEMORY_API_URL=https://api.basicmemory.cloud
   bm tools search --query "meeting" --project work
   ```

3. **Hybrid approach:**
   - Primary device: bisync for local work
   - Other devices: cloud mode for quick access

## Troubleshooting

### Not Authenticated Error

```bash
$ bm sync --project notes
Error: Not authenticated. Please run 'bm cloud login' first.
```

**Solution:**
```bash
bm cloud login
```

### Connection Refused

```bash
$ bm sync
Error: Connection refused: https://api.basicmemory.cloud
```

**Solutions:**
1. Check API URL: `echo $BASIC_MEMORY_API_URL`
2. Verify network: `curl https://api.basicmemory.cloud/health`
3. Check cloud status: https://status.basicmemory.com

### Wrong Projects Listed

**Problem:** `bm project list` shows unexpected projects

**Check mode:**
```bash
# What mode am I in?
echo $BASIC_MEMORY_API_URL

# If set → cloud projects
# If not set → local projects
```

**Solution:** Set/unset API_URL as needed

### Subscription Required

```bash
$ bm sync --project notes
Error: Active subscription required
Subscribe at: https://basicmemory.com/subscribe
```

**Solution:** Subscribe or renew subscription

## Configuration Examples

### Development Setup

```bash
# .bashrc / .zshrc
export BASIC_MEMORY_ENV=dev
export BASIC_MEMORY_LOG_LEVEL=DEBUG

# Local mode by default
# Cloud mode on demand
alias bm-cloud='BASIC_MEMORY_API_URL=https://api.basicmemory.cloud bm'
```

### Production Setup

```bash
# systemd service
[Service]
Environment="BASIC_MEMORY_API_URL=https://api.basicmemory.cloud"
Environment="BASIC_MEMORY_LOG_LEVEL=INFO"
ExecStart=/usr/local/bin/basic-memory serve
```

### Docker Setup

```yaml
# docker-compose.yml
services:
  basic-memory:
    environment:
      BASIC_MEMORY_API_URL: https://api.basicmemory.cloud
      BASIC_MEMORY_LOG_LEVEL: INFO
    volumes:
      - ./cloud-auth:/root/.basic-memory/cloud-auth.json:ro
```

## Security

### API Authentication

- All cloud API calls authenticated with JWT
- Token in Authorization header
- Subscription validated per request

### Network Security

- All traffic over HTTPS/TLS
- No credentials in URLs or logs
- Tokens stored securely (mode 600)

### Multi-Tenant Isolation

- Tenant ID from JWT claims
- Each request isolated to tenant
- Cannot access other tenants' data

## See Also

- `cloud-authentication.md` - Authentication setup
- `cloud-bisync.md` - Bidirectional sync workflow
- `cloud-mount.md` - Direct cloud file access
- MCP server configuration documentation
