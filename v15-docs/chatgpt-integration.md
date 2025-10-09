# ChatGPT MCP Integration

**Status**: New Feature
**PR**: #305
**File**: `mcp/tools/chatgpt_tools.py`
**Mode**: Remote MCP only

## What's New

v0.15.0 introduces ChatGPT-specific MCP tools that expose Basic Memory's search and fetch functionality using OpenAI's required tool schema and response format.

## Requirements

### ChatGPT Plus/Pro Subscription

**Required:** ChatGPT Plus or Pro subscription
- Free tier does NOT support MCP
- Pro tier includes MCP support

**Pricing:**
- ChatGPT Plus: $20/month
- ChatGPT Pro: $200/month (includes advanced features)

### Developer Mode

**Required:** ChatGPT Developer Mode
- Access to MCP server configuration
- Ability to add custom MCP servers

**Enable Developer Mode:**
1. Open ChatGPT settings
2. Navigate to "Advanced" or "Developer" settings
3. Enable "Developer Mode"
4. Restart ChatGPT

### Remote MCP Configuration

**Important:** ChatGPT only supports **remote MCP servers**
- Cannot use local MCP (like Claude Desktop)
- Requires publicly accessible MCP server
- Basic Memory must be deployed and reachable

## How It Works

### ChatGPT-Specific Format

OpenAI requires MCP responses in a specific format:

**Standard MCP (Claude, etc.):**
```json
{
  "results": [...],
  "total": 10
}
```

**ChatGPT MCP:**
```json
[
  {
    "type": "text",
    "text": "{\"results\": [...], \"total\": 10}"
  }
]
```

**Key difference:** ChatGPT expects content wrapped in `[{"type": "text", "text": "..."}]` array

### Adapter Architecture

```
ChatGPT Request
    ↓
ChatGPT MCP Tools (chatgpt_tools.py)
    ↓
Standard Basic Memory Tools (search_notes, read_note)
    ↓
Format for ChatGPT
    ↓
[{"type": "text", "text": "{...json...}"}]
    ↓
ChatGPT Response
```

## Available Tools

### 1. search

Search across the knowledge base.

**Tool Definition:**
```json
{
  "name": "search",
  "description": "Search for content across the knowledge base",
  "inputSchema": {
    "type": "object",
    "properties": {
      "query": {
        "type": "string",
        "description": "Search query"
      }
    },
    "required": ["query"]
  }
}
```

**Example Request:**
```json
{
  "query": "authentication system"
}
```

**Example Response:**
```json
[
  {
    "type": "text",
    "text": "{\"results\": [{\"id\": \"auth-design\", \"title\": \"Authentication Design\", \"url\": \"auth-design\"}], \"total_count\": 1, \"query\": \"authentication system\"}"
  }
]
```

**Parsed JSON:**
```json
{
  "results": [
    {
      "id": "auth-design",
      "title": "Authentication Design",
      "url": "auth-design"
    }
  ],
  "total_count": 1,
  "query": "authentication system"
}
```

### 2. fetch

Fetch full contents of a document.

**Tool Definition:**
```json
{
  "name": "fetch",
  "description": "Fetch the full contents of a search result document",
  "inputSchema": {
    "type": "object",
    "properties": {
      "id": {
        "type": "string",
        "description": "Document identifier"
      }
    },
    "required": ["id"]
  }
}
```

**Example Request:**
```json
{
  "id": "auth-design"
}
```

**Example Response:**
```json
[
  {
    "type": "text",
    "text": "{\"id\": \"auth-design\", \"title\": \"Authentication Design\", \"text\": \"# Authentication Design\\n\\n...\", \"url\": \"auth-design\", \"metadata\": {\"format\": \"markdown\"}}"
  }
]
```

**Parsed JSON:**
```json
{
  "id": "auth-design",
  "title": "Authentication Design",
  "text": "# Authentication Design\n\n...",
  "url": "auth-design",
  "metadata": {
    "format": "markdown"
  }
}
```

## Configuration

### Remote MCP Server Setup

**Option 1: Deploy to Cloud**

```bash
# Deploy Basic Memory to cloud provider
# Ensure publicly accessible

# Example: Deploy to Fly.io
fly deploy

# Get URL
export MCP_SERVER_URL=https://your-app.fly.dev
```

**Option 2: Use ngrok for Testing**

```bash
# Start Basic Memory locally
bm mcp --port 8000

# Expose via ngrok
ngrok http 8000

# Get public URL
# → https://abc123.ngrok.io
```

### ChatGPT MCP Configuration

**In ChatGPT Developer Mode:**

