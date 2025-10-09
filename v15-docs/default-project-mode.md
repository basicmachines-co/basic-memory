# Default Project Mode

**Status**: New Feature
**PR**: #298 (SPEC-6)
**Related**: explicit-project-parameter.md

## What's New

v0.15.0 introduces `default_project_mode` - a configuration option that simplifies single-project workflows by automatically using your default project when no explicit project parameter is provided.

## Quick Start

### Enable Default Project Mode

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

### Now Tools Work Without Project Parameter

```python
# Before (explicit project required)
await write_note("Note", "Content", "folder", project="main")

# After (with default_project_mode: true)
await write_note("Note", "Content", "folder")  # Uses "main" automatically
```

## Configuration Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `default_project_mode` | boolean | `false` | Enable auto-fallback to default project |
| `default_project` | string | `"main"` | Which project to use as default |

## How It Works

### Three-Tier Project Resolution

When a tool is called, Basic Memory resolves the project in this order:

1. **CLI Constraint** (Highest): `bm --project work-notes` forces all tools to use "work-notes"
2. **Explicit Parameter** (Medium): `project="specific"` in tool call
3. **Default Mode** (Lowest): Uses `default_project` if `default_project_mode: true`

### Examples

**With default_project_mode: false (default):**
```python
# Must specify project explicitly
await search_notes("query", project="main")  # ✓ Works
await search_notes("query")                  # ✗ Error: project required
```

**With default_project_mode: true:**
```python
# Project parameter is optional
await search_notes("query")                  # ✓ Uses default_project
await search_notes("query", project="work")  # ✓ Explicit override works
```

## Use Cases

### Single-Project Users

**Best for:**
- Users who maintain one primary knowledge base
- Personal knowledge management
- Single-purpose documentation

**Configuration:**
```json
{
  "default_project": "main",
  "default_project_mode": true,
  "projects": {
    "main": "/Users/you/basic-memory"
  }
}
```

**Benefits:**
- Simpler tool calls
- Less verbose for AI assistants
- Familiar workflow (like v0.14.x)

### Multi-Project Users

**Best for:**
- Multiple distinct knowledge bases (work, personal, research)
- Switching contexts frequently
- Team collaboration with separate projects

**Configuration:**
```json
{
  "default_project": "main",
  "default_project_mode": false,
  "projects": {
    "work": "/Users/you/work-kb",
    "personal": "/Users/you/personal-kb",
    "research": "/Users/you/research-kb"
  }
}
```

**Benefits:**
- Explicit project selection prevents mistakes
- Clear which knowledge base is being accessed
- Better for context switching

## Workflow Examples

### Single-Project Workflow

```python
# config.json: default_project_mode: true, default_project: "main"

# Write without specifying project
await write_note(
    title="Meeting Notes",
    content="# Team Sync\n...",
    folder="meetings"
)  # → Saved to "main" project

# Search across default project
results = await search_notes("quarterly goals")
# → Searches "main" project

# Build context from default project
context = await build_context("memory://goals/q4-2024")
# → Uses "main" project
```

### Multi-Project with Explicit Selection

```python
# config.json: default_project_mode: false

# Work project
await write_note(
    title="Architecture Decision",
    content="# ADR-001\n...",
    folder="decisions",
    project="work"
)

# Personal project
await write_note(
    title="Book Notes",
    content="# Design Patterns\n...",
    folder="reading",
    project="personal"
)

# Research project
await search_notes(
    query="machine learning",
    project="research"
)
```

### Hybrid: Default with Occasional Override

```python
# config.json: default_project_mode: true, default_project: "personal"

# Most operations use personal (default)
await write_note("Daily Journal", "...", "journal")
# → Saved to "personal"

# Explicitly use work project when needed
await write_note(
    title="Sprint Planning",
    content="...",
    folder="planning",
    project="work"  # Override default
)
# → Saved to "work"

# Back to default
await search_notes("goals")
# → Searches "personal"
```

## Migration Guide

### From v0.14.x (Implicit Project)

v0.14.x had implicit project context via middleware. To get similar behavior:

**Enable default_project_mode:**
```json
{
  "default_project": "main",
  "default_project_mode": true
}
```

Now tools work without explicit project parameter (like v0.14.x).

### From v0.15.0 Explicit-Only

