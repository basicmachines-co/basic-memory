<!--
  This file is AUTO-GENERATED. Do not edit it by hand.

  To regenerate:
      uv run scripts/generate_tool_docs.py

  Source: scripts/generate_tool_docs.py
-->

# Basic Memory MCP Tool Reference

Complete reference for all MCP tools exposed by the Basic Memory server.
Tools are grouped by function. Parameters marked *(required)* have no default value.

> **Regenerating this file**: run `uv run scripts/generate_tool_docs.py` from the
> repository root. The output is deterministic; running it twice should produce
> an identical file (zero diff).

## Table of Contents


- [Note Management](#note-management)
  - [`write_note`](#write-note)
  - [`read_note`](#read-note)
  - [`view_note`](#view-note)
  - [`edit_note`](#edit-note)
  - [`move_note`](#move-note)
  - [`delete_note`](#delete-note)
- [Reading & Navigation](#reading-navigation)
  - [`read_content`](#read-content)
  - [`build_context`](#build-context)
  - [`recent_activity`](#recent-activity)
  - [`list_directory`](#list-directory)
- [Search](#search)
  - [`search_notes`](#search-notes)
  - [`search`](#search-tool)
  - [`fetch`](#fetch)
- [Project & Workspace Management](#project-workspace-management)
  - [`list_memory_projects`](#list-memory-projects)
  - [`create_memory_project`](#create-memory-project)
  - [`delete_project`](#delete-project)
  - [`list_workspaces`](#list-workspaces)
- [Schema Tools](#schema-tools)
  - [`schema_validate`](#schema-validate)
  - [`schema_infer`](#schema-infer)
  - [`schema_diff`](#schema-diff)
- [Visualization](#visualization)
  - [`canvas`](#canvas)
- [Info & Utilities](#info-utilities)
  - [`cloud_info`](#cloud-info)
  - [`release_notes`](#release-notes)


---


## Note Management

### `write_note`

Create a markdown note. If the note already exists, returns an error by default — pass overwrite=True to replace.

Write a markdown note to the knowledge base.

Creates a markdown note with semantic observations and relations.
If the note already exists, returns an error by default. Pass overwrite=True
to replace the existing note. For incremental updates, use edit_note instead.

Project Resolution:
Server resolves projects using a unified priority chain (same in local and cloud modes):
Single Project Mode → project parameter → default project.
Uses default project automatically. Specify `project` parameter to target a different project.

The content can include semantic observations and relations using markdown syntax:

Observations format:
    `- [category] Observation text #tag1 #tag2 (optional context)`

    Examples:
    `- [design] Files are the source of truth #architecture (All state comes from files)`
    `- [tech] Using SQLite for storage #implementation`
    `- [note] Need to add error handling #todo`

Relations format:
    - Explicit: `- relation_type [[Entity]] (optional context)`
    - Quoted: `- "multi word relation type" [[Entity]] (optional context)`
    - Quoted: `- 'multi word relation type' [[Entity]] (optional context)`
    - Inline: Any other `[[Entity]]` reference creates a `links_to` relation

    Examples:
    `- depends_on [[Content Parser]] (Need for semantic extraction)`
    `- "based on" [[Design Notes]]`
    `- 'in response to' [[Incident Review]]`
    `- implements [[Search Spec]] (Initial implementation)`
    `- This feature extends [[Base Design]] and uses [[Core Utils]]`

Returns:
    A markdown formatted summary of the semantic content, including:
    - Creation/update status with project name
    - File path and checksum
    - Observation counts by category
    - Relation counts (resolved/unresolved)
    - Tags if present
    - Session tracking metadata for project awareness

Raises:
    HTTPError: If project doesn't exist or is inaccessible
    SecurityError: If directory path attempts path traversal

**Parameters**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `title` | `str` | *(required)* | The title of the note |
| `content` | `str` | *(required)* | Markdown content for the note, can include observations and relations |
| `directory` | `str` | *(required)* | Directory path relative to project root where the file should be saved. |
| `project` | `Optional[str]` | `None` | Project name to write to. Optional - server will resolve using the |
| `project_id` | `Optional[str]` | `None` | Project external_id (UUID). Prefer this over `project` when known — |
| `tags` | `list[str] \| str \| None` | `None` | Tags to categorize the note. Can be a list of strings, a comma-separated string, or None. |
| `note_type` | `str` | `'note'` | Type of note to create (stored in frontmatter). Defaults to "note". |
| `metadata` | `dict \| None` | `None` | Optional dict of extra frontmatter fields merged into entity_metadata. |
| `overwrite` | `bool \| None` | `None` | If True, replace existing note on conflict. If False, error on conflict. |
| `output_format` | `Literal['text', 'json']` | `'text'` | "text" returns the existing markdown summary. "json" returns |

**Examples**

```python
# Create a simple note (uses default project automatically)
    write_note(
        project="my-research",
        title="Meeting Notes",
        directory="meetings",
        content="# Weekly Standup\n\n- [decision] Use SQLite for storage #tech"
    )

    # Create a note with tags and note type
    write_note(
        project="work-project",
        title="API Design",
        directory="specs",
        content="# REST API Specification\n\n- implements [[Authentication]]",
        tags=["api", "design"],
        note_type="guide"
    )

    # Overwrite an existing note explicitly
    write_note(
        project="my-research",
        title="Meeting Notes",
        directory="meetings",
        content="# Weekly Standup\n\n- [decision] Use PostgreSQL instead #tech",
        overwrite=True
    )

    # Create a schema note with custom frontmatter via metadata
    write_note(
        title="Person",
        directory="schemas",
        note_type="schema",
        content="# Person\n\nSchema for person entities.",
        metadata={
            "entity": "person",
            "version": 1,
            "schema": {"name": "string", "role?": "string"},
            "settings": {"validation": "warn"},
        },
    )
```

*Source: `src/basic_memory/mcp/tools/write_note.py`*

---

### `read_note`

Read a markdown note by title or permalink.

Return the raw markdown for a note, or guidance text if no match is found.

Finds and retrieves a note by its title, permalink, or content search,
returning the raw markdown content including observations, relations, and metadata.

Project Resolution:
Server resolves projects using a unified priority chain (same in local and cloud modes):
Single Project Mode → project parameter → default project.
Uses default project automatically. Specify `project` parameter to target a different project.

This tool will try multiple lookup strategies to find the most relevant note:
1. Direct permalink lookup
2. Title search fallback
3. Text search as last resort

Returns:
    The full markdown content of the note if found, or helpful guidance if not found.
    Content includes frontmatter, observations, relations, and all markdown formatting.

Raises:
    HTTPError: If project doesn't exist or is inaccessible
    SecurityError: If identifier attempts path traversal

Note:
    If the exact note isn't found, this tool provides helpful suggestions
    including related notes, search commands, and note creation templates.

**Parameters**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `identifier` | `str` | *(required)* | The title or permalink of the note to read |
| `project` | `Optional[str]` | `None` | Project name to read from. Optional - server will resolve using the |
| `project_id` | `Optional[str]` | `None` | Project external_id (UUID). Prefer this over `project` when known — |
| `page` | `int` | `1` | Page of fallback-search results to use when the identifier does not |
| `page_size` | `int` | `10` | Number of fallback-search results per page (default: 10). When no |
| `output_format` | `Literal['text', 'json']` | `'text'` | "text" returns markdown content or guidance text. |
| `include_frontmatter` | `bool` | `False` | When output_format="json", whether content should include the |

**Examples**

```python
# Read by permalink
    read_note("my-research", "specs/search-spec")

    # Read by title
    read_note("work-project", "Search Specification")

    # Read with memory URL
    read_note("my-research", "memory://specs/search-spec")

    # Read recent meeting notes
    read_note("team-docs", "Weekly Standup")

    # Page through fallback-search suggestions when nothing matches directly
    read_note("unknown topic", page=2, page_size=5)
```

*Source: `src/basic_memory/mcp/tools/read_note.py`*

---

### `view_note`

View a note as a formatted artifact for better readability.

View a markdown note as a formatted artifact.

This tool reads a note using the same logic as read_note but instructs Claude
to display the content as a markdown artifact in the Claude Desktop app.
Project parameter optional with server resolution.

Returns:
    Instructions for Claude to create a markdown artifact with the note content.

Raises:
    HTTPError: If project doesn't exist or is inaccessible
    SecurityError: If identifier attempts path traversal

**Parameters**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `identifier` | `str` | *(required)* | The title or permalink of the note to view |
| `project` | `Optional[str]` | `None` | Project name to read from. Optional - server will resolve using hierarchy. |
| `project_id` | `Optional[str]` | `None` | Project external_id (UUID). Prefer this over `project` when known — |

**Examples**

```python
# View a note by title
    view_note("Meeting Notes")

    # View a note by permalink
    view_note("meetings/weekly-standup")

    # Explicit project specification
    view_note("Meeting Notes", project="my-project")
```

*Source: `src/basic_memory/mcp/tools/view_note.py`*

---

### `edit_note`

Edit an existing markdown note using various operations like append, prepend, find_replace, replace_section, insert_before_section, or insert_after_section.

Edit an existing markdown note in the knowledge base.

Makes targeted changes to existing notes without rewriting the entire content.

Project Resolution:
Server resolves projects in this order: Single Project Mode → project parameter → default project.
If project unknown, use list_memory_projects() or recent_activity() first.

Returns:
    A markdown formatted summary of the edit operation and resulting semantic content,
    including operation details, file path, observations, relations, and project metadata.

Raises:
    HTTPError: If project doesn't exist or is inaccessible
    ValueError: If operation is invalid or required parameters are missing
    SecurityError: If identifier attempts path traversal

Note:
    Edit operations require exact identifier matches. If unsure, use read_note() or
    search_notes() first to find the correct identifier. When the identifier looks
    like a file path and the file exists on disk but is not indexed yet, edit_note
    indexes that file automatically and retries the edit. The tool provides detailed
    error messages with suggestions if operations fail.

**Parameters**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `identifier` | `str` | *(required)* | The exact title, permalink, or memory:// URL of the note to edit. |
| `operation` | `str` | *(required)* | The editing operation to perform: |
| `content` | `str` | *(required)* | The content to add or use for replacement |
| `project` | `Optional[str]` | `None` | Project name to edit in. Optional - server will resolve using hierarchy. |
| `workspace` | `Optional[str]` | `None` | Workspace slug, name, or tenant_id. When provided with `project`, |
| `project_id` | `Optional[str]` | `None` | Project external_id (UUID). Prefer this over `project` when known — |
| `section` | `Optional[str]` | `None` | For replace_section operation - the markdown header to replace content under (e.g., "## Notes", "### Implementation") |
| `find_text` | `Optional[str]` | `None` | For find_replace operation - the text to find and replace |
| `expected_replacements` | `Optional[int]` | `None` | For find_replace operation - the expected number of replacements (validation will fail if actual doesn't match) |
| `output_format` | `Literal['text', 'json']` | `'text'` | "text" returns the existing markdown summary. "json" returns |

**Examples**

```python
# Add new content to end of note
    edit_note("my-project", "project-planning", "append", "\n## New Requirements\n- Feature X\n- Feature Y")

    # Add timestamp at beginning (frontmatter-aware)
    edit_note("work-docs", "meeting-notes", "prepend", "## 2025-05-25 Update\n- Progress update...\n\n")

    # Update version number (single occurrence)
    edit_note("api-project", "config-spec", "find_replace", "v0.13.0", find_text="v0.12.0")

    # Update version in multiple places with validation
    edit_note("docs-project", "api-docs", "find_replace", "v2.1.0", find_text="v2.0.0", expected_replacements=3)

    # Replace text that appears multiple times - validate count first
    edit_note("team-docs", "docs/guide", "find_replace", "new-api", find_text="old-api", expected_replacements=5)

    # Replace implementation section
    edit_note("specs", "api-spec", "replace_section", "New implementation approach...\n", section="## Implementation")

    # Replace subsection with more specific header
    edit_note("docs", "docs/setup", "replace_section", "Updated install steps\n", section="### Installation")

    # Using different identifier formats (must be exact matches)
    edit_note("work-project", "Meeting Notes", "append", "\n- Follow up on action items")  # exact title
    edit_note("work-project", "docs/meeting-notes", "append", "\n- Follow up tasks")       # exact permalink

    # If uncertain about identifier, search first:
    # search_notes("work-project", "meeting")  # Find available notes
    # edit_note("work-project", "docs/meeting-notes-2025", "append", "content")  # Use exact result

    # Add new section to document
    edit_note("planning", "project-plan", "replace_section", "TBD - needs research\n", section="## Future Work")

    # Update status across document (expecting exactly 2 occurrences)
    edit_note("reports", "status-report", "find_replace", "In Progress", find_text="Not Started", expected_replacements=2)
```

*Source: `src/basic_memory/mcp/tools/edit_note.py`*

---

### `move_note`

Move a note or directory to a new location, updating database and maintaining links.

Move a note or directory to a new location within the same project.

Moves a note or directory from one location to another within the project,
updating all database references and maintaining semantic content. Uses stateless
architecture - project parameter optional with server resolution.

Returns:
    Success message with move details and project information.
    For directories, includes count of files moved and any errors.

Raises:
    ToolError: If project doesn't exist, identifier is not found, or destination_path is invalid

Note:
    This operation moves notes within the specified project only. Moving notes
    between different projects is not currently supported.

The move operation:
- Updates the entity's file_path in the database
- Moves the physical file on the filesystem
- Optionally updates permalinks if configured
- Re-indexes the entity for search
- Maintains all observations and relations

**Parameters**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `identifier` | `str` | *(required)* | For files: exact entity identifier (title, permalink, or memory:// URL). |
| `destination_path` | `str` | `''` | For files: new path relative to project root (e.g., "work/meetings/note.md") |
| `destination_folder` | `Optional[str]` | `None` | Move the note into this folder, preserving the original filename. |
| `is_directory` | `bool` | `False` | If True, moves an entire directory and all its contents. |
| `project` | `Optional[str]` | `None` | Project name to move within. Optional - server will resolve using hierarchy. |
| `project_id` | `Optional[str]` | `None` | Project external_id (UUID). Prefer this over `project` when known — |
| `output_format` | `Literal['text', 'json']` | `'text'` | "text" returns existing markdown guidance/success text. "json" |

**Examples**

```python
# Move a single note to new folder (exact title match)
    move_note("My Note", "work/notes/my-note.md")

    # Move by exact permalink
    move_note("my-note-permalink", "archive/old-notes/my-note.md")

    # Move note to archive folder (filename preserved automatically)
    move_note("my-note", destination_folder="archive")

    # Move with complex path structure
    move_note("experiments/ml-results", "archive/2025/ml-experiments.md")

    # Explicit project specification
    move_note("My Note", "work/notes/my-note.md", project="work-project")

    # Move entire directory
    move_note("docs", "archive/docs", is_directory=True)

    # Move nested directory
    move_note("projects/2024", "archive/projects/2024", is_directory=True)

    # If uncertain about identifier, search first:
    # search_notes("my note")  # Find available notes
    # move_note("docs/my-note-2025", "archive/my-note.md")  # Use exact result
```

*Source: `src/basic_memory/mcp/tools/move_note.py`*

---

### `delete_note`

Delete a note or directory by title, permalink, or path

Delete a note or directory from the knowledge base.

Permanently removes a note or directory from the specified project. For single notes,
they are identified by title or permalink. For directories, use is_directory=True and
provide the directory path. If the note/directory doesn't exist, the operation returns
False without error. If deletion fails, helpful error messages are provided.

Project Resolution:
Server resolves projects in this order: Single Project Mode → project parameter → default project.
If project unknown, use list_memory_projects() or recent_activity() first.

Returns:
    True if note was successfully deleted, False if note was not found.
    For directories, returns a formatted summary of deleted files.
    On errors, returns a formatted string with helpful troubleshooting guidance.

Raises:
    HTTPError: If project doesn't exist or is inaccessible
    SecurityError: If identifier attempts path traversal

Warning:
    This operation is permanent and cannot be undone. The note/directory files
    will be removed from the filesystem and all references will be lost.

Note:
    If the note is not found, this function provides helpful error messages
    with suggestions for finding the correct identifier, including search
    commands and alternative formats to try.

**Parameters**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `identifier` | `str` | *(required)* | For files: note title or permalink to delete. |
| `is_directory` | `bool` | `False` | If True, deletes an entire directory and all its contents. |
| `project` | `Optional[str]` | `None` | Project name to delete from. Optional - server will resolve using hierarchy. |
| `project_id` | `Optional[str]` | `None` | Project external_id (UUID). Prefer this over `project` when known — |
| `output_format` | `Literal['text', 'json']` | `'text'` | "text" preserves existing behavior (bool/string). "json" |

**Examples**

```python
# Delete by title
    delete_note("Meeting Notes: Project Planning")

    # Delete by permalink
    delete_note("notes/project-planning")

    # Delete with explicit project
    delete_note("experiments/ml-model-results", project="research")

    # Delete entire directory
    delete_note("docs", is_directory=True)

    # Delete nested directory
    delete_note("projects/2024", is_directory=True)

    # Common usage pattern
    if delete_note("old-draft"):
        print("Note deleted successfully")
    else:
        print("Note not found or already deleted")
```

*Source: `src/basic_memory/mcp/tools/delete_note.py`*

---


## Reading & Navigation

### `read_content`

Read a file's raw content by path or permalink

This tool provides direct access to file content in the knowledge base,
handling different file types appropriately. Uses stateless architecture -
project parameter optional with server resolution.

Supported file types:
- Text files (markdown, code, etc.) are returned as plain text
- Images are automatically resized/optimized for display
- Other binary files are returned as base64 if below size limits

Returns:
    A dictionary with the file content and metadata:
    - For text: {"type": "text", "text": "content", "content_type": "text/markdown", "encoding": "utf-8"}
    - For images: {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "base64_data"}}
    - For other files: {"type": "document", "source": {"type": "base64", "media_type": "content_type", "data": "base64_data"}}
    - For errors: {"type": "error", "error": "error message"}

Raises:
    HTTPError: If project doesn't exist or is inaccessible
    SecurityError: If path attempts path traversal

**Parameters**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `path` | `str` | *(required)* | The path or permalink to the file. Can be: |
| `project` | `Optional[str]` | `None` | Project name to read from. Optional - server will resolve using hierarchy. |
| `project_id` | `Optional[str]` | `None` | Project external_id (UUID). Prefer this over `project` when known — |

**Examples**

```python
# Read a markdown file
    result = await read_content("docs/project-specs.md")

    # Read an image
    image_data = await read_content("assets/diagram.png")

    # Read using memory URL
    content = await read_content("memory://docs/architecture")

    # Read configuration file
    config = await read_content("config/settings.json")

    # Explicit project specification
    result = await read_content("docs/project-specs.md", project="my-project")
```

*Source: `src/basic_memory/mcp/tools/read_content.py`*

---

### `build_context`

Build context from a memory:// URI to continue conversations naturally.

    Use this to follow up on previous discussions or explore related topics.

    Memory URL Format:
    - Use paths like "folder/note" or "memory://folder/note"
    - Pattern matching: "folder/*" matches all notes in folder
    - Valid characters: letters, numbers, hyphens, underscores, forward slashes
    - Avoid: double slashes (//), angle brackets (<>), quotes, pipes (|)
    - Examples: "specs/search", "projects/basic-memory", "notes/*"

    Timeframes support natural language like:
    - "2 days ago", "last week", "today", "3 months ago"
    - Or standard formats like "7d", "24h"

    Format options:
    - "json" (default): Structured JSON with internal fields excluded
    - "text": Compact markdown text for LLM consumption

Get context needed to continue a discussion within a specific project.

This tool enables natural continuation of discussions by loading relevant context
from memory:// URIs. It uses pattern matching to find relevant content and builds
a rich context graph of related information.

Project Resolution:
Server resolves projects using a unified priority chain (same in local and cloud modes):
Single Project Mode → project parameter → default project.
Uses default project automatically. Specify `project` parameter to target a different project.

Returns:
    dict (output_format="json"): Structured JSON with internal fields excluded
    str (output_format="text"): Compact markdown representation

Raises:
    ToolError: If project doesn't exist or depth parameter is invalid

**Parameters**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `url` | `MemoryUrl` | *(required)* | memory:// URI pointing to discussion content (e.g. memory://specs/search) |
| `project` | `Optional[str]` | `None` | Project name to build context from. Optional - server will resolve using hierarchy. |
| `project_id` | `Optional[str]` | `None` | Project external_id (UUID). Prefer this over `project` when known — |
| `depth` | `str \| int \| None` | `1` | How many relation hops to traverse (1-3 recommended for performance) |
| `timeframe` | `Optional[TimeFrame]` | `'7d'` | How far back to look. Supports natural language like "2 days ago", "last week" |
| `page` | `int` | `1` | Page number of results to return (default: 1) |
| `page_size` | `int` | `10` | Number of results to return per page (default: 10) |
| `max_related` | `int` | `10` | Maximum number of related results to return (default: 10) |
| `output_format` | `Literal['json', 'text']` | `'json'` | Response format - "json" for structured JSON dict, |

**Examples**

```python
# Continue a specific discussion
    build_context("my-project", "memory://specs/search")

    # Get deeper context about a component
    build_context("work-docs", "memory://components/memory-service", depth=2)

    # Get text output for compact context
    build_context("research", "memory://specs/search", output_format="text")
```

*Source: `src/basic_memory/mcp/tools/build_context.py`*

---

### `recent_activity`

Get recent activity for a project or across all projects.

    Timeframe supports natural language formats like:
    - "2 days ago"
    - "last week"
    - "yesterday"
    - "today"
    - "3 weeks ago"
    Or standard formats like "7d"

Get recent activity for a specific project or across all projects.

Project Resolution:
The server resolves projects in this order:
1. Single Project Mode - server constrained to one project, parameter ignored
2. Explicit project parameter - specify which project to query
3. Default project - server configured default if no project specified

Discovery Mode:
When no specific project can be resolved, returns activity across all projects
to help discover available projects and their recent activity.

Project Discovery (when project is unknown):
1. Call list_memory_projects() to see available projects
2. Or use this tool without project parameter to see cross-project activity
3. Ask the user which project to focus on
4. Remember their choice for the conversation

Returns:
    Human-readable summary of recent activity. When no specific project is
    resolved, returns cross-project discovery information. When a specific
    project is resolved, returns detailed activity for that project.

Raises:
    ToolError: If project doesn't exist or type parameter contains invalid values

Notes:
    - Higher depth values (>3) may impact performance with large result sets
    - For focused queries, consider using build_context with a specific URI
    - Max timeframe is 1 year in the past

**Parameters**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `type` | `Union[str, List[str]]` | `''` | Filter by content type(s). Can be a string or list of strings. |
| `depth` | `int` | `1` | How many relation hops to traverse (1-3 recommended) |
| `timeframe` | `TimeFrame` | `'7d'` | Time window to search. Supports natural language: |
| `page` | `int` | `1` | Page number for pagination (default 1) |
| `page_size` | `int` | `10` | Number of items per page (default 10) |
| `project` | `Optional[str]` | `None` | Project name to query. Optional - server will resolve using the |
| `project_id` | `Optional[str]` | `None` | Project external_id (UUID). Prefer this over `project` when known — |
| `output_format` | `Literal['text', 'json']` | `'text'` | "text" returns human-readable summary text. "json" returns |

**Examples**

```python
# Cross-project discovery mode
    recent_activity()
    recent_activity(timeframe="yesterday")

    # Project-specific activity
    recent_activity(project="work-docs", type="entity", timeframe="yesterday")
    recent_activity(project="research", type=["entity", "relation"], timeframe="today")
    recent_activity(project="notes", type="entity", depth=2, timeframe="2 weeks ago")
```

*Source: `src/basic_memory/mcp/tools/recent_activity.py`*

---

### `list_directory`

List directory contents with filtering and depth control.

List directory contents from the knowledge base with optional filtering.

This tool provides 'ls' functionality for browsing the knowledge base directory structure.
It can list immediate children or recursively explore subdirectories with depth control,
and supports glob pattern filtering for finding specific files.

Returns:
    Formatted listing of directory contents with file metadata

Raises:
    ToolError: If project doesn't exist or directory path is invalid

**Parameters**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `dir_name` | `str` | `'/'` | Directory path to list (default: root "/") |
| `depth` | `int` | `1` | Recursion depth (1-10, default: 1 for immediate children only) |
| `file_name_glob` | `Optional[str]` | `None` | Optional glob pattern for filtering file names |
| `project` | `Optional[str]` | `None` | Project name to list directory from. Optional - server will resolve using hierarchy. |
| `project_id` | `Optional[str]` | `None` | Project external_id (UUID). Prefer this over `project` when known — |

**Examples**

```python
# List root directory contents
    list_directory()

    # List specific folder
    list_directory(dir_name="/projects")

    # Find all markdown files
    list_directory(file_name_glob="*.md")

    # Deep exploration of research folder
    list_directory(dir_name="/research", depth=3)

    # Find meeting notes in projects folder
    list_directory(dir_name="/projects", file_name_glob="*meeting*")

    # Explicit project specification
    list_directory(project="work-docs", dir_name="/projects")
```

*Source: `src/basic_memory/mcp/tools/list_directory.py`*

---


## Search

### `search_notes`

Search across all content in the knowledge base with advanced syntax support.

Search across all content in the knowledge base with comprehensive syntax support.

This tool searches the knowledge base using full-text search, pattern matching,
or exact permalink lookup. It supports filtering by content type, entity type,
and date, with advanced boolean and phrase search capabilities.

Project Resolution:
Server resolves projects in this order: Single Project Mode → project parameter → default project.
If project unknown, use list_memory_projects() or recent_activity() first.
Set search_all_projects=True to search every accessible project; this is opt-in because it
performs one search per project.

## Search Syntax Examples

#### Basic Searches
- `search_notes("my-project", "keyword")` - Find any content containing "keyword"
- `search_notes("work-docs", "'exact phrase'")` - Search for exact phrase match

#### Advanced Boolean Searches
- `search_notes("my-project", "term1 term2")` - Strict implicit-AND first; retries with
  relaxed OR terms only if strict search returns no results
- `search_notes("my-project", "term1 AND term2")` - Explicit AND search (both terms required)
- `search_notes("my-project", "term1 OR term2")` - Either term can be present
- `search_notes("my-project", "term1 NOT term2")` - Include term1 but exclude term2
- `search_notes("my-project", "(project OR planning) AND notes")` - Grouped boolean logic

#### Content-Specific Searches
- `search_notes("research", "tag:example")` - Search within specific tags (if supported by content)
- `search_notes("work-project", "req", entity_types=["observation"], categories=["requirement"])`
  - Return only observations whose category is exactly "requirement"
- `search_notes("team-docs", "author:username")` - Find content by author (if metadata available)

**Note:** `tag:` shorthand is automatically converted to a `tags` filter, so it works
with any search type (text, hybrid, vector). You can also use the `tags` parameter
directly: `search_notes("project", "query", tags=["my-tag"])`

#### Search Type Examples
- `search_notes("my-project", "Meeting", search_type="title")` - Search only in titles
- `search_notes("work-docs", "docs/meeting-*", search_type="permalink")` - Pattern match permalinks
  Note: Permalink patterns match the full path (e.g., "project/folder/chapter-13*", not just "chapter-13*").
- `search_notes("research", "keyword")` - Default search (hybrid when semantic is enabled,
  text when disabled)

#### Filtering Options
- `search_notes("my-project", "query", note_types=["note"])` - Search only notes
- `search_notes("work-docs", "query", note_types=["note", "person"])` - Multiple note types
- `search_notes("research", "query", entity_types=["observation"])` - Filter by entity type
- `search_notes("research", "query", entity_types=["observation"], categories=["requirement"])`
  - Filter observations to an exact category
- `search_notes("team-docs", "query", after_date="2024-01-01")` - Recent content only
- `search_notes("my-project", "query", after_date="1 week")` - Relative date filtering
- `search_notes("my-project", "query", tags=["security"])` - Filter by frontmatter tags
- `search_notes("my-project", "query", status="in-progress")` - Filter by frontmatter status
- `search_notes("my-project", "query", metadata_filters={"priority": {"$in": ["high"]}})`

#### Structured Metadata Filters
Filters are exact matches on frontmatter metadata. Supported forms:
- Equality: `{"status": "in-progress"}`
- Array contains (all): `{"tags": ["security", "oauth"]}`
- Operators:
  - `$in`: `{"priority": {"$in": ["high", "critical"]}}`
  - `$gt`, `$gte`, `$lt`, `$lte`: `{"schema.confidence": {"$gt": 0.7}}`
  - `$between`: `{"schema.confidence": {"$between": [0.3, 0.6]}}`
- Nested keys use dot notation (e.g., `"schema.confidence"`).

#### Filter-only Searches
Omit `query` (or pass None) when only using structured filters:
- `search_notes(metadata_filters={"type": "spec"}, project="my-project")`
- `search_notes(tags=["security"], project="my-project")`
- `search_notes(status="draft", project="my-project")`

#### Convenience Filters
`tags` and `status` are shorthand for metadata_filters. If the same key exists in
metadata_filters, that value wins.

#### Advanced Pattern Examples
- `search_notes("work-project", "project AND (meeting OR discussion)")` - Complex boolean logic
- `search_notes("research", ""exact phrase" AND keyword")` - Combine phrase and keyword search
- `search_notes("dev-notes", "bug NOT fixed")` - Exclude resolved issues
- `search_notes("archive", "docs/2024-*", search_type="permalink")` - Year-based permalink search

Returns:
    Formatted markdown text (output_format="text"), dict (output_format="json"),
    or helpful error guidance string if search fails

**Parameters**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `query` | `Optional[str]` | `None` | Optional search query string (supports boolean operators, phrases, patterns). |
| `project` | `Optional[str]` | `None` | Project name to search in. Optional - server will resolve using hierarchy. |
| `project_id` | `Optional[str]` | `None` | Project external_id (UUID). Prefer this over `project` when known — |
| `search_all_projects` | `bool` | `False` | Optional opt-in to search every accessible project. Ignored when |
| `page` | `int` | `1` | The page number of results to return (default 1) |
| `page_size` | `int` | `10` | The number of results to return per page (default 10) |
| `search_type` | `str \| None` | `None` | Type of search to perform, one of: |
| `output_format` | `Literal['text', 'json']` | `'text'` | "text" preserves existing structured search response behavior. |
| `note_types` | `List[str] \| None` | `None` | Optional list of note types to search (e.g., ["note", "person"]) |
| `entity_types` | `List[str] \| None` | `None` | Optional list of entity types to filter by (e.g., ["entity", "observation"]) |
| `categories` | `List[str] \| None` | `None` | Optional list of observation categories for exact matching (e.g., |
| `after_date` | `Optional[str]` | `None` | Optional date filter for recent content (e.g., "1 week", "2d", "2024-01-01") |
| `metadata_filters` | `Dict[str, Any] \| None` | `None` | Optional structured frontmatter filters (e.g., {"status": "in-progress"}) |
| `tags` | `List[str] \| None` | `None` | Optional tag filter (frontmatter tags); shorthand for metadata_filters["tags"]. |
| `status` | `Optional[str]` | `None` | Optional status filter (frontmatter status); shorthand for metadata_filters["status"] |
| `min_similarity` | `Optional[float]` | `None` | Optional float to override the global semantic_min_similarity threshold |

**Examples**

```python
# Basic text search
    results = await search_notes("project planning")
    # Plain multi-term text uses strict matching first, then relaxed OR fallback if needed

    # Boolean AND search (both terms must be present)
    results = await search_notes("project AND planning")

    # Boolean OR search (either term can be present)
    results = await search_notes("project OR meeting")

    # Boolean NOT search (exclude terms)
    results = await search_notes("project NOT meeting")

    # Boolean search with grouping
    results = await search_notes("(project OR planning) AND notes")

    # Exact phrase search
    results = await search_notes(""weekly standup meeting"")

    # Search with note type filter - type property in frontmatter
    results = await search_notes(
        "meeting notes",
        note_types=["note"],
    )

    # Search with entity type filter
    results = await search_notes(
        "meeting notes",
        entity_types=["observation"],
    )

    # Search for recent content
    results = await search_notes(
        "bug report",
        after_date="1 week"
    )

    # Pattern matching on permalinks
    results = await search_notes(
        "docs/meeting-*",
        search_type="permalink"
    )

    # Title-only search
    results = await search_notes(
        "Machine Learning",
        search_type="title"
    )

    # Complex search with multiple filters
    results = await search_notes(
        "(bug OR issue) AND NOT resolved",
        note_types=["note"],
        after_date="2024-01-01"
    )

    # Explicit project specification
    results = await search_notes("project planning", project="my-project")
```

*Source: `src/basic_memory/mcp/tools/search.py`*

---

<a id="search-tool"></a>

### `search`

Search for content across the knowledge base

ChatGPT/OpenAI MCP search adapter returning a single text content item.

Returns:
    List with one dict: `{ "type": "text", "text": "{...JSON...}" }`
    where the JSON body contains `results`, `total_count`, and echo of `query`.

**Parameters**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `query` | `str` | *(required)* | Search query (full-text syntax supported by `search_notes`) |

*Source: `src/basic_memory/mcp/tools/chatgpt_tools.py`*

---

### `fetch`

Fetch the full contents of a search result document

ChatGPT/OpenAI MCP fetch adapter returning a single text content item.

Returns:
    List with one dict: `{ "type": "text", "text": "{...JSON...}" }`
    where the JSON body includes `id`, `title`, `text`, `url`, and metadata.

**Parameters**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `id` | `str` | *(required)* | Document identifier (permalink, title, or memory URL) |

*Source: `src/basic_memory/mcp/tools/chatgpt_tools.py`*

---


## Project & Workspace Management

### `list_memory_projects`

List all available projects with their status.

Shows projects from both local and cloud sources when cloud credentials
are available, merging by permalink to give a unified view.

Each project entry includes an `external_id` (UUID). Pass that value as the
`project_id` parameter on other tools to address a specific project
unambiguously across cloud workspaces — useful when the same project name
exists in more than one workspace.

**Parameters**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `output_format` | `Literal['text', 'json']` | `'text'` | "text" returns the existing human-readable project list. |

*Source: `src/basic_memory/mcp/tools/project_management.py`*

---

### `create_memory_project`

Create a new Basic Memory project.

Creates a new project with the specified name and path. The project directory
will be created if it doesn't exist. Optionally sets the new project as default.

Returns:
    Confirmation message with project details

Example:
    create_memory_project("my-research", "~/Documents/research")
    create_memory_project("work-notes", "/home/user/work", set_default=True)
    create_memory_project("team-notes", "/team/notes", workspace="team-paul")

**Parameters**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `project_name` | `str` | *(required)* | Name for the new project (must be unique) |
| `project_path` | `str` | *(required)* | File system path where the project will be stored |
| `set_default` | `bool` | `False` | Whether to set this project as the default (optional, defaults to False) |
| `workspace` | `str \| None` | `None` | Optional cloud workspace selector to create the project in. Slug is |
| `output_format` | `Literal['text', 'json']` | `'text'` | "text" returns the existing human-readable result text. |

*Source: `src/basic_memory/mcp/tools/project_management.py`*

---

### `delete_project`

Delete a Basic Memory project.

Removes a project from the configuration and database. This does NOT delete
the actual files on disk - only removes the project from Basic Memory's
configuration and database records.

Returns:
    Confirmation message about project deletion

Example:
    delete_project("old-project")
    delete_project("team-project", workspace="team-paul")

Warning:
    This action cannot be undone. The project will need to be re-added
    to access its content through Basic Memory again.

**Parameters**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `project_name` | `str` | *(required)* | Name of the project to delete |
| `workspace` | `str \| None` | `None` | Optional cloud workspace selector to delete the project from. |

*Source: `src/basic_memory/mcp/tools/project_management.py`*

---

### `list_workspaces`

List available cloud workspaces (tenant_id, type, role, and name).

List workspaces available to the current cloud user.

**Parameters**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `output_format` | `Literal['text', 'json']` | `'text'` | "text" returns human-readable workspace list. |

*Source: `src/basic_memory/mcp/tools/workspaces.py`*

---


## Schema Tools

### `schema_validate`

Validate notes against their Picoschema definitions.

Validate notes against their resolved schema.

Validates a specific note (by identifier) or all notes of a given type.
Returns warnings/errors based on the schema's validation mode.

Schemas are resolved in priority order:
1. Inline schema (dict in frontmatter)
2. Explicit reference (string in frontmatter)
3. Implicit by type (type field matches schema note's entity field)
4. No schema (no validation)

Project Resolution:
Server resolves projects in this order: Single Project Mode -> project parameter -> default.
If project unknown, use list_memory_projects() first.

Returns:
    ValidationReport with per-note results, or error guidance string

**Parameters**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `note_type` | `Optional[str]` | `None` | Note type to batch-validate (e.g., "person", "meeting"). |
| `identifier` | `Optional[str]` | `None` | Specific note to validate (permalink, title, or path). |
| `project` | `Optional[str]` | `None` | Project name. Optional -- server will resolve. |
| `project_id` | `Optional[str]` | `None` | Project external_id (UUID). Prefer this over `project` when known — |
| `output_format` | `Literal['text', 'json']` | `'text'` |  |

**Examples**

```python
# Validate all person notes
    schema_validate(note_type="person")

    # Validate a specific note
    schema_validate(identifier="people/paul-graham")

    # Validate in a specific project
    schema_validate(note_type="person", project="my-research")
```

*Source: `src/basic_memory/mcp/tools/schema.py`*

---

### `schema_infer`

Analyze existing notes and suggest a Picoschema definition.

Analyze existing notes and suggest a schema definition.

Examines observation categories and relation types across all notes
of the given type. Returns frequency analysis and suggested Picoschema
YAML that can be saved as a schema note.

Frequency thresholds:
- 95%+ present -> required field
- threshold+ present -> optional field
- Below threshold -> excluded (but noted)

Project Resolution:
Server resolves projects in this order: Single Project Mode -> project parameter -> default.
If project unknown, use list_memory_projects() first.

Returns:
    InferenceReport with frequency data and suggested schema, or error string

**Parameters**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `note_type` | `str` | *(required)* | The note type to analyze (e.g., "person", "meeting"). |
| `threshold` | `float` | `0.25` | Minimum frequency (0-1) for a field to be suggested as optional. |
| `project` | `Optional[str]` | `None` | Project name. Optional -- server will resolve. |
| `project_id` | `Optional[str]` | `None` | Project external_id (UUID). Prefer this over `project` when known — |
| `output_format` | `Literal['text', 'json']` | `'text'` |  |

**Examples**

```python
# Infer schema for person notes
    schema_infer("person")

    # Use a higher threshold (50% minimum)
    schema_infer("meeting", threshold=0.5)

    # Infer in a specific project
    schema_infer("person", project="my-research")
```

*Source: `src/basic_memory/mcp/tools/schema.py`*

---

### `schema_diff`

Detect drift between a schema definition and actual note usage.

Compares the existing schema for a note type against how notes of
that type are actually structured. Identifies new fields that have
appeared, declared fields that are rarely used, and cardinality changes
(single-value vs array).

Useful for evolving schemas as your knowledge base grows -- run
periodically to see if your schema still matches reality.

Project Resolution:
Server resolves projects in this order: Single Project Mode -> project parameter -> default.
If project unknown, use list_memory_projects() first.

Returns:
    DriftReport with new fields, dropped fields, and cardinality changes,
    or error guidance string

**Parameters**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `note_type` | `str` | *(required)* | The note type to check for drift (e.g., "person"). |
| `project` | `Optional[str]` | `None` | Project name. Optional -- server will resolve. |
| `project_id` | `Optional[str]` | `None` | Project external_id (UUID). Prefer this over `project` when known — |
| `output_format` | `Literal['text', 'json']` | `'text'` |  |

**Examples**

```python
# Check drift for person schema
    schema_diff("person")

    # Check drift in a specific project
    schema_diff("person", project="my-research")
```

*Source: `src/basic_memory/mcp/tools/schema.py`*

---


## Visualization

### `canvas`

Create an Obsidian canvas file to visualize concepts and connections.

Create an Obsidian canvas file with the provided nodes and edges.

This tool creates a .canvas file compatible with Obsidian's Canvas feature,
allowing visualization of relationships between concepts or documents.

Project Resolution:
Server resolves projects in this order: Single Project Mode → project parameter → default project.
If project unknown, use list_memory_projects() or recent_activity() first.

For the full JSON Canvas 1.0 specification, see the 'spec://canvas' resource.

Returns:
    A summary of the created canvas file

Important Notes:
- When referencing files, use the exact file path as shown in Obsidian
  Example: "docs/Document Name.md" (not permalink format)
- For file nodes, the "file" attribute must reference an existing file
- Nodes require id, type, x, y, width, height properties
- Edges require id, fromNode, toNode properties
- Position nodes in a logical layout (x,y coordinates in pixels)
- Use color attributes ("1"-"6" or hex) for visual organization

Basic Structure:
```json
{
  "nodes": [
    {
      "id": "node1",
      "type": "file",  // Options: "file", "text", "link", "group"
      "file": "docs/Document.md",
      "x": 0,
      "y": 0,
      "width": 400,
      "height": 300
    }
  ],
  "edges": [
    {
      "id": "edge1",
      "fromNode": "node1",
      "toNode": "node2",
      "label": "connects to"
    }
  ]
}
```

Raises:
    ToolError: If project doesn't exist or directory path is invalid

**Parameters**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `nodes` | `List[Dict[str, Any]]` | *(required)* | List of node objects following JSON Canvas 1.0 spec |
| `edges` | `List[Dict[str, Any]]` | *(required)* | List of edge objects following JSON Canvas 1.0 spec |
| `title` | `str` | *(required)* | The title of the canvas (will be saved as title.canvas) |
| `directory` | `str` | *(required)* | Directory path relative to project root where the canvas should be saved. |
| `project` | `Optional[str]` | `None` | Project name to create canvas in. Optional - server will resolve using hierarchy. |
| `project_id` | `Optional[str]` | `None` | Project external_id (UUID). Prefer this over `project` when known — |

**Examples**

```python
# Create canvas in default/current project
    canvas(nodes=[...], edges=[...], title="My Canvas", directory="diagrams")

    # Create canvas with explicit project
    canvas(nodes=[...], edges=[...], title="Process Flow", directory="visual/maps", project="work-project")
```

*Source: `src/basic_memory/mcp/tools/canvas.py`*

---


## Info & Utilities

### `cloud_info`

Return optional Basic Memory Cloud information and setup guidance.

*Source: `src/basic_memory/mcp/tools/cloud_info.py`*

---

### `release_notes`

Return the latest product release notes for optional user review.

*Source: `src/basic_memory/mcp/tools/release_notes.py`*

---

