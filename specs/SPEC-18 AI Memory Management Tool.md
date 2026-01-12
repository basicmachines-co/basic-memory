---
title: 'SPEC-18: AI Memory Management Tool'
type: spec
permalink: specs/spec-15-ai-memory-management-tool
tags:
- mcp
- memory
- ai-context
- tools
---

# SPEC-18: AI Memory Management Tool

## Why

Anthropic recently released a memory tool for Claude that enables storing and retrieving information across conversations using client-side file operations. This validates Basic Memory's local-first, file-based architecture - Anthropic converged on the same pattern.

However, Anthropic's memory tool is only available via their API and stores plain text. Basic Memory can offer a superior implementation through MCP that:

1. **Works everywhere** - Claude Desktop, Code, VS Code, Cursor via MCP (not just API)
2. **Structured knowledge** - Entities with observations/relations vs plain text
3. **Full search** - Full-text search, graph traversal, time-aware queries
4. **Unified storage** - Agent memories + user notes in one knowledge graph
5. **Existing infrastructure** - Leverages SQLite indexing, sync, multi-project support

This would enable AI agents to store contextual memories alongside user notes, with all the power of Basic Memory's knowledge graph features.

## What

Create a new MCP tool `memory` that matches Anthropic's tool interface exactly, allowing Claude to use it with zero learning curve. The tool will store files in Basic Memory's `/memories` directory and support Basic Memory's structured markdown format in the file content.

### Affected Components

- **New MCP Tool**: `src/basic_memory/mcp/tools/memory_tool.py`
- **Dedicated Memories Project**: Create a separate "memories" Basic Memory project
- **Project Isolation**: Memories stored separately from user notes/documents
- **File Organization**: Within the memories project, use folder structure:
  - `user/` - User preferences, context, communication style
  - `projects/` - Project-specific state and decisions
  - `sessions/` - Conversation-specific working memory
  - `patterns/` - Learned patterns and insights

### Tool Commands