```json
{
  "mcpServers": {
    "basic-memory": {
      "url": "https://your-server.com/mcp",
      "apiKey": "your-api-key-if-needed"
    }
  }
}
```

**Environment Variables (if using auth):**
```bash
export BASIC_MEMORY_API_KEY=your-secret-key
```

## Usage Examples

### Search Workflow

**User asks ChatGPT:**
> "Search my knowledge base for authentication notes"

**ChatGPT internally calls:**
```json
{
  "tool": "search",
  "arguments": {
    "query": "authentication notes"
  }
}
```

**Basic Memory responds:**
```json
[{
  "type": "text",
  "text": "{\"results\": [{\"id\": \"auth-design\", \"title\": \"Auth Design\", \"url\": \"auth-design\"}, {\"id\": \"oauth-setup\", \"title\": \"OAuth Setup\", \"url\": \"oauth-setup\"}], \"total_count\": 2, \"query\": \"authentication notes\"}"
}]
```

**ChatGPT displays:**
> I found 2 documents about authentication:
> 1. Auth Design
> 2. OAuth Setup

### Fetch Workflow

**User asks ChatGPT:**
> "Show me the Auth Design document"

**ChatGPT internally calls:**
```json
{
  "tool": "fetch",
  "arguments": {
    "id": "auth-design"
  }
}
```

**Basic Memory responds:**
```json
[{
  "type": "text",
  "text": "{\"id\": \"auth-design\", \"title\": \"Auth Design\", \"text\": \"# Auth Design\\n\\n## Overview\\n...full content...\", \"url\": \"auth-design\", \"metadata\": {\"format\": \"markdown\"}}"
}]
```

**ChatGPT displays:**
> Here's the Auth Design document:
>
> # Auth Design
>
> ## Overview
> ...

## Response Schema

### Search Response

```typescript
{
  results: Array<{
    id: string,        // Document permalink
    title: string,     // Document title
    url: string        // Document URL/permalink
  }>,
  total_count: number, // Total results found
  query: string        // Original query echoed back
}
```

### Fetch Response

```typescript
{
  id: string,          // Document identifier
  title: string,       // Document title
  text: string,        // Full markdown content
  url: string,         // Document URL/permalink
  metadata: {
    format: string     // "markdown"
  }
}
```

### Error Response

```typescript
{
  results: [],         // Empty for search
  error: string,       // Error type
  error_message: string // Error details
}
```

## Differences from Standard Tools

### ChatGPT Tools vs Standard MCP Tools

| Feature | ChatGPT Tools | Standard Tools |
|---------|---------------|----------------|
| **Tool Names** | `search`, `fetch` | `search_notes`, `read_note` |
| **Response Format** | `[{"type": "text", "text": "..."}]` | Direct JSON |
| **Parameters** | Minimal (query, id) | Rich (project, page, filters) |
| **Project Selection** | Automatic | Explicit or default_project_mode |
| **Pagination** | Fixed (10 results) | Configurable |
| **Error Handling** | JSON error objects | Direct error messages |

### Automatic Defaults

ChatGPT tools use sensible defaults:

```python
# search tool defaults
page = 1
page_size = 10
search_type = "text"
project = None  # Auto-resolved

# fetch tool defaults
page = 1
page_size = 10
project = None  # Auto-resolved
```

## Project Resolution

### Automatic Project Selection

ChatGPT tools use automatic project resolution:

1. **CLI constraint** (if `--project` flag used)
2. **default_project_mode** (if enabled in config)
3. **Error** if no project can be resolved

**Recommended Setup:**
```json
// ~/.basic-memory/config.json
{
  "default_project": "main",
  "default_project_mode": true
}
```

This ensures ChatGPT tools work without explicit project parameters.

## Error Handling

### Search Errors

```json
[{
  "type": "text",
  "text": "{\"results\": [], \"error\": \"Search failed\", \"error_details\": \"Project not found\"}"
}]
```

### Fetch Errors

```json
[{
  "type": "text",
  "text": "{\"id\": \"missing-doc\", \"title\": \"Fetch Error\", \"text\": \"Failed to fetch document: Not found\", \"url\": \"missing-doc\", \"metadata\": {\"error\": \"Fetch failed\"}}"
}]
```

### Common Errors

**No project found:**
```json
{
  "error": "Project required",
  "error_message": "No project specified and default_project_mode not enabled"
}
```

**Document not found:**
```json
{
  "id": "doc-123",
  "title": "Document Not Found",
  "text": "# Note Not Found\n\nThe requested document 'doc-123' could not be found",
  "metadata": {"error": "Document not found"}
}
```

## Deployment Patterns

### Production Deployment