If you started with v0.15.0 using explicit projects:

**Keep current behavior:**
```json
{
  "default_project_mode": false  # or omit (false is default)
}
```

**Or simplify for single project:**
```json
{
  "default_project": "main",
  "default_project_mode": true
}
```

## LLM Integration

### Claude Desktop

Claude can detect and use default_project_mode:

**Auto-detection:**
```python
# Claude reads config
config = read_config()

if config.get("default_project_mode"):
    # Use simple calls
    await write_note("Note", "Content", "folder")
else:
    # Discover and use explicit project
    projects = await list_memory_projects()
    await write_note("Note", "Content", "folder", project=projects[0].name)
```

### Custom MCP Clients

```python
from basic_memory.config import ConfigManager

config = ConfigManager().config

if config.default_project_mode:
    # Project parameter optional
    result = await mcp_tool(arg1, arg2)
else:
    # Project parameter required
    result = await mcp_tool(arg1, arg2, project="name")
```

## Error Handling

### Missing Project (default_project_mode: false)

```python
try:
    results = await search_notes("query")
except ValueError as e:
    print("Error: project parameter required")
    # Show available projects
    projects = await list_memory_projects()
    print(f"Available: {[p.name for p in projects]}")
```

### Invalid Default Project

```json
{
  "default_project": "nonexistent",
  "default_project_mode": true
}
```

**Result:** Falls back to "main" project if default doesn't exist.

## Configuration Management

### Update Config

```bash
# Edit directly
vim ~/.basic-memory/config.json

# Or use CLI (if available)
bm config set default_project_mode true
bm config set default_project main
```

### Verify Config

```python
from basic_memory.config import ConfigManager

config = ConfigManager().config
print(f"Default mode: {config.default_project_mode}")
print(f"Default project: {config.default_project}")
print(f"Projects: {list(config.projects.keys())}")
```

### Environment Override

```bash
# Override via environment
export BASIC_MEMORY_DEFAULT_PROJECT_MODE=true
export BASIC_MEMORY_DEFAULT_PROJECT=work

# Now default_project_mode enabled for this session
```

## Best Practices

1. **Choose based on workflow:**
   - Single project → enable default_project_mode
   - Multiple projects → keep explicit (false)

2. **Document your choice:**
   - Add comment to config.json explaining why

3. **Consistent with team:**
   - Agree on project mode for shared setups

4. **Test both modes:**
   - Try each to see what feels natural

5. **Use CLI constraints when needed:**
   - `bm --project work-notes` overrides everything

## Troubleshooting

### Tools Not Using Default Project

**Problem:** default_project_mode: true but tools still require project

**Check:**
```bash
# Verify config
cat ~/.basic-memory/config.json | grep default_project_mode

# Should show: "default_project_mode": true
```

**Solution:** Restart MCP server to reload config

### Wrong Project Being Used

**Problem:** Tools using unexpected project

**Check resolution order:**
1. CLI constraint (`--project` flag)
2. Explicit parameter in tool call
3. Default project (if mode enabled)

**Solution:** Check for CLI constraints or explicit parameters

### Config Not Loading

**Problem:** Changes to config.json not taking effect

**Solution:**
```bash
# Restart MCP server
# Or reload config programmatically
from basic_memory import config as config_module
config_module._config = None  # Clear cache
```

## Technical Details

### Implementation

```python
class BasicMemoryConfig(BaseSettings):
    default_project: str = Field(
        default="main",
        description="Name of the default project to use"
    )

    default_project_mode: bool = Field(
        default=False,
        description="When True, MCP tools automatically use default_project when no project parameter is specified"
    )
```

### Project Resolution Logic

```python
def resolve_project(
    explicit_project: Optional[str] = None,
    cli_project: Optional[str] = None,
    config: BasicMemoryConfig = None
) -> str:
    # 1. CLI constraint (highest priority)
    if cli_project:
        return cli_project

    # 2. Explicit parameter
    if explicit_project:
        return explicit_project

    # 3. Default mode (lowest priority)
    if config.default_project_mode:
        return config.default_project

    # 4. No project found
    raise ValueError("Project parameter required")
```

## See Also

- `explicit-project-parameter.md` - Why explicit project is required
- SPEC-6: Explicit Project Parameter Architecture
- MCP tools documentation
