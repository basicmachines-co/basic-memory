# Explicit Project Parameter (SPEC-6)

**Status**: Breaking Change
**PR**: #298
**Affects**: All MCP tool users

## What Changed

Starting in v0.15.0, **all MCP tools require an explicit `project` parameter**. The previous implicit project context (via middleware) has been removed in favor of a stateless architecture.

### Before v0.15.0
```python
# Tools used implicit current_project from middleware
await write_note("My Note", "Content", "folder")
await search_notes("query")
```

### v0.15.0 and Later
```python
# Explicit project required
await write_note("My Note", "Content", "folder", project="main")
await search_notes("query", project="main")
```

## Why This Matters

**Benefits:**
- **Stateless Architecture**: Tools are now truly stateless - no hidden state
- **Multi-project Clarity**: Explicit about which project you're working with
- **Better for Cloud**: Enables proper multi-tenant isolation
- **Simpler Debugging**: No confusion about "current" project

**Impact:**
- Existing MCP integrations may break if they don't specify project
- LLMs need to be aware of project parameter requirement
- Configuration option available for easier migration (see below)

## How to Use

### Option 1: Specify Project Every Time (Recommended for Multi-project Users)

```python
# Always include project parameter
results = await search_notes(
    query="authentication",
    project="work-docs"
)

content = await read_note(
    identifier="Search Design",
    project="work-docs"
)

await write_note(
    title="New Feature",
    content="...",
    folder="specs",
    project="work-docs"
)
```

### Option 2: Enable default_project_mode (Recommended for Single-project Users)

Edit `~/.basic-memory/config.json`:

```json
{
  "default_project": "main",
  "default_project_mode": true,
  "projects": {
    "main": "/Users/you/basic-memory"
  }
}
```

With `default_project_mode: true`:
```python
# Project parameter is optional - uses default_project when omitted
await write_note("My Note", "Content", "folder")  # Uses "main" project
await search_notes("query")  # Uses "main" project

# Can still override with explicit project
await search_notes("query", project="other-project")
```

### Option 3: Project Discovery for New Users

If you don't know which project to use:

```python
# List available projects
projects = await list_memory_projects()
for project in projects:
    print(f"- {project.name}: {project.path}")

# Check recent activity to find active project
activity = await recent_activity()  # Shows cross-project activity
# Returns recommendations for which project to use
```

## Migration Guide

### For Claude Desktop Users

1. **Check your config**: `cat ~/.basic-memory/config.json`

2. **Single project setup** (easiest):
   ```json
   {
     "default_project_mode": true,
     "default_project": "main"
   }
   ```

3. **Multi-project setup** (explicit):
   - Keep `default_project_mode: false` (or omit it)
   - LLM will need to specify project in each call

### For MCP Server Developers

Update tool calls to include project parameter:

```python
# Old (v0.14.x)
async def my_integration():
    # Relied on middleware to set current_project
    results = await search_notes(query="test")

# New (v0.15.0+)
async def my_integration(project: str = "main"):
    # Explicitly pass project
    results = await search_notes(query="test", project=project)
```

### For API Users

If using the Basic Memory API directly:

```python
# All endpoints now require project parameter
import httpx

async with httpx.AsyncClient() as client:
    response = await client.post(
        "http://localhost:8000/notes/search",
        json={
            "query": "test",
            "project": "main"  # Required
        }
    )
```

## Technical Details

### Architecture Change

**Removed:**
- `ProjectMiddleware` - no longer maintains project context
- `get_current_project()` - removed from MCP tools
- Implicit project state in MCP server

**Added:**
- `default_project_mode` config option
- Explicit project parameter on all MCP tools
- Stateless tool architecture (SPEC-6)

### Configuration Options

| Config Key | Type | Default | Description |
|------------|------|---------|-------------|
| `default_project_mode` | bool | `false` | Auto-use default_project when project param omitted |
| `default_project` | string | `"main"` | Project to use in default_project_mode |

### Three-Tier Project Resolution

1. **CLI Constraint** (Highest Priority): `--project` flag constrains all operations
2. **Explicit Parameter** (Medium): `project="name"` in tool calls
3. **Default Mode** (Lowest): Falls back to `default_project` if `default_project_mode: true`

## Common Questions

**Q: Will my existing setup break?**
A: If you use a single project and enable `default_project_mode: true`, no. Otherwise, you'll need to add project parameters.

**Q: Can I still use multiple projects?**
A: Yes! Just specify the project parameter explicitly in each call.

**Q: What if I forget the project parameter?**
A: You'll get an error unless `default_project_mode: true` is set in config.

**Q: How does this work with Claude Desktop?**
A: Claude can read your config and use default_project_mode, or it can discover projects using `list_memory_projects()`.

## Related Changes

- See `default-project-mode.md` for detailed config options
- See `cloud-mode-usage.md` for cloud API usage
- See SPEC-6 for full architectural specification
