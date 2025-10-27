# Basic Memory MCP Tool Usage Guide

This document provides comprehensive documentation and usage examples for all Basic Memory MCP tools. Use this as a reference when creating instructions for your LLM or integrating Basic Memory into your workflows.

**Total Tools:** 16 core tools across 5 categories

---

## Table of Contents

### Content Management
- [write_note](#write_note)
- [read_note](#read_note)
- [read_content](#read_content)
- [view_note](#view_note)
- [edit_note](#edit_note)
- [move_note](#move_note)
- [delete_note](#delete_note)

### Knowledge Graph Navigation
- [build_context](#build_context)
- [recent_activity](#recent_activity)
- [list_directory](#list_directory)

### Search & Discovery
- [search_notes](#search_notes)

### Project Management
- [list_memory_projects](#list_memory_projects)
- [create_memory_project](#create_memory_project)
- [delete_project](#delete_project)
- [get_current_project](#get_current_project)
- [sync_status](#sync_status)

### Visualization
- [canvas](#canvas)

---

## Content Management

### write_note

**Create or update a markdown note. Returns a markdown formatted summary of the semantic content.**

#### Function Signature
```python
write_note(title: str, content: str, folder: str, project: Optional[str] = None,
           tags: Union[List[str], str, None] = None, entity_type: str = "note")
```

#### Description

Creates or updates a markdown note with semantic observations and relations. The content can include:

**Observations format:**
```markdown
- [category] Observation text #tag1 #tag2 (optional context)
```

Examples:
```markdown
- [design] Files are the source of truth #architecture (All state comes from files)
- [tech] Using SQLite for storage #implementation
- [note] Need to add error handling #todo
```

**Relations format:**
- Explicit: `- relation_type [[Entity]] (optional context)`
- Inline: Any `[[Entity]]` reference creates a relation

Examples:
```markdown
- depends_on [[Content Parser]] (Need for semantic extraction)
- implements [[Search Spec]] (Initial implementation)
- This feature extends [[Base Design]] and uses [[Core Utils]]
```

#### Parameters

- **title**: The title of the note
- **content**: Markdown content for the note, can include observations and relations
- **folder**: Folder path relative to project root where the file should be saved (use "/" or "" for root)
- **project**: Project name to write to (optional - server will resolve)
- **tags**: Tags to categorize the note (list of strings or comma-separated string)
- **entity_type**: Type of entity to create (default: "note", can be "guide", "report", "config", etc.)

#### Returns

A markdown formatted summary including:
- Creation/update status with project name
- File path and checksum
- Observation counts by category
- Relation counts (resolved/unresolved)
- Tags if present

#### Examples

```python
# Create a simple note
write_note(
    project="my-research",
    title="Meeting Notes",
    folder="meetings",
    content="# Weekly Standup\n\n- [decision] Use SQLite for storage #tech"
)

# Create a note with tags and entity type
write_note(
    project="work-project",
    title="API Design",
    folder="specs",
    content="# REST API Specification\n\n- implements [[Authentication]]",
    tags=["api", "design"],
    entity_type="guide"
)

# Update existing note (same title/folder)
write_note(
    project="my-research",
    title="Meeting Notes",
    folder="meetings",
    content="# Weekly Standup\n\n- [decision] Use PostgreSQL instead #tech"
)

# Write to project root
write_note(
    project="notes",
    title="Quick Note",
    folder="/",  # or "" for root
    content="# Quick Note\n\nSome quick thoughts..."
)
```

---

### read_note

**Read a markdown note by title or permalink.**

#### Function Signature
```python
read_note(identifier: str, project: Optional[str] = None, page: int = 1,
          page_size: int = 10)
```

#### Description

Returns the raw markdown for a note, or guidance text if no match is found. This tool tries multiple lookup strategies:
1. Direct permalink lookup
2. Title search fallback
3. Text search as last resort

#### Parameters

- **identifier**: The title, permalink, or memory:// URL of the note to read
- **project**: Project name to read from (optional - server will resolve)
- **page**: Page number for paginated results (default: 1)
- **page_size**: Number of items per page (default: 10)

#### Returns

The full markdown content of the note including frontmatter, observations, relations, and all formatting.

#### Examples

```python
# Read by permalink
read_note("my-research", "specs/search-spec")

# Read by title
read_note("work-project", "Search Specification")

# Read with memory URL
read_note("my-research", "memory://specs/search-spec")

# Read with pagination
read_note("work-project", "Project Updates", page=2, page_size=5)

# Read recent meeting notes
read_note("team-docs", "Weekly Standup")
```

---

### read_content

**Read raw file content directly without knowledge graph processing.**

#### Function Signature
```python
read_content(path: str, project: Optional[str] = None)
```

#### Description

Reads file content directly, supporting various file types including text, images, and other binary files. Unlike `read_note`, this tool:
- Does not process markdown semantics
- Supports images (automatically optimized for viewing)
- Supports any text or binary file

For images, the tool automatically:
- Resizes large images while maintaining aspect ratio
- Optimizes quality to stay under size limits
- Returns base64-encoded image data

#### Parameters

- **path**: File path relative to project root
- **project**: Project name to read from (optional - server will resolve)

#### Returns

For text files: The raw file content as a string
For images: Optimized image data with dimensions and format information

#### Examples

```python
# Read a text file
read_content("config/settings.json")

# Read an image
read_content("diagrams/architecture.png")

# Read from specific project
read_content("docs/readme.txt", project="work-docs")
```

---

### view_note

**View a note as a formatted artifact for better readability.**

#### Function Signature
```python
view_note(identifier: str, project: Optional[str] = None, page: int = 1,
          page_size: int = 10)
```

#### Description

This tool reads a note using the same logic as `read_note` but instructs Claude to display the content as a markdown artifact in the Claude Desktop app for better readability.

#### Parameters

Same as `read_note`:
- **identifier**: The title or permalink of the note to view
- **project**: Project name (optional)
- **page**: Page number (default: 1)
- **page_size**: Items per page (default: 10)

#### Examples

```python
# View a note by title
view_note("Meeting Notes")

# View a note by permalink
view_note("meetings/weekly-standup")

# View with pagination
view_note("large-document", page=2, page_size=5)

# Explicit project specification
view_note("Meeting Notes", project="my-project")
```

---

### edit_note

**Edit an existing markdown note using various operations like append, prepend, find_replace, or replace_section.**

#### Function Signature
```python
edit_note(identifier: str, operation: str, content: str, project: Optional[str] = None,
          section: Optional[str] = None, find_text: Optional[str] = None,
          expected_replacements: int = 1)
```

#### Description

Makes targeted changes to existing notes without rewriting the entire content. Supports four operations:
- **append**: Add content to the end
- **prepend**: Add content to the beginning (frontmatter-aware)
- **find_replace**: Replace occurrences of text
- **replace_section**: Replace content under a markdown header

#### Parameters

- **identifier**: Exact title, permalink, or memory:// URL (must match exactly)
- **operation**: One of: "append", "prepend", "find_replace", "replace_section"
- **content**: The content to add or use for replacement
- **project**: Project name (optional)
- **section**: For replace_section - the markdown header (e.g., "## Notes")
- **find_text**: For find_replace - the text to find and replace
- **expected_replacements**: For find_replace - expected number of replacements (validates count)

#### Returns

A markdown formatted summary of the edit operation including file path, operation details, and updated semantic content.

#### Examples

```python
# Add new content to end of note
edit_note("my-project", "project-planning", "append",
          "\n## New Requirements\n- Feature X\n- Feature Y")

# Add timestamp at beginning (frontmatter-aware)
edit_note("work-docs", "meeting-notes", "prepend",
          "## 2025-05-25 Update\n- Progress update...\n\n")

# Update version number (single occurrence)
edit_note("api-project", "config-spec", "find_replace", "v0.13.0",
          find_text="v0.12.0")

# Update version in multiple places with validation
edit_note("docs-project", "api-docs", "find_replace", "v2.1.0",
          find_text="v2.0.0", expected_replacements=3)

# Replace implementation section
edit_note("specs", "api-spec", "replace_section",
          "New implementation approach...\n", section="## Implementation")

# Replace subsection with more specific header
edit_note("docs", "docs/setup", "replace_section",
          "Updated install steps\n", section="### Installation")

# Update status across document (expecting exactly 2 occurrences)
edit_note("reports", "status-report", "find_replace", "In Progress",
          find_text="Not Started", expected_replacements=2)
```

---

### move_note

**Move a note to a new location within the same project.**

#### Function Signature
```python
move_note(identifier: str, destination_path: str, project: Optional[str] = None)
```

#### Description

Moves notes to new locations, updating the database and maintaining links. Cross-project moves are not supported - use read + write workflow for that.

#### Parameters

- **identifier**: The exact title, permalink, or memory:// URL of the note to move
- **destination_path**: New file path relative to project root
- **project**: Project name (optional)

#### Returns

Confirmation message with old and new paths.

#### Examples

```python
# Move note to different folder
move_note("meeting-notes", "archive/meetings/2024/meeting-notes.md")

# Reorganize structure
move_note("specs/old-spec", "archive/specs/deprecated/old-spec.md")

# With explicit project
move_note("docs/guide", "documentation/user-guides/guide.md", project="work-project")
```

**Note:** For cross-project moves, use this workflow:
```python
# 1. Read from source project
content = read_note("source-project", "note-to-move")

# 2. Write to destination project
write_note("dest-project", "Note Title", content, "new-folder")

# 3. Delete from source (optional)
delete_note("source-project", "note-to-move")
```

---

### delete_note

**Delete a note from the knowledge base.**

#### Function Signature
```python
delete_note(identifier: str, project: Optional[str] = None)
```

#### Description

Permanently deletes a note from both the database and filesystem. This action cannot be undone.

#### Parameters

- **identifier**: The exact title, permalink, or memory:// URL of the note to delete
- **project**: Project name (optional)

#### Returns

Confirmation message with details of deleted entities, relations, and observations.

#### Examples

```python
# Delete by permalink
delete_note("old-notes/deprecated-spec")

# Delete by title
delete_note("Obsolete Meeting Notes")

# With explicit project
delete_note("archive/old-file", project="work-docs")
```

**Warning:** This permanently deletes the file and all associated database records. Use with caution.

---

## Knowledge Graph Navigation

### build_context

**Build context from a memory:// URI to continue conversations naturally.**

#### Function Signature
```python
build_context(url: str, project: Optional[str] = None, depth: int | str = 1,
              timeframe: str = "7d", page: int = 1, page_size: int = 10,
              max_related: int = 10)
```

#### Description

Get context needed to continue a discussion by loading relevant context from memory:// URIs. Uses pattern matching to find relevant content and builds a rich context graph of related information.

**Memory URL Format:**
- Use paths like "folder/note" or "memory://folder/note"
- Pattern matching: "folder/*" matches all notes in folder
- Valid characters: letters, numbers, hyphens, underscores, forward slashes
- Examples: "specs/search", "projects/basic-memory", "notes/*"

**Timeframes** support natural language:
- "2 days ago", "last week", "today", "3 months ago"
- Or standard formats: "7d", "24h"

#### Parameters

- **url**: memory:// URI pointing to discussion content
- **project**: Project name (optional)
- **depth**: How many relation hops to traverse (1-3 recommended)
- **timeframe**: How far back to look (default: "7d")
- **page**: Page number (default: 1)
- **page_size**: Number of results per page (default: 10)
- **max_related**: Maximum number of related results (default: 10)

#### Returns

GraphContext containing primary_results, related_results, and metadata.

#### Examples

```python
# Continue a specific discussion
build_context("my-project", "memory://specs/search")

# Get deeper context about a component
build_context("work-docs", "memory://components/memory-service", depth=2)

# Look at recent changes to a specification
build_context("research", "memory://specs/document-format", timeframe="today")

# Research the history of a feature
build_context("dev-notes", "memory://features/knowledge-graph",
              timeframe="3 months ago")

# Pattern matching for folder
build_context("my-project", "memory://meetings/*", timeframe="last week")
```

---

### recent_activity

**Get recent activity for a project or across all projects.**

#### Function Signature
```python
recent_activity(type: str | List[str] = "", depth: int = 1,
                timeframe: str = "7d", project: Optional[str] = None)
```

#### Description

Shows recently updated information. When project is unknown, returns cross-project discovery information. When specific project is known, returns detailed activity for that project.

**Timeframes** support natural language:
- "2 days ago", "last week", "yesterday", "today"
- Or standard formats: "7d", "24h"

#### Parameters

- **type**: Filter by content type(s) - "entity", "relation", "observation", or list like ["entity", "relation"]
- **depth**: How many relation hops to traverse (1-3 recommended)
- **timeframe**: Time window to search (default: "7d")
- **project**: Project name (optional)

#### Returns

Human-readable summary of recent activity.

#### Examples

```python
# Cross-project discovery mode
recent_activity()
recent_activity(timeframe="yesterday")

# Project-specific activity
recent_activity(project="work-docs", type="entity", timeframe="yesterday")
recent_activity(project="research", type=["entity", "relation"], timeframe="today")
recent_activity(project="notes", type="entity", depth=2, timeframe="2 weeks ago")

# See all recent changes
recent_activity(project="my-project", timeframe="last week")

# Focus on entities only
recent_activity(project="work-docs", type="entity", timeframe="3 days ago")
```

---

### list_directory

**List directory contents with filtering and depth control.**

#### Function Signature
```python
list_directory(dir_name: str = "/", depth: int = 1,
               file_name_glob: Optional[str] = None, project: Optional[str] = None)
```

#### Description

Provides 'ls' functionality for browsing the knowledge base directory structure. Can list immediate children or recursively explore subdirectories with depth control, and supports glob pattern filtering.

#### Parameters

- **dir_name**: Directory path to list (default: root "/")
- **depth**: Recursion depth 1-10 (default: 1 for immediate children only)
- **file_name_glob**: Optional glob pattern for filtering (e.g., "*.md", "*meeting*")
- **project**: Project name (optional)

#### Returns

Formatted listing of directory contents with file metadata.

#### Examples

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

# Browse subdirectories recursively
list_directory(dir_name="/specs", depth=2)
```

---

## Search & Discovery

### search_notes

**Search across all content in the knowledge base with advanced syntax support.**

#### Function Signature
```python
search_notes(query: str, project: Optional[str] = None, page: int = 1,
             page_size: int = 10, search_type: str = "text",
             types: Optional[List[str]] = None, entity_types: Optional[List[str]] = None,
             after_date: Optional[str] = None)
```

#### Description

Full-text search with support for boolean operators, phrase matching, pattern matching, and filtering.

**Search Types:**
- **text**: Full-text search (default)
- **title**: Search only in titles
- **permalink**: Pattern match permalinks

#### Search Syntax

**Basic Searches:**
```python
search_notes("my-project", "keyword")              # Any content containing "keyword"
search_notes("work-docs", "'exact phrase'")        # Exact phrase match
```

**Boolean Searches:**
```python
search_notes("my-project", "term1 term2")          # Both terms (implicit AND)
search_notes("my-project", "term1 AND term2")      # Explicit AND
search_notes("my-project", "term1 OR term2")       # Either term
search_notes("my-project", "term1 NOT term2")      # Include term1, exclude term2
search_notes("my-project", "(project OR planning) AND notes")  # Grouped logic
```

**Content-Specific Searches:**
```python
search_notes("research", "tag:example")            # Search within tags
search_notes("work-project", "category:observation")  # Filter by category
search_notes("team-docs", "author:username")       # By author (if metadata available)
```

**Search Type Examples:**
```python
search_notes("my-project", "Meeting", search_type="title")  # Title only
search_notes("work-docs", "docs/meeting-*", search_type="permalink")  # Permalink pattern
search_notes("research", "keyword", search_type="text")  # Full-text (default)
```

**Filtering Options:**
```python
search_notes("my-project", "query", types=["entity"])  # Search only entities
search_notes("work-docs", "query", types=["note", "person"])  # Multiple types
search_notes("research", "query", entity_types=["observation"])  # By entity type
search_notes("team-docs", "query", after_date="2024-01-01")  # Recent only
search_notes("my-project", "query", after_date="1 week")  # Relative dates
```

**Advanced Patterns:**
```python
# Complex boolean logic
search_notes("work-project", "project AND (meeting OR discussion)")

# Combine phrase and keyword
search_notes("research", '"exact phrase" AND keyword')

# Exclude resolved issues
search_notes("dev-notes", "bug NOT fixed")

# Year-based permalink search
search_notes("archive", "docs/2024-*", search_type="permalink")
```

#### Parameters

- **query**: Search query string (supports boolean operators, phrases, patterns)
- **project**: Project name (optional)
- **page**: Page number of results (default: 1)
- **page_size**: Number of results per page (default: 10)
- **search_type**: "text", "title", or "permalink" (default: "text")
- **types**: Optional list of note types (e.g., ["note", "person"])
- **entity_types**: Optional list of entity types (e.g., ["entity", "observation"])
- **after_date**: Optional date filter (e.g., "1 week", "2024-01-01")

#### Returns

SearchResponse with results and pagination info, or helpful error guidance if search fails.

#### Examples

```python
# Basic text search
search_notes("project planning")

# Boolean AND search
search_notes("project AND planning")

# Boolean OR search
search_notes("project OR meeting")

# Boolean NOT search
search_notes("project NOT meeting")

# Boolean with grouping
search_notes("(project OR planning) AND notes")

# Exact phrase search
search_notes('"weekly standup meeting"')

# Search with type filter
search_notes("meeting notes", types=["entity"])

# Search with entity type filter
search_notes("meeting notes", entity_types=["observation"])

# Search for recent content
search_notes("bug report", after_date="1 week")

# Pattern matching on permalinks
search_notes("docs/meeting-*", search_type="permalink")

# Title-only search
search_notes("Machine Learning", search_type="title")

# Complex search with filters
search_notes("(bug OR issue) AND NOT resolved", types=["entity"],
             after_date="2024-01-01")

# Explicit project specification
search_notes("project planning", project="my-project")
```

---

## Project Management

### list_memory_projects

**List all available projects with their status.**

#### Function Signature
```python
list_memory_projects()
```

#### Description

Shows all Basic Memory projects available for MCP operations. Use this to discover projects when you need to know which project to use.

**Use this tool:**
- At conversation start when project is unknown
- When user asks about available projects
- Before any operation requiring a project

**After calling:**
- Ask user which project to use
- Remember their choice for the session

#### Returns

Formatted list of projects with session management guidance.

#### Example

```python
list_memory_projects()
```

---

### create_memory_project

**Create a new Basic Memory project.**

#### Function Signature
```python
create_memory_project(project_name: str, project_path: str, set_default: bool = False)
```

#### Description

Creates a new project with the specified name and path. The project directory will be created if it doesn't exist. Optionally sets the new project as default.

#### Parameters

- **project_name**: Name for the new project (must be unique)
- **project_path**: File system path where the project will be stored
- **set_default**: Whether to set this project as the default (optional, defaults to False)

#### Returns

Confirmation message with project details.

#### Examples

```python
create_memory_project("my-research", "~/Documents/research")
create_memory_project("work-notes", "/home/user/work", set_default=True)
```

---

### delete_project

**Delete a Basic Memory project.**

#### Function Signature
```python
delete_project(project_name: str)
```

#### Description

Removes a project from the configuration and database. This does NOT delete the actual files on disk - only removes the project from Basic Memory's configuration and database records.

#### Parameters

- **project_name**: Name of the project to delete

#### Returns

Confirmation message about project deletion.

#### Example

```python
delete_project("old-project")
```

**Warning:** This action cannot be undone. The project will need to be re-added to access its content through Basic Memory again.

---

### get_current_project

**Get information about the current active project.**

#### Function Signature
```python
get_current_project()
```

#### Description

Returns detailed information about the currently active project including name, path, and statistics.

#### Returns

Project information with stats (entity count, relation count, etc.)

#### Example

```python
get_current_project()
```

---

### sync_status

**Check file synchronization and background operation status.**

#### Function Signature
```python
sync_status(project: Optional[str] = None)
```

#### Description

Returns status information about file synchronization, including pending operations, sync progress, and any background tasks.

#### Parameters

- **project**: Project name (optional)

#### Returns

Status information including sync state, pending operations, and background task progress.

#### Examples

```python
# Check current project sync status
sync_status()

# Check specific project
sync_status(project="work-docs")
```

---

## Visualization

### canvas

**Create an Obsidian canvas file to visualize concepts and connections.**

#### Function Signature
```python
canvas(nodes: List[Dict], edges: List[Dict], title: str, folder: str,
       project: Optional[str] = None)
```

#### Description

Creates a .canvas file compatible with Obsidian's Canvas feature, allowing visualization of relationships between concepts or documents.

**Node Types:**
- **file**: References an existing note
- **text**: Contains text content
- **link**: External URL
- **group**: Groups other nodes

**Important Notes:**
- When referencing files, use exact file path as shown in Obsidian (e.g., "folder/Document Name.md")
- Nodes require: id, type, x, y, width, height
- Edges require: id, fromNode, toNode
- Position nodes in logical layout (x,y coordinates in pixels)
- Use color attributes ("1"-"6" or hex) for visual organization

#### Parameters

- **nodes**: List of node objects following JSON Canvas 1.0 spec
- **edges**: List of edge objects following JSON Canvas 1.0 spec
- **title**: The title of the canvas (saved as title.canvas)
- **folder**: Folder path relative to project root
- **project**: Project name (optional)

#### Returns

Summary of the created canvas file.

#### Basic Structure

```json
{
  "nodes": [
    {
      "id": "node1",
      "type": "file",
      "file": "folder/Document.md",
      "x": 0,
      "y": 0,
      "width": 400,
      "height": 300
    },
    {
      "id": "node2",
      "type": "text",
      "text": "Some text content",
      "x": 500,
      "y": 0,
      "width": 300,
      "height": 200
    }
  ],
  "edges": [
    {
      "id": "edge1",
      "fromNode": "node1",
      "toNode": "node2",
      "label": "relates to"
    }
  ]
}
```

#### Examples

```python
# Simple two-node canvas
canvas(
    nodes=[
        {
            "id": "1",
            "type": "file",
            "file": "specs/API Spec.md",
            "x": 0,
            "y": 0,
            "width": 400,
            "height": 300
        },
        {
            "id": "2",
            "type": "text",
            "text": "Implementation Notes",
            "x": 500,
            "y": 0,
            "width": 300,
            "height": 200
        }
    ],
    edges=[
        {
            "id": "e1",
            "fromNode": "1",
            "toNode": "2",
            "label": "implemented by"
        }
    ],
    title="API Architecture",
    folder="diagrams"
)

# Complex knowledge graph
canvas(
    nodes=[
        {"id": "concept1", "type": "file", "file": "concepts/Core Idea.md",
         "x": 200, "y": 0, "width": 400, "height": 300, "color": "1"},
        {"id": "concept2", "type": "file", "file": "concepts/Related Concept.md",
         "x": 700, "y": 0, "width": 400, "height": 300, "color": "2"},
        {"id": "impl", "type": "file", "file": "implementation/Code.md",
         "x": 400, "y": 400, "width": 400, "height": 300, "color": "3"}
    ],
    edges=[
        {"id": "e1", "fromNode": "concept1", "toNode": "concept2", "label": "extends"},
        {"id": "e2", "fromNode": "concept1", "toNode": "impl", "label": "implemented by"},
        {"id": "e3", "fromNode": "concept2", "toNode": "impl", "label": "uses"}
    ],
    title="Project Architecture",
    folder="visual/maps",
    project="work-project"
)
```

---

## Project Resolution

Most tools support optional `project` parameter with this resolution hierarchy:

1. **Single Project Mode**: Server constrained to one project (parameter ignored)
2. **Explicit project parameter**: Specify which project to use
3. **Default project**: Server configured default if no project specified

**Discovery Mode**: When project is unknown, use:
1. `list_memory_projects()` to see available projects
2. `recent_activity()` without project to see cross-project activity
3. Ask user which project to focus on
4. Remember their choice for the conversation

---

## Additional Resources

- [Basic Memory README](../README.md)
- [CLAUDE.md Project Guide](../CLAUDE.md)
- [MCP Server Implementation](../src/basic_memory/mcp/)
- [Basic Memory Documentation](https://docs.basicmachines.co)

---

## Common Workflows

### Starting a Conversation

```python
# 1. Discover available projects
projects = list_memory_projects()

# 2. See recent activity
activity = recent_activity(timeframe="last week")

# 3. Ask user which project to use
# User: "Let's work with my-research"

# 4. Remember for session and begin work
write_note(project="my-research", ...)
```

### Creating Knowledge

```python
# 1. Write a note with observations and relations
write_note(
    project="research",
    title="Machine Learning Overview",
    folder="ml",
    content="""
    # Machine Learning Overview

    - [concept] ML enables computers to learn from data #ai
    - [tech] Common frameworks include TensorFlow and PyTorch #implementation

    ## Relations
    - implements [[AI Principles]]
    - uses [[Statistical Methods]]
    """
)

# 2. Create related notes
write_note(
    project="research",
    title="AI Principles",
    folder="ml",
    content="# AI Principles\n\n- [design] Intelligence from data patterns"
)

# 3. Visualize relationships
canvas(
    nodes=[
        {"id": "1", "type": "file", "file": "ml/Machine Learning Overview.md",
         "x": 0, "y": 0, "width": 400, "height": 300},
        {"id": "2", "type": "file", "file": "ml/AI Principles.md",
         "x": 500, "y": 0, "width": 400, "height": 300}
    ],
    edges=[
        {"id": "e1", "fromNode": "1", "toNode": "2", "label": "implements"}
    ],
    title="ML Knowledge Graph",
    folder="diagrams"
)
```

### Continuing Conversations

```python
# 1. Build context from previous discussion
context = build_context(
    url="memory://specs/search-feature",
    depth=2,
    timeframe="1 week"
)

# 2. Review recent changes
activity = recent_activity(
    type="entity",
    timeframe="2 days ago"
)

# 3. Search for related topics
results = search_notes(
    query="search AND implementation",
    after_date="1 week"
)

# 4. Continue with updates
edit_note(
    identifier="specs/search-feature",
    operation="append",
    content="\n## New Implementation Notes\n- Added caching layer\n"
)
```

### Searching and Exploring

```python
# Broad exploration
list_directory(dir_name="/", depth=2)

# Focused search
search_notes("machine learning AND python", types=["entity"])

# Pattern finding
search_notes("docs/2024-*", search_type="permalink")

# Recent relevant content
search_notes("deployment", after_date="1 week")

# Cross-project discovery
recent_activity(timeframe="yesterday")
```

---

*This documentation was manually created from the MCP tool source code.*
*For the latest tool documentation, regenerate using: `python scripts/generate_tool_docs.py`*
*Last updated: 2025-10-27*
