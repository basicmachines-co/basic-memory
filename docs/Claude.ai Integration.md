# Claude.ai Integration Guide

This guide explains how to connect Basic Memory to Claude.ai, enabling Claude to read and write to your personal knowledge base.

## Overview

When connected to Claude.ai, Basic Memory provides:
- Persistent memory across conversations
- Knowledge graph navigation
- Note-taking and search capabilities
- File organization and management

## Prerequisites

1. Basic Memory MCP server with OAuth enabled
2. Public HTTPS URL (or tunneling service for testing)
3. Claude.ai account (Free, Pro, or Enterprise)

## Quick Start (Testing)

### 1. Start MCP Server with OAuth

```bash
# Enable OAuth with basic provider
export FASTMCP_AUTH_ENABLED=true
export FASTMCP_AUTH_PROVIDER=basic

# Start server on all interfaces
basic-memory mcp --transport streamable-http --host 0.0.0.0 --port 8000
```

### 2. Make Server Accessible

For testing, use ngrok:

```bash
# Install ngrok
brew install ngrok  # macOS
# or download from https://ngrok.com

# Create tunnel
ngrok http 8000
```

Note the HTTPS URL (e.g., `https://abc123.ngrok.io`)

### 3. Register OAuth Client

```bash
# Register a client for Claude
basic-memory auth register-client --client-id claude-ai

# Save the credentials!
# Client ID: claude-ai
# Client Secret: xxx...
```

### 4. Connect in Claude.ai

1. Go to Claude.ai → Settings → Integrations
2. Click "Add More"
3. Enter your server URL: `https://abc123.ngrok.io/mcp`
4. Click "Connect"
5. Authorize the connection

### 5. Use in Conversations

- Click the tools icon (🔧) in the chat
- Select "Basic Memory"
- Try commands like:
  - "Create a note about our meeting"
  - "Search for project ideas"
  - "Show recent notes"

## Production Setup

### 1. Deploy with Supabase Auth

```bash
# .env file
FASTMCP_AUTH_ENABLED=true
FASTMCP_AUTH_PROVIDER=supabase
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_ANON_KEY=your-anon-key
SUPABASE_SERVICE_KEY=your-service-key
```

### 2. Deploy to Cloud

Options for deployment:

#### Vercel
```json
// vercel.json
{
  "functions": {
    "api/mcp.py": {
      "runtime": "python3.9"
    }
  }
}
```

#### Railway
```bash
# Install Railway CLI
brew install railway

# Deploy
railway init
railway up
```

#### Docker
```dockerfile
FROM python:3.12
WORKDIR /app
COPY . .
RUN pip install -e .
CMD ["basic-memory", "mcp", "--transport", "streamable-http"]
```

### 3. Configure for Organization

For Claude.ai Enterprise:

1. **Admin Setup**:
   - Go to Organizational Settings
   - Navigate to Integrations
   - Add MCP server URL for all users
   - Configure allowed scopes

2. **User Permissions**:
   - Users connect individually
   - Each user has their own auth token
   - Scopes determine access level

## Security Best Practices

### 1. Use HTTPS
- Required for OAuth
- Encrypt all data in transit
- Use proper SSL certificates

### 2. Implement Scopes
```bash
# Configure required scopes
FASTMCP_AUTH_REQUIRED_SCOPES=read,write

# User-specific scopes
read: Can search and read notes
write: Can create and update notes
admin: Can manage all data
```

### 3. Token Security
- Short-lived access tokens (1 hour)
- Refresh token rotation
- Secure token storage

### 4. Rate Limiting
```python
# In your MCP server
from fastapi import HTTPException
from slowapi import Limiter

limiter = Limiter(key_func=get_remote_address)

@app.get("/mcp")
@limiter.limit("100/minute")
async def mcp_endpoint():
    # Handle MCP requests
```

## Advanced Features

### 1. Custom Tools

Create specialized tools for Claude:

```python
@mcp.tool()
async def analyze_notes(topic: str) -> str:
    """Analyze all notes on a specific topic."""
    # Search and analyze implementation
    return analysis
```

### 2. Context Preservation

Use memory:// URLs to maintain context:

```python
@mcp.tool()
async def continue_conversation(memory_url: str) -> str:
    """Continue from a previous conversation."""
    context = await build_context(memory_url)
    return context
```

### 3. Multi-User Support

With Supabase, each user has isolated data:

```sql
-- Row-level security
CREATE POLICY "Users see own notes"
ON notes FOR SELECT
USING (auth.uid() = user_id);
```

## Troubleshooting

### Connection Issues

1. **"Failed to connect"**
   - Verify server is running
   - Check HTTPS is working
   - Confirm OAuth is enabled

2. **"Authorization failed"**
   - Check client credentials
   - Verify redirect URLs
   - Review OAuth logs

3. **"No tools available"**
   - Ensure MCP tools are registered
   - Check required scopes
   - Verify transport type

### Debug Mode

Enable detailed logging:

```bash
# Server side
export FASTMCP_LOG_LEVEL=DEBUG
export LOGURU_LEVEL=DEBUG

# Check logs
tail -f logs/mcp.log
```

### Test Connection

```bash
# Test OAuth flow
curl https://your-server.com/mcp/.well-known/oauth-authorization-server

# Should return OAuth metadata
{
  "issuer": "https://your-server.com",
  "authorization_endpoint": "https://your-server.com/auth/authorize",
  ...
}
```

## Best Practices

1. **Regular Backups**
   - Export your knowledge base
   - Use version control
   - Multiple storage locations

2. **Access Control**
   - Principle of least privilege
   - Regular token rotation
   - Audit access logs

3. **Performance**
   - Index frequently searched fields
   - Optimize large knowledge bases
   - Use caching where appropriate

4. **User Experience**
   - Clear tool descriptions
   - Helpful error messages
   - Quick response times

## Examples

### Creating Notes

```
User: Create a note about the meeting with the product team

Claude: I'll create a note about your meeting with the product team.

[Uses write_note tool]

Note created: "Meeting with Product Team - 2024-01-15"
Location: Work/Meetings/

I've documented the meeting notes. The note includes the date, attendees, and key discussion points.
```

### Searching Knowledge

```
User: What did we discuss about the API redesign?

Claude: Let me search for information about the API redesign.

[Uses search_notes tool]

I found 3 relevant notes about the API redesign:

1. "API Redesign Proposal" (2024-01-10)
   - RESTful architecture
   - Version 2.0 specifications
   - Migration timeline

2. "Technical Review: API Changes" (2024-01-12)
   - Breaking changes documented
   - Backwards compatibility plan

3. "Meeting: API Implementation" (2024-01-14)
   - Team assignments
   - Q1 deliverables
```

## Next Steps

1. Set up production deployment
2. Configure organizational access
3. Create custom tools for your workflow
4. Implement advanced security features
5. Monitor usage and performance

## Resources

- [Basic Memory Documentation](../README.md)
- [OAuth Setup Guide](OAuth%20Authentication.md)
- [MCP Specification](https://modelcontextprotocol.io)
- [Claude.ai Help Center](https://support.anthropic.com)