**1. Deploy to Cloud:**
```bash
# Docker deployment
docker build -t basic-memory .
docker run -p 8000:8000 \
  -e BASIC_MEMORY_API_URL=https://api.basicmemory.cloud \
  basic-memory mcp --port 8000

# Or use managed hosting
fly deploy
```

**2. Configure ChatGPT:**
```json
{
  "mcpServers": {
    "basic-memory": {
      "url": "https://your-app.fly.dev/mcp"
    }
  }
}
```

**3. Enable default_project_mode:**
```json
{
  "default_project_mode": true,
  "default_project": "main"
}
```

### Development/Testing

**1. Use ngrok:**
```bash
# Terminal 1: Start MCP server
bm mcp --port 8000

# Terminal 2: Expose with ngrok
ngrok http 8000
# → https://abc123.ngrok.io
```

**2. Configure ChatGPT:**
```json
{
  "mcpServers": {
    "basic-memory-dev": {
      "url": "https://abc123.ngrok.io/mcp"
    }
  }
}
```

## Limitations

### ChatGPT-Specific Constraints

1. **Remote only** - Cannot use local MCP server
2. **No streaming** - Results returned all at once
3. **Fixed pagination** - 10 results per search
4. **Simplified parameters** - Cannot specify advanced filters
5. **No project selection** - Must use default_project_mode
6. **Subscription required** - ChatGPT Plus/Pro only

### Workarounds

**For more results:**
- Refine search query
- Use fetch to get full documents
- Deploy multiple searches

**For project selection:**
- Enable default_project_mode
- Or deploy separate instances per project

**For advanced features:**
- Use Claude Desktop with full MCP tools
- Or use Basic Memory CLI directly

## Troubleshooting

### ChatGPT Can't Connect

**Problem:** ChatGPT shows "MCP server unavailable"

**Solutions:**
1. Verify server is publicly accessible
   ```bash
   curl https://your-server.com/mcp/health
   ```

2. Check firewall/security groups
3. Verify HTTPS (not HTTP)
4. Check API key if using auth

### No Results Returned

**Problem:** Search returns empty results

**Solutions:**
1. Check default_project_mode enabled
   ```json
   {"default_project_mode": true}
   ```

2. Verify data is synced
   ```bash
   bm sync --project main
   ```

3. Test search locally
   ```bash
   bm tools search --query "test"
   ```

### Format Errors

**Problem:** ChatGPT shows parsing errors

**Check response format:**
```python
# Must be wrapped array
[{"type": "text", "text": "{...json...}"}]

# NOT direct JSON
{"results": [...]}
```

### Developer Mode Not Available

**Problem:** Can't find Developer Mode in ChatGPT

**Solution:**
- Ensure ChatGPT Plus/Pro subscription
- Check for feature rollout (may not be available in all regions)
- Contact OpenAI support

## Best Practices

### 1. Enable default_project_mode

```json
{
  "default_project_mode": true,
  "default_project": "main"
}
```

### 2. Use Cloud Deployment

Don't rely on ngrok for production:
```bash
# Production deployment
fly deploy
# or
railway up
# or
vercel deploy
```

### 3. Monitor Usage

```bash
# Enable logging
export BASIC_MEMORY_LOG_LEVEL=INFO

# Monitor requests
tail -f /var/log/basic-memory/mcp.log
```

### 4. Secure Your Server

```bash
# Use API key authentication
export BASIC_MEMORY_API_KEY=secret

# Restrict CORS
export BASIC_MEMORY_ALLOWED_ORIGINS=https://chatgpt.com
```

### 5. Test Locally First

```bash
# Test with curl
curl -X POST https://your-server.com/mcp/tools/search \
  -H "Content-Type: application/json" \
  -d '{"query": "test"}'
```

## Comparison with Claude Desktop

| Feature | ChatGPT | Claude Desktop |
|---------|---------|----------------|
| **MCP Mode** | Remote only | Local or Remote |
| **Tools** | 2 (search, fetch) | 17+ (full suite) |
| **Response Format** | OpenAI-specific | Standard MCP |
| **Project Support** | Default only | Full multi-project |
| **Subscription** | Plus/Pro required | Free (Claude) |
| **Configuration** | Developer mode | Config file |
| **Performance** | Network latency | Local (instant) |

**Recommendation:** Use Claude Desktop for full features, ChatGPT for convenience

## See Also

- ChatGPT MCP documentation: https://platform.openai.com/docs/mcp
- `default-project-mode.md` - Required for ChatGPT tools
- `cloud-mode-usage.md` - Deploying MCP to cloud
- Standard MCP tools documentation
