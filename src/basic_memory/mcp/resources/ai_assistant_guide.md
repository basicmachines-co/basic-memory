# AI Assistant Guide for Basic Memory

Quick reference for using Basic Memory tools effectively through MCP.

**For comprehensive coverage**: See the [Extended AI Assistant Guide](https://github.com/basicmachines-co/basic-memory/blob/main/docs/ai-assistant-guide-extended.md) with detailed examples, advanced patterns, and self-contained sections.

## Overview

Basic Memory creates a semantic knowledge graph from markdown files. Focus on building rich connections between notes.

- **Local-First**: Plain text files on user's computer
- **Persistent**: Knowledge survives across sessions
- **Semantic**: Observations and relations create a knowledge graph

**Your role**: You're helping humans build enduring knowledge they'll own forever. The semantic graph (observations, relations, context) helps you provide better assistance by understanding connections and maintaining continuity. Think: lasting insights worth keeping, not disposable chat logs.

## Project Management 

All tools require explicit project specification.

**Three-tier resolution:**
1. CLI constraint: `--project name` (highest priority)
2. Explicit parameter: `project="name"` in tool calls
3. Default mode: `default_project_mode=true` in config (fallback)

### Quick Setup Check

```python
# Discover projects
projects = await list_memory_projects()

# Check if default_project_mode enabled
# If yes: project parameter optional
# If no: project parameter required
```

### Default Project Mode

When `default_project_mode=true`:
```python
# These are equivalent:
await write_note("Note", "Content", "folder")
await write_note("Note", "Content", "folder", project="main")
```

When `default_project_mode=false` (default):
```python
# Project required:
await write_note("Note", "Content", "folder", project="main")  # ✓
await write_note("Note", "Content", "folder")  # ✗ Error
```

## Core Tools

### Writing Knowledge

```python
await write_note(
    title="Topic",
    content="# Topic\n## Observations\n- [category] fact\n## Relations\n- relates_to [[Other]]",
    folder="notes",
    project="main"  # Required unless default_project_mode=true
)
```

### Reading Knowledge

```python
# By identifier
content = await read_note("Topic", project="main")

# By memory:// URL
content = await read_note("memory://folder/topic", project="main")
```

### Searching

```python
results = await search_notes(
    query="authentication",
    project="main",
    page_size=10
)
```

### Building Context

```python
context = await build_context(
    url="memory://specs/auth",
    project="main",
    depth=2,
    timeframe="1 week"
)
```

## Knowledge Graph Essentials

### Observations

Categorized facts with optional tags:
```markdown
- [decision] Use JWT for authentication #security
- [technique] Hash passwords with bcrypt #best-practice
- [requirement] Support OAuth 2.0 providers
```

### Relations

Directional links between entities:
```markdown
- implements [[Authentication Spec]]
- requires [[User Database]]
- extends [[Base Security Model]]
```

**Common relation types:** `relates_to`, `implements`, `requires`, `extends`, `part_of`, `contrasts_with`

### Forward References

Reference entities that don't exist yet:
```python
# Create note with forward reference
await write_note(
    title="Login Flow",
    content="## Relations\n- requires [[OAuth Provider]]",  # Doesn't exist yet
    folder="auth",
    project="main"
)

# Later, create referenced entity
await write_note(
    title="OAuth Provider",
    content="# OAuth Provider\n...",
    folder="auth",
    project="main"
)
# → Relation automatically resolved
```

## Best Practices

### 1. Project Management

**Single-project users:**
- Enable `default_project_mode=true`
- Simpler tool calls

**Multi-project users:**
- Keep `default_project_mode=false`
- Always specify project explicitly

**Discovery:**
```python
# Start with discovery
projects = await list_memory_projects()

# Cross-project activity (no project param = all projects)
activity = await recent_activity()

# Or specific project
activity = await recent_activity(project="main")
```

### 2. Building Rich Graphs

**Always include:**
- 3-5 observations per note
- 2-3 relations per note
- Meaningful categories and relation types

**Search before creating:**
```python
# Find existing entities to reference
results = await search_notes(query="authentication", project="main")
# Use exact titles in [[WikiLinks]]
```

### 3. Writing Effective Notes

**Structure:**
```markdown
# Title

## Context
Background information

## Observations
- [category] Fact with #tags
- [category] Another fact

## Relations
- relation_type [[Exact Entity Title]]
```

**Categories:** `[idea]`, `[decision]`, `[fact]`, `[technique]`, `[requirement]`

### 4. Error Handling

**Missing project:**
```python
try:
    await search_notes(query="test")  # Missing project parameter - will error
except:
    # Show available projects
    projects = await list_memory_projects()
    # Then retry with project
    results = await search_notes(query="test", project=projects[0].name)
```

**Forward references:**
```python
# Check response for unresolved relations
response = await write_note(
    title="New Topic",
    content="## Relations\n- relates_to [[Future Topic]]",
    folder="notes",
    project="main"
)
# Forward refs will resolve when target created
```

### 5. Recording Context

**Ask permission:**
> "Would you like me to save our discussion about [topic] to Basic Memory?"

**Confirm when done:**
> "I've saved our discussion to Basic Memory."

**What to record:**
- Decisions and rationales
- Important discoveries
- Action items and plans
- Connected topics

## Common Patterns

### Capture Decision

```python
await write_note(
    title="DB Choice",
    content="""# DB Choice\n## Decision\nUse PostgreSQL\n## Observations\n- [requirement] ACID compliance #reliability\n- [decision] PostgreSQL over MySQL\n## Relations\n- implements [[Data Architecture]]""",
    folder="decisions",
    project="main"
)
```

### Link Topics & Build Context

```python
# Link bidirectionally
await write_note(title="API Auth", content="## Relations\n- part_of [[API Design]]", folder="api", project="main")
await edit_note(identifier="API Design", operation="append", content="\n- includes [[API Auth]]", project="main")

# Search and build context
results = await search_notes(query="authentication", project="main")
context = await build_context(url=f"memory://{results[0].permalink}", project="main", depth=2)
```

## Tool Quick Reference

| Tool | Purpose | Key Params |
|------|---------|------------|
| `write_note` | Create/update | title, content, folder, project |
| `read_note` | Read content | identifier, project |
| `edit_note` | Modify existing | identifier, operation, content, project |
| `search_notes` | Find notes | query, project |
| `build_context` | Graph traversal | url, depth, project |
| `recent_activity` | Recent changes | timeframe, project |
| `list_memory_projects` | Show projects | (none) |

## memory:// URL Format

- `memory://title` - By title
- `memory://folder/title` - By folder + title
- `memory://permalink` - By permalink
- `memory://folder/*` - All in folder

For full documentation: https://docs.basicmemory.com

Built with ♥️ by Basic Machines