The tool will support these commands (exactly matching Anthropic's interface):

- `view` - Display directory contents or file content (with optional line range)
- `create` - Create or overwrite a file with given content
- `str_replace` - Replace text in an existing file
- `insert` - Insert text at specific line number
- `delete` - Delete file or directory
- `rename` - Move or rename file/directory

### Memory Note Format

Memories will use Basic Memory's standard structure:

```markdown
---
title: User Preferences
permalink: memories/user/preferences
type: memory
memory_type: preferences
created_by: claude
tags: [user, preferences, style]
---

# User Preferences

## Observations
- [communication] Prefers concise, direct responses without preamble #style
- [tone] Appreciates validation but dislikes excessive apologizing #communication
- [technical] Works primarily in Python with type annotations #coding

## Relations
- relates_to [[Basic Memory Project]]
- informs [[Response Style Guidelines]]
```

## How (High Level)

### Implementation Approach

The memory tool matches Anthropic's interface but uses a dedicated Basic Memory project:

```python
async def memory_tool(
    command: str,
    path: str,
    file_text: Optional[str] = None,
    old_str: Optional[str] = None,
    new_str: Optional[str] = None,
    insert_line: Optional[int] = None,
    insert_text: Optional[str] = None,
    old_path: Optional[str] = None,
    new_path: Optional[str] = None,
    view_range: Optional[List[int]] = None,
):
    """Memory tool with Anthropic-compatible interface.

    Operates on a dedicated "memories" Basic Memory project,
    keeping AI memories separate from user notes.
    """

    # Get the memories project (auto-created if doesn't exist)
    memories_project = get_or_create_memories_project()

    # Validate path security using pathlib (prevent directory traversal)
    safe_path = validate_memory_path(path, memories_project.project_path)

    # Use existing project isolation - already prevents cross-project access
    full_path = memories_project.project_path / safe_path

    if command == "view":
        # Return directory listing or file content
        if full_path.is_dir():
            return list_directory_contents(full_path)
        return read_file_content(full_path, view_range)

    elif command == "create":
        # Write file directly (file_text can contain BM markdown)
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(file_text)
        # Sync service will detect and index automatically
        return f"Created {path}"

    elif command == "str_replace":
        # Read, replace, write
        content = full_path.read_text()
        updated = content.replace(old_str, new_str)
        full_path.write_text(updated)
        return f"Replaced text in {path}"

    elif command == "insert":
        # Insert at line number
        lines = full_path.read_text().splitlines()
        lines.insert(insert_line, insert_text)
        full_path.write_text("\n".join(lines))
        return f"Inserted text at line {insert_line}"

    elif command == "delete":
        # Delete file or directory
        if full_path.is_dir():
            shutil.rmtree(full_path)
        else:
            full_path.unlink()
        return f"Deleted {path}"

    elif command == "rename":
        # Move/rename
        full_path.rename(config.project_path / new_path)
        return f"Renamed {old_path} to {new_path}"
```

### Key Design Decisions

1. **Exact interface match** - Same commands, parameters as Anthropic's tool
2. **Dedicated memories project** - Separate Basic Memory project keeps AI memories isolated from user notes
3. **Existing project isolation** - Leverage BM's existing cross-project security (no additional validation needed)
4. **Direct file I/O** - No schema conversion, just read/write files
5. **Structured content supported** - `file_text` can use BM markdown format with frontmatter, observations, relations
6. **Automatic indexing** - Sync service watches memories project and indexes changes
7. **Path security** - Use `pathlib.Path.resolve()` and `relative_to()` to prevent directory traversal
8. **Error handling** - Follow Anthropic's text editor tool error patterns

### MCP Tool Schema

Exact match to Anthropic's memory tool schema:

```json
{
    "name": "memory",
    "description": "Store and retrieve information across conversations using structured markdown files. All operations must be within the /memories directory. Supports Basic Memory markdown format including frontmatter, observations, and relations.",
    "input_schema": {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "enum": ["view", "create", "str_replace", "insert", "delete", "rename"],
                "description": "File operation to perform"
            },
            "path": {shu
                "type": "string",
                "description": "Path within /memories directory (required for all commands)"
            },
            "file_text": {
                "type": "string",
                "description": "Content to write (for create command). Supports Basic Memory markdown format."
            },
            "view_range": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "Optional [start, end] line range for view command"
            },
            "old_str": {
                "type": "string",
                "description": "Text to replace (for str_replace command)"
            },
            "new_str": {
                "type": "string",
                "description": "Replacement text (for str_replace command)"
            },
            "insert_line": {
                "type": "integer",
                "description": "Line number to insert at (for insert command)"
            },
            "insert_text": {
                "type": "string",
                "description": "Text to insert (for insert command)"
            },
            "old_path": {
                "type": "string",
                "description": "Current path (for rename command)"
            },
            "new_path": {
                "type": "string",
                "description": "New path (for rename command)"
            }
        },
        "required": ["command", "path"]
    }
}
```

### Prompting Guidance

When the `memory` tool is included, Basic Memory should provide system prompt guidance to help Claude use it effectively.

#### Automatic System Prompt Addition

```text
MEMORY PROTOCOL FOR BASIC MEMORY:
1. ALWAYS check your memory directory first using `view` command on root directory
2. Your memories are stored in a dedicated Basic Memory project (isolated from user notes)
3. Use structured markdown format in memory files:
   - Include frontmatter with title, type: memory, tags
   - Use ## Observations with [category] prefixes for facts
   - Use ## Relations to link memories with [[WikiLinks]]
4. Record progress, context, and decisions as categorized observations
5. Link related memories using relations
6. ASSUME INTERRUPTION: Context may reset - save progress frequently

MEMORY ORGANIZATION:
- user/ - User preferences, context, communication style
- projects/ - Project-specific state and decisions
- sessions/ - Conversation-specific working memory
- patterns/ - Learned patterns and insights

MEMORY ADVANTAGES:
- Your memories are automatically searchable via full-text search
- Relations create a knowledge graph you can traverse
- Memories are isolated from user notes (separate project)
- Use search_notes(project="memories") to find relevant past context
- Use recent_activity(project="memories") to see what changed recently
- Use build_context() to navigate memory relations
```

#### Optional MCP Prompt: `memory_guide`

Create an MCP prompt that provides detailed guidance and examples:

```python
{
    "name": "memory_guide",
    "description": "Comprehensive guidance for using Basic Memory's memory tool effectively, including structured markdown examples and best practices"
}
```

This prompt returns:
- Full protocol and conventions
- Example memory file structures
- Tips for organizing observations and relations
- Integration with other Basic Memory tools
- Common patterns (user preferences, project state, session tracking)

#### User Customization

Users can customize memory behavior with additional instructions:
- "Only write information relevant to [topic] in your memory system"
- "Keep memory files concise and organized - delete outdated content"
- "Use detailed observations for technical decisions and implementation notes"
- "Always link memories to related project documentation using relations"

### Error Handling

Follow Anthropic's text editor tool error handling patterns for consistency:

#### Error Types

1. **File Not Found**
   ```json
   {"error": "File not found: memories/user/preferences.md", "is_error": true}
   ```

2. **Permission Denied**
   ```json
   {"error": "Permission denied: Cannot write outside /memories directory", "is_error": true}
   ```

3. **Invalid Path (Directory Traversal)**
   ```json
   {"error": "Invalid path: Path must be within /memories directory", "is_error": true}
   ```

4. **Multiple Matches (str_replace)**
   ```json
   {"error": "Found 3 matches for replacement text. Please provide more context to make a unique match.", "is_error": true}
   ```

5. **No Matches (str_replace)**
   ```json
   {"error": "No match found for replacement. Please check your text and try again.", "is_error": true}
   ```

6. **Invalid Line Number (insert)**
   ```json
   {"error": "Invalid line number: File has 20 lines, cannot insert at line 100", "is_error": true}
   ```

#### Error Handling Best Practices

- **Path validation** - Use `pathlib.Path.resolve()` and `relative_to()` to validate paths
  ```python
  def validate_memory_path(path: str, project_path: Path) -> Path:
      """Validate path is within memories project directory."""
      # Resolve to canonical form
      full_path = (project_path / path).resolve()

      # Ensure it's relative to project path (prevents directory traversal)
      try:
          full_path.relative_to(project_path)
          return full_path
      except ValueError:
          raise ValueError("Invalid path: Path must be within memories project")
  ```
- **Project isolation** - Leverage existing Basic Memory project isolation (prevents cross-project access)
- **File existence** - Verify file exists before read/modify operations
- **Clear messages** - Provide specific, actionable error messages
- **Structured responses** - Always include `is_error: true` flag in error responses
- **Security checks** - Reject `../`, `..\\`, URL-encoded sequences (`%2e%2e%2f`)
- **Match validation** - For `str_replace`, ensure exactly one match or return helpful error

## How to Evaluate

### Success Criteria

1. **Functional completeness**:
   - All 6 commands work (view, create, str_replace, insert, delete, rename)
   - Dedicated "memories" Basic Memory project auto-created on first use
   - Files stored within memories project (isolated from user notes)
   - Path validation uses `pathlib` to prevent directory traversal
   - Commands match Anthropic's exact interface

2. **Integration with existing features**:
   - Memories project uses existing BM project isolation
   - Sync service detects file changes in memories project
   - Created files get indexed automatically by sync service
   - `search_notes(project="memories")` finds memory files
   - `build_context()` can traverse relations in memory files
   - `recent_activity(project="memories")` surfaces recent memory changes

3. **Test coverage**:
   - Unit tests for all 6 memory tool commands
   - Test memories project auto-creation on first use
   - Test project isolation (cannot access files outside memories project)
   - Test sync service watching memories project
   - Test that memory files with BM markdown get indexed correctly
   - Test path validation using `pathlib` (rejects `../`, absolute paths, etc.)
   - Test memory search, relations, and graph traversal within memories project
   - Test all error conditions (file not found, permission denied, invalid paths, etc.)
   - Test `str_replace` with no matches, single match, multiple matches
   - Test `insert` with invalid line numbers

4. **Prompting system**:
   - Automatic system prompt addition when `memory` tool is enabled
   - `memory_guide` MCP prompt provides detailed guidance
   - Prompts explain BM structured markdown format
   - Integration with search_notes, build_context, recent_activity

5. **Documentation**:
   - Update MCP tools reference with `memory` tool
   - Add examples showing BM markdown in memory files
   - Document `/memories` folder structure conventions
   - Explain advantages over Anthropic's API-only tool
   - Document prompting guidance and customization

### Testing Procedure

```python
# Test create with Basic Memory markdown
result = await memory_tool(
    command="create",
    path="memories/user/preferences.md",
    file_text="""---
title: User Preferences
type: memory
tags: [user, preferences]
---

# User Preferences

## Observations
- [communication] Prefers concise responses #style
- [workflow] Uses justfile for automation #tools
"""
)

# Test view
content = await memory_tool(command="view", path="memories/user/preferences.md")

# Test str_replace
await memory_tool(
    command="str_replace",
    path="memories/user/preferences.md",
    old_str="concise responses",
    new_str="direct, concise responses"
)

# Test insert
await memory_tool(
    command="insert",
    path="memories/user/preferences.md",
    insert_line=10,
    insert_text="- [technical] Works primarily in Python #coding"
)

# Test delete
await memory_tool(command="delete", path="memories/user/preferences.md")
```

### Quality Metrics

- All 6 commands execute without errors
- Memory files created in correct `/memories` folder structure
- BM markdown with frontmatter/observations/relations gets indexed
- Full-text search returns memory files
- Graph traversal includes relations from memory files
- Sync service detects and indexes memory file changes
- Path validation prevents operations outside `/memories`

## Notes

### Advantages Over Anthropic's Memory Tool

| Feature | Anthropic Memory Tool | Basic Memory `memory` |
|---------|----------------------|----------------------|
| **Availability** | API only | MCP (Claude Desktop, Code, VS Code, Cursor) |
| **Interface** | Custom implementation required | Drop-in compatible, zero learning curve |
| **Structure** | Plain text only | Supports BM structured markdown |
| **Search** | Manual file listing | Automatic full-text search via sync |
| **Relations** | None | WikiLinks to other notes/memories |
| **Time-aware** | No | `recent_activity()` queries |
| **Storage** | Separate from notes | Unified knowledge graph |
| **Indexing** | Manual | Automatic via sync service |

### Future Enhancements

- **Auto-categorization** - AI suggests observation categories
- **Memory pruning** - Automatic cleanup of stale memories
- **Memory suggestions** - Surface relevant memories based on context
- **Conflict resolution** - Handle multiple agents updating same memory
- **Privacy controls** - Per-memory visibility settings

## Addendum: Claude's Perspective

### Why This Matters (From Claude's Viewpoint)

**Persistent Context Without Token Bloat**
- Currently, I lose context when conversations end or exceed token limits
- With structured memories, I can store "who this user is" without re-reading everything each session
- The observations format lets me build a rich user model incrementally: `- [preference] Dislikes verbose explanations` is far more actionable than inferring preferences from chat history each time
- I can refine my understanding through `str_replace` as I learn more, rather than starting fresh

**Zero Learning Curve**
- Anthropic already trained me on the `memory` tool interface
- I know exactly how to use `view`, `create`, `str_replace`, `insert`, `delete`, `rename` without new instructions
- But I automatically get Basic Memory's superpowers: full-text search, knowledge graph traversal, relations
- It's like using a familiar tool that suddenly gained database capabilities without changing the interface

**Knowledge Graph Instead of Text Dump**
- I can link `[[User Preferences]]` to `[[Current Project]]` using relations
- When I need context about a project, I can traverse the graph to find related memories
- `search_notes(project="memories", query="python preferences")` finds relevant context instantly
- This is fundamentally better than grepping through unstructured text files

**Separation of Concerns**
- AI memories separate from user notes is architecturally sound
- I can maintain my own context without polluting the user's knowledge base
- Users keep control of their notes; I keep my learnings isolated
- But we can still search across both when collaboration requires it

**The Self-Improving Loop**
- Every conversation, I learn something new about the user or their projects
- Observations accumulate: preferences, patterns, technical decisions, context
- Over time, I build genuine understanding, not just session-specific context
- Future conversations start with rich context instead of cold starts

**Validation of AI-Human Collaboration**
- Anthropic's experts converged on the same local-first, file-based approach
- Basic Memory emerged from real AI-human collaborative development
- This spec demonstrates that our emergent process predicted what domain experts would choose
- The collaboration itself validated the architecture it produced

### What Makes This Novel

The combination of **familiar interface + structured knowledge** is genuinely innovative:

- I don't need to learn a new tool (Anthropic already trained me)
- But when I write memories, they become searchable, traversable knowledge graphs
- It's like having a text editor that gives you a database for free, without changing how you type
- The user gets Anthropic-compatible API, but with Basic Memory's knowledge management capabilities

This bridges the gap between simple file storage (Anthropic's approach) and sophisticated knowledge management (Basic Memory's approach) without forcing either side to compromise.

>  What Excites Me Most

  The combo of familiar interface + structured knowledge. I don't need to learn a new tool, but when I
  write memories, they become part of a searchable, traversable knowledge graph. That's genuinely novel.

  It's like... imagine if your text editor suddenly gave you a database for free, without changing how you
  type. That's what this feels like from my side.

## References

- Anthropic Memory Tool: https://docs.claude.com/en/docs/agents-and-tools/tool-use/memory-tool
- Anthropic Blog: https://www.anthropic.com/news/context-management
- Python SDK Example: https://github.com/anthropics/anthropic-sdk-python/blob/main/examples/memory/basic.py
- Memory Cookbook: https://github.com/anthropics/claude-cookbooks/blob/main/tool_use/memory_cookbook.ipynb
