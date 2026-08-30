# Basic Memory MCP Tool Reference

> **Auto-generated** by `scripts/generate_tool_docs.py`. Do not edit by hand.
>
> Regenerate with: `uv run scripts/generate_tool_docs.py`

This reference documents all **21** MCP tools registered by Basic Memory. Each entry lists the tool's purpose and its parameters (types, whether they are required, defaults, and descriptions).

## Table of Contents

- [Note Management](#note-management)
  - [`delete_note`](#delete_note)
  - [`edit_note`](#edit_note)
  - [`move_note`](#move_note)
  - [`write_note`](#write_note)
- [Reading & Navigation](#reading--navigation)
  - [`build_context`](#build_context)
  - [`list_directory`](#list_directory)
  - [`read_content`](#read_content)
  - [`read_note`](#read_note)
  - [`recent_activity`](#recent_activity)
  - [`view_note`](#view_note)
- [Search](#search)
  - [`fetch`](#fetch)
  - [`search`](#search)
  - [`search_notes`](#search_notes)
- [Project & Workspace Management](#project--workspace-management)
  - [`create_memory_project`](#create_memory_project)
  - [`delete_project`](#delete_project)
  - [`list_memory_projects`](#list_memory_projects)
  - [`list_workspaces`](#list_workspaces)
- [Schema Tools](#schema-tools)
  - [`schema_diff`](#schema_diff)
  - [`schema_infer`](#schema_infer)
  - [`schema_validate`](#schema_validate)
- [Diagnostics](#diagnostics)
  - [`basic_memory_diagnostics`](#basic_memory_diagnostics)

## Note Management

### `delete_note`

Delete a note or directory from the knowledge base.

Permanently removes a note or directory from the specified project. For single notes,
they are identified by title or permalink. For directories, use is_directory=True and
provide the directory path. If the note/directory doesn't exist, the operation returns
False without error. If deletion fails, helpful error messages are provided.

Project Resolution:
Server resolves projects in this order: Single Project Mode → project parameter → default project.
If project unknown, use list_memory_projects() or recent_activity() first.

**Parameters:**

| Parameter | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `identifier` | `str` | Yes |  | For files: note title or permalink to delete. For directories: the directory path (e.g., "docs", "projects/2025"). Can be a title like "Meeting Notes" or permalink like "notes/meeting-notes" |
| `is_directory` | `bool` | No | `False` | If True, deletes an entire directory and all its contents. When True, identifier should be a directory path (without file extensions). Defaults to False. |
| `project` | `Optional[str]` | No | `None` | Project name to delete from. Optional - server will resolve using hierarchy. If unknown, use list_memory_projects() to discover available projects. |
| `project_id` | `Optional[str]` | No | `None` | Project external_id (UUID). Prefer this over `project` when known — it routes to the exact project regardless of name collisions across cloud workspaces. Takes precedence over `project`. Get from list_memory_projects(). |
| `output_format` | `Literal['text', 'json']` | No | `'text'` | "text" preserves existing behavior (bool/string). "json" returns machine-readable deletion metadata. |

_Source: `src/basic_memory/mcp/tools/delete_note.py`_

### `edit_note`

Edit an existing markdown note in the knowledge base.

Makes targeted changes to existing notes without rewriting the entire content.

Project Resolution:
Server resolves projects in this order: Single Project Mode → project parameter → default project.
If project unknown, use list_memory_projects() or recent_activity() first.

**Parameters:**

| Parameter | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `identifier` | `str` | Yes |  | The exact title, permalink, or memory:// URL of the note to edit. Must be an exact match - fuzzy matching is not supported for edit operations. Use search_notes() or read_note() first to find the correct identifier if uncertain. |
| `operation` | `str` | Yes |  | The editing operation to perform: - "append": Add content to the end of the note (creates the note if it doesn't exist) - "prepend": Add content to the beginning of the note (creates the note if it doesn't exist) - "find_replace": Replace occurrences of find_text with content (note must exist) - "replace_section": Replace a markdown section identified by its header (note must exist). By default the section spans through the next heading of the same or higher level, so its subsections are replaced too; see replace_subsections. - "insert_before_section": Insert content before a section heading without consuming it (note must exist) - "insert_after_section": Insert content after a section heading without consuming it (note must exist) |
| `content` | `str` | Yes |  | The content to add or use for replacement |
| `project` | `Optional[str]` | No | `None` | Project name to edit in. Optional - server will resolve using hierarchy. Use "workspace/project" to route to a project in a specific cloud workspace. If unknown, use list_memory_projects() to discover available projects. |
| `workspace` | `Optional[str]` | No | `None` | Workspace slug, name, or tenant_id. When provided with `project`, routes as `workspace/project`. Cannot be combined with `project_id`. |
| `project_id` | `Optional[str]` | No | `None` | Project external_id (UUID). Prefer this over `project` when known — it routes to the exact project regardless of name collisions across cloud workspaces. Takes precedence over `project`. Get from list_memory_projects(). |
| `section` | `Optional[str]` | No | `None` | For replace_section operation - the markdown header to replace content under (e.g., "## Notes", "### Implementation") |
| `find_text` | `Optional[str]` | No | `None` | For find_replace operation - the text to find and replace |
| `expected_replacements` | `Optional[int]` | No | `None` | For find_replace operation - the expected number of replacements (validation will fail if actual doesn't match) |
| `replace_subsections` | `Optional[bool]` | No | `None` | For replace_section operation. Default (true): the section spans everything through the next heading of the same or higher level in the original note, so replacing "## Section" also replaces its "###" subsections — the replacement content may freely introduce new headings. Set to false to replace only the immediate content under the header, stopping at the next heading of any level and preserving subsections. |
| `metadata` | `Optional[dict[str, Any]]` | No | `None` | Optional dict of frontmatter fields to merge, independent of `operation`. Provided keys overwrite existing frontmatter values (or are added if new); unrelated frontmatter keys and the note body are left untouched. Can be combined with any operation in the same call. `title`, `type`, and `permalink` are ignored since those have their own dedicated handling. Key deletion is not supported. |
| `output_format` | `Literal['text', 'json']` | No | `'text'` | "text" returns the existing markdown summary. "json" returns machine-readable edit metadata. |

_Source: `src/basic_memory/mcp/tools/edit_note.py`_

### `move_note`

Move a note or directory to a new location within the same project.

Moves a note or directory from one location to another within the project,
updating all database references and maintaining semantic content. Uses stateless
architecture - project parameter optional with server resolution.

**Parameters:**

| Parameter | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `identifier` | `str` | Yes |  | For files: exact entity identifier (title, permalink, or memory:// URL). For directories: the directory path (e.g., "docs", "projects/2025"). Must be an exact match - fuzzy matching is not supported for move operations. Use search_notes() or list_directory() first to find the correct path if uncertain. |
| `destination_path` | `str` | No | `''` | For files: new path relative to project root (e.g., "work/meetings/note.md") For directories: new directory path (e.g., "archive/docs") Mutually exclusive with destination_folder. |
| `destination_folder` | `Optional[str]` | No | `None` | Move the note into this folder, preserving the original filename. Mutually exclusive with destination_path. Only for single-file moves. |
| `is_directory` | `bool` | No | `False` | If True, moves an entire directory and all its contents. When True, identifier and destination_path should be directory paths (without file extensions). Defaults to False. |
| `project` | `Optional[str]` | No | `None` | Project name to move within. Optional - server will resolve using hierarchy. If unknown, use list_memory_projects() to discover available projects. |
| `project_id` | `Optional[str]` | No | `None` | Project external_id (UUID). Prefer this over `project` when known — it routes to the exact project regardless of name collisions across cloud workspaces. Takes precedence over `project`. Get from list_memory_projects(). |
| `output_format` | `Literal['text', 'json']` | No | `'text'` | "text" returns existing markdown guidance/success text. "json" returns machine-readable move metadata. |

_Source: `src/basic_memory/mcp/tools/move_note.py`_

### `write_note`

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

**Parameters:**

| Parameter | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `title` | `str` | Yes |  | The title of the note |
| `content` | `str` | Yes |  | Markdown content for the note, can include observations and relations |
| `directory` | `str` | Yes |  | Directory path relative to project root where the file should be saved. Use forward slashes (/) as separators. Use "/" or "" to write to project root. Examples: "notes", "projects/2025", "research/ml", "/" (root) |
| `project` | `Optional[str]` | No | `None` | Project name to write to. Optional - server will resolve using the hierarchy above. Use "workspace/project" to route to a project in a specific cloud workspace. A bare name that exists in multiple workspaces resolves to the default workspace, so use the qualified form (or project_id) to disambiguate. If unknown, use list_memory_projects() to discover available projects and their qualified names. |
| `workspace` | `Optional[str]` | No | `None` | Workspace slug, name, or tenant_id. When provided with `project`, routes as `workspace/project`. Cannot be combined with `project_id`. |
| `project_id` | `Optional[str]` | No | `None` | Project external_id (UUID). Prefer this over `project` when known — it routes to the exact project regardless of name collisions across cloud workspaces. Takes precedence over `project`. Get from list_memory_projects(). |
| `tags` | `list[str] \| str \| None` | No | `None` | Tags to categorize the note. Can be a list of strings, a comma-separated string, or None. Note: If passing from external MCP clients, use a string format (e.g. "tag1,tag2,tag3") |
| `note_type` | `str` | No | `'note'` | Type of note to create (stored in frontmatter). Defaults to "note". Can be "guide", "report", "config", "person", etc. |
| `metadata` | `dict[str, Any] \| None` | No | `None` | Optional dict of extra frontmatter fields merged into entity_metadata. Useful for schema notes or any note that needs custom YAML frontmatter beyond title/type/tags. Nested dicts are supported. |
| `overwrite` | `bool \| None` | No | `None` | If True, replace existing note on conflict. If False, error on conflict. If None (default), consult write_note_overwrite_default config setting. |
| `output_format` | `Literal['text', 'json']` | No | `'text'` | "text" returns the existing markdown summary. "json" returns machine-readable metadata. |

_Source: `src/basic_memory/mcp/tools/write_note.py`_

## Reading & Navigation

### `build_context`

Get context needed to continue a discussion within a specific project.

This tool enables natural continuation of discussions by loading relevant context
from memory:// URIs. It uses pattern matching to find relevant content and builds
a rich context graph of related information.

Project Resolution:
Server resolves projects using a unified priority chain (same in local and cloud modes):
Single Project Mode → project parameter → default project.
Uses default project automatically. Specify `project` parameter to target a different project.

**Parameters:**

| Parameter | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `url` | `MemoryUrl` | Yes |  | memory:// URI pointing to discussion content (e.g. memory://specs/search) |
| `project` | `Optional[str]` | No | `None` | Project name to build context from. Optional - server will resolve using hierarchy. If unknown, use list_memory_projects() to discover available projects. |
| `project_id` | `Optional[str]` | No | `None` | Project external_id (UUID). Prefer this over `project` when known — it routes to the exact project regardless of name collisions across cloud workspaces. Takes precedence over `project`. Get from list_memory_projects(). |
| `depth` | `str \| int \| None` | No | `1` | How many relation hops to traverse (1-3 recommended for performance) |
| `timeframe` | `Optional[TimeFrame]` | No | `'7d'` | How far back to look. Supports natural language like "2 days ago", "last week" |
| `page` | `int` | No | `1` | Page number of results to return (default: 1) |
| `page_size` | `int` | No | `DEFAULT_CONTEXT_PAGE_SIZE` | Number of primary results to return per page (default: 10, maximum: 50) |
| `max_related` | `int` | No | `DEFAULT_CONTEXT_RELATED_RESULTS` | Maximum total related results to return (default: 10, maximum: 100) |
| `output_format` | `Literal['json', 'text']` | No | `'json'` | Response format - "json" for structured JSON dict, "text" for compact markdown text |

_Source: `src/basic_memory/mcp/tools/build_context.py`_

### `list_directory`

List directory contents from the knowledge base with optional filtering.

This tool provides 'ls' functionality for browsing the knowledge base directory structure.
It can list immediate children or recursively explore subdirectories with depth control,
and supports glob pattern filtering for finding specific files.

**Parameters:**

| Parameter | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `dir_name` | `str` | No | `'/'` | Directory path to list (default: root "/") Examples: "/", "/projects", "/research/ml" |
| `depth` | `int` | No | `1` | Recursion depth (1-10, default: 1 for immediate children only) Higher values show subdirectory contents recursively |
| `file_name_glob` | `Optional[str]` | No | `None` | Optional glob pattern for filtering file names Examples: "*.md", "*meeting*", "project_*" |
| `sort` | `DirectorySortOrder \| None` | No | `None` | Optional file ordering: "title_asc", "title_desc", "updated_asc", or "updated_desc". Directories remain first. |
| `page` | `int` | No | `1` | One-indexed result page (default: 1) |
| `page_size` | `int` | No | `DEFAULT_DIRECTORY_PAGE_SIZE` | Number of nodes per page (default: 10, maximum: 200) |
| `output_format` | `Literal['text', 'json']` | No | `'text'` | "text" for a readable listing or "json" for structured pagination data |
| `project` | `Optional[str]` | No | `None` | Project name to list directory from. Optional - server will resolve using hierarchy. If unknown, use list_memory_projects() to discover available projects. |
| `project_id` | `Optional[str]` | No | `None` | Project external_id (UUID). Prefer this over `project` when known — it routes to the exact project regardless of name collisions across cloud workspaces. Takes precedence over `project`. Get from list_memory_projects(). |

_Source: `src/basic_memory/mcp/tools/list_directory.py`_

### `read_content`

Read a file's raw content by path or permalink.

This tool provides direct access to file content in the knowledge base,
handling different file types appropriately. Uses stateless architecture -
project parameter optional with server resolution.

Supported file types:
- Text files (markdown, code, etc.) are returned as plain text
- Images are automatically resized/optimized for display
- Other binary files are returned as base64 if below size limits

**Parameters:**

| Parameter | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `path` | `str` | Yes |  | The path or permalink to the file. Can be: - A regular file path (docs/example.md) - A memory URL (memory://docs/example) - A permalink (docs/example) |
| `project` | `Optional[str]` | No | `None` | Project name to read from. Optional - server will resolve using hierarchy. If unknown, use list_memory_projects() to discover available projects. |
| `project_id` | `Optional[str]` | No | `None` | Project external_id (UUID). Prefer this over `project` when known — it routes to the exact project regardless of name collisions across cloud workspaces. Takes precedence over `project`. Get from list_memory_projects(). |

_Source: `src/basic_memory/mcp/tools/read_content.py`_

### `read_note`

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

**Parameters:**

| Parameter | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `identifier` | `str` | Yes |  | The title or permalink of the note to read Can be a full memory:// URL, a permalink, a title, or search text |
| `project` | `Optional[str]` | No | `None` | Project name to read from. Optional - server will resolve using the hierarchy above. If unknown, use list_memory_projects() to discover available projects. |
| `project_id` | `Optional[str]` | No | `None` | Project external_id (UUID). Prefer this over `project` when known — it routes to the exact project regardless of name collisions across cloud workspaces. Takes precedence over `project`. Get from list_memory_projects(). |
| `page` | `int` | No | `1` | Page of fallback-search results to use when the identifier does not resolve to a note directly (default: 1). A direct or exact-title match always returns the full note content — page/page_size never chunk the note itself, and the title-match lookup pages through fixed-size pages of title results until an exact match is found or results are exhausted, regardless of page or page_size. |
| `page_size` | `int` | No | `10` | Number of fallback-search results per page (default: 10). When no match is found, this caps how many related-note suggestions are listed. |
| `output_format` | `Literal['text', 'json']` | No | `'text'` | "text" returns markdown content or guidance text. "json" returns a structured object with title/permalink/file_path/content/frontmatter. |
| `include_frontmatter` | `bool` | No | `False` | When output_format="json", whether content should include the opening YAML frontmatter block. |

_Source: `src/basic_memory/mcp/tools/read_note.py`_

### `recent_activity`

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

**Parameters:**

| Parameter | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `type` | `Union[str, List[str]]` | No | `''` | Filter by content type(s). Can be a string or list of strings. Valid options: - "entity" or ["entity"] for knowledge entities - "relation" or ["relation"] for connections between entities - "observation" or ["observation"] for notes and observations Multiple types can be combined: ["entity", "relation"] Case-insensitive: "ENTITY" and "entity" are treated the same. Default is entity-only. Specify other types explicitly to include observations and relations. |
| `depth` | `int` | No | `1` | How many relation hops to traverse (1-3 recommended) |
| `timeframe` | `TimeFrame` | No | `'7d'` | Time window to search. Supports natural language: - Relative: "2 days ago", "last week", "yesterday" - Points in time: "2024-01-01", "January 1st" - Standard format: "7d", "24h" |
| `page` | `int` | No | `1` | Page number for pagination (default 1) |
| `page_size` | `int` | No | `10` | Number of items per page (default 10) |
| `project` | `Optional[str]` | No | `None` | Project name to query. Optional - server will resolve using the hierarchy above. If unknown, use list_memory_projects() to discover available projects. |
| `project_id` | `Optional[str]` | No | `None` | Project external_id (UUID). Prefer this over `project` when known — it routes to the exact project regardless of name collisions across cloud workspaces. Takes precedence over `project`. Get from list_memory_projects(). |
| `output_format` | `Literal['text', 'json']` | No | `'text'` | "text" returns human-readable summary text. "json" returns a flat list of recent items. |

_Source: `src/basic_memory/mcp/tools/recent_activity.py`_

### `view_note`

View a markdown note as a formatted artifact.

This tool reads a note using the same logic as read_note but instructs Claude
to display the content as a markdown artifact in the Claude Desktop app.
Project parameter optional with server resolution.

**Parameters:**

| Parameter | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `identifier` | `str` | Yes |  | The title or permalink of the note to view |
| `project` | `Optional[str]` | No | `None` | Project name to read from. Optional - server will resolve using hierarchy. If unknown, use list_memory_projects() to discover available projects. |
| `project_id` | `Optional[str]` | No | `None` | Project external_id (UUID). Prefer this over `project` when known — it routes to the exact project regardless of name collisions across cloud workspaces. Takes precedence over `project`. Get from list_memory_projects(). |

_Source: `src/basic_memory/mcp/tools/view_note.py`_

## Search

### `fetch`

ChatGPT/OpenAI MCP fetch adapter returning a single text content item.

**Parameters:**

| Parameter | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `id` | `str` | Yes |  | Document identifier (permalink, title, or memory URL) |

_Source: `src/basic_memory/mcp/tools/chatgpt_tools.py`_

### `search`

ChatGPT/OpenAI MCP search adapter returning a single text content item.

**Parameters:**

| Parameter | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `query` | `str` | Yes |  | Search query (full-text syntax supported by `search_notes`) |

_Source: `src/basic_memory/mcp/tools/chatgpt_tools.py`_

### `search_notes`

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

### Basic Searches
- `search_notes("my-project", "keyword")` - Find any content containing "keyword"
- `search_notes("work-docs", "'exact phrase'")` - Search for exact phrase match

### Advanced Boolean Searches
- `search_notes("my-project", "term1 term2")` - Strict implicit-AND first; retries with
  relaxed OR terms only if strict search returns no results
- `search_notes("my-project", "term1 AND term2")` - Explicit AND search (both terms required)
- `search_notes("my-project", "term1 OR term2")` - Either term can be present
- `search_notes("my-project", "term1 NOT term2")` - Include term1 but exclude term2
- `search_notes("my-project", "(project OR planning) AND notes")` - Grouped boolean logic

### Content-Specific Searches
- `search_notes("research", "tag:example")` - Search within specific tags (if supported by content)
- `search_notes("work-project", "req", entity_types=["observation"], categories=["requirement"])`
  - Return only observations whose category is exactly "requirement"
- `search_notes("team-docs", "author:username")` - Find content by author (if metadata available)

**Note:** `tag:` shorthand is automatically converted to a `tags` filter, so it works
with any search type (text, hybrid, vector). You can also use the `tags` parameter
directly: `search_notes("project", "query", tags=["my-tag"])`

### Search Type Examples
- `search_notes("my-project", "Meeting", search_type="title")` - Search only in titles
- `search_notes("work-docs", "docs/meeting-*", search_type="permalink")` - Pattern match permalinks
  Note: Permalink patterns match the full path (e.g., "project/folder/chapter-13*", not just "chapter-13*").
- `search_notes("research", "keyword")` - Default search (hybrid when semantic is enabled,
  text when disabled)

### Filtering Options
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

### Structured Metadata Filters
Filters are exact matches on frontmatter metadata. Supported forms:
- Equality: `{"status": "in-progress"}`
- Array contains (all): `{"tags": ["security", "oauth"]}`
- Operators:
  - `$in`: `{"priority": {"$in": ["high", "critical"]}}`
  - `$gt`, `$gte`, `$lt`, `$lte`: `{"schema.confidence": {"$gt": 0.7}}`
  - `$between`: `{"schema.confidence": {"$between": [0.3, 0.6]}}`
- Nested keys use dot notation (e.g., `"schema.confidence"`).

### Filter-only Searches
Omit `query` (or pass None) when only using structured filters:
- `search_notes(metadata_filters={"type": "spec"}, project="my-project")`
- `search_notes(tags=["security"], project="my-project")`
- `search_notes(status="draft", project="my-project")`

### Convenience Filters
`tags` and `status` are shorthand for metadata_filters. If the same key exists in
metadata_filters, that value wins.

### Advanced Pattern Examples
- `search_notes("work-project", "project AND (meeting OR discussion)")` - Complex boolean logic
- `search_notes("research", ""exact phrase" AND keyword")` - Combine phrase and keyword search
- `search_notes("dev-notes", "bug NOT fixed")` - Exclude resolved issues
- `search_notes("archive", "docs/2024-*", search_type="permalink")` - Year-based permalink search

**Parameters:**

| Parameter | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `query` | `Optional[str]` | No | `None` | Optional search query string (supports boolean operators, phrases, patterns). Omit or pass None for filter-only searches using metadata_filters, tags, or status. |
| `project` | `Optional[str]` | No | `None` | Project name to search in. Optional - server will resolve using hierarchy. If unknown, use list_memory_projects() to discover available projects. |
| `project_id` | `Optional[str]` | No | `None` | Project external_id (UUID). Prefer this over `project` when known — it routes to the exact project regardless of name collisions across cloud workspaces. Takes precedence over `project`. Get from list_memory_projects(). |
| `search_all_projects` | `bool` | No | `False` | Optional opt-in to search every accessible project. Ignored when `project` or `project_id` is supplied. |
| `page` | `int` | No | `1` | The page number of results to return (default 1) |
| `page_size` | `int` | No | `10` | The number of results to return per page (default 10) |
| `search_type` | `str \| None` | No | `None` | Type of search to perform, one of: "text", "title", "permalink", "vector", "semantic", "hybrid". Default is dynamic: "hybrid" when semantic search is enabled, otherwise "text". |
| `output_format` | `Literal['text', 'json']` | No | `'text'` | "text" preserves existing structured search response behavior. "json" returns a machine-readable dictionary payload. |
| `note_types` | `List[str] \| None` | No | `None` | Optional list of note types to search (e.g., ["note", "person"]) |
| `entity_types` | `List[str] \| None` | No | `None` | Optional list of entity types to filter by (e.g., ["entity", "observation"]) |
| `categories` | `List[str] \| None` | No | `None` | Optional list of observation categories for exact matching (e.g., ["requirement"]). Pair with entity_types=["observation"] to return only observations whose category matches exactly. |
| `after_date` | `Optional[str]` | No | `None` | Optional date filter for recent content (e.g., "1 week", "2d", "2024-01-01") |
| `metadata_filters` | `Dict[str, Any] \| None` | No | `None` | Optional structured frontmatter filters (e.g., {"status": "in-progress"}) |
| `tags` | `List[str] \| None` | No | `None` | Optional tag filter (frontmatter tags); shorthand for metadata_filters["tags"]. Accepts a list (["a", "b"]) or a comma-separated string ("a,b"), matching the write_note tags convention and the tag: query shorthand. |
| `status` | `Optional[str]` | No | `None` | Optional status filter (frontmatter status); shorthand for metadata_filters["status"] |
| `min_similarity` | `Optional[float]` | No | `None` | Optional float to override the global semantic_min_similarity threshold for this query. E.g., 0.0 to see all vector results, or 0.8 for high precision. Only applies to vector and hybrid search types. |

_Source: `src/basic_memory/mcp/tools/search.py`_

## Project & Workspace Management

### `create_memory_project`

Create a new Basic Memory project.

Creates a new project with the specified name and path. The project directory
will be created if it doesn't exist. Optionally sets the new project as default.

**Parameters:**

| Parameter | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `project_name` | `str` | Yes |  | Name for the new project (must be unique) |
| `project_path` | `str` | Yes |  | File system path where the project will be stored |
| `set_default` | `bool` | No | `False` | Whether to set this project as the default (optional, defaults to False) |
| `workspace` | `str \| None` | No | `None` | Optional cloud workspace selector to create the project in. Slug is preferred for AI callers, but tenant_id and unique name are also accepted. When omitted, the connection's default workspace is used. Discover values via `list_workspaces`. A workspace selector implies cloud routing: without cloud credentials the call fails fast instead of silently creating a local project (#954). |
| `output_format` | `Literal['text', 'json']` | No | `'text'` | "text" returns the existing human-readable result text. "json" returns structured project creation metadata. |

_Source: `src/basic_memory/mcp/tools/project_management.py`_

### `delete_project`

Delete a Basic Memory project.

Removes a project from Basic Memory's configuration and database records.
By default the project's note files are retained: local projects keep
their files on disk, cloud projects keep their files in cloud storage.
Pass delete_notes=True to also delete the note files themselves.

**Parameters:**

| Parameter | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `project_name` | `str` | Yes |  | Name of the project to delete |
| `delete_notes` | `bool` | No | `False` | Also delete the project's note files (from local disk for local projects, from cloud storage for cloud projects). Defaults to False, which only stops tracking the project. |
| `workspace` | `str \| None` | No | `None` | Optional cloud workspace selector to delete the project from. Slug is preferred for AI callers, but tenant_id and unique name are also accepted. When omitted, the connection's default workspace is used. A workspace selector implies cloud routing: without cloud credentials the call fails fast, matching create_memory_project behavior (#954). |

_Source: `src/basic_memory/mcp/tools/project_management.py`_

### `list_memory_projects`

List all available projects with their status.

Shows projects from both local and cloud sources when cloud credentials
are available, merging by permalink to give a unified view.

Each project entry includes an `external_id` (UUID). Pass that value as the
`project_id` parameter on other tools to address a specific project
unambiguously across cloud workspaces — useful when the same project name
exists in more than one workspace.

**Parameters:**

| Parameter | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `output_format` | `Literal['text', 'json']` | No | `'text'` | "text" returns the existing human-readable project list. "json" returns structured project metadata. |

_Source: `src/basic_memory/mcp/tools/project_management.py`_

### `list_workspaces`

List workspaces available to the current cloud user.

**Parameters:**

| Parameter | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `output_format` | `Literal['text', 'json']` | No | `'text'` | "text" returns human-readable workspace list. "json" returns structured workspace metadata. |

_Source: `src/basic_memory/mcp/tools/workspaces.py`_

## Schema Tools

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

**Parameters:**

| Parameter | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `note_type` | `str` | Yes |  | The note type to check for drift (e.g., "person"). |
| `project` | `Optional[str]` | No | `None` | Project name. Optional -- server will resolve. |
| `project_id` | `Optional[str]` | No | `None` | Project external_id (UUID). Prefer this over `project` when known — it routes to the exact project regardless of name collisions across cloud workspaces. Takes precedence over `project`. Get from list_memory_projects(). |
| `output_format` | `Literal['text', 'json']` | No | `'text'` |  |

_Source: `src/basic_memory/mcp/tools/schema.py`_

### `schema_infer`

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

**Parameters:**

| Parameter | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `note_type` | `str` | Yes |  | The note type to analyze (e.g., "person", "meeting"). |
| `threshold` | `float` | No | `0.25` | Minimum frequency (0-1) for a field to be suggested as optional. Default 0.25 (25%). Fields above 95% become required. |
| `project` | `Optional[str]` | No | `None` | Project name. Optional -- server will resolve. |
| `project_id` | `Optional[str]` | No | `None` | Project external_id (UUID). Prefer this over `project` when known — it routes to the exact project regardless of name collisions across cloud workspaces. Takes precedence over `project`. Get from list_memory_projects(). |
| `output_format` | `Literal['text', 'json']` | No | `'text'` |  |

_Source: `src/basic_memory/mcp/tools/schema.py`_

### `schema_validate`

Validate notes against their resolved schema.

Validates a specific note (by identifier), all notes of a given type, or —
when called with neither — all notes of every type that has a schema
defined, with a per-type breakdown.
Returns warnings/errors based on the schema's validation mode.

Schemas are resolved in priority order:
1. Inline schema (dict in frontmatter)
2. Explicit reference (string in frontmatter)
3. Implicit by type (type field matches schema note's entity field)
4. No schema (no validation)

Project Resolution:
Server resolves projects in this order: Single Project Mode -> project parameter -> default.
If project unknown, use list_memory_projects() first.

**Parameters:**

| Parameter | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `note_type` | `Optional[str]` | No | `None` | Note type to batch-validate (e.g., "person", "meeting"). If provided, validates all notes of this type. |
| `identifier` | `Optional[str]` | No | `None` | Specific note to validate (permalink, title, or path). If provided, validates only this note. |
| `project` | `Optional[str]` | No | `None` | Project name. Optional -- server will resolve. |
| `project_id` | `Optional[str]` | No | `None` | Project external_id (UUID). Prefer this over `project` when known — it routes to the exact project regardless of name collisions across cloud workspaces. Takes precedence over `project`. Get from list_memory_projects(). |
| `output_format` | `Literal['text', 'json']` | No | `'text'` |  |

_Source: `src/basic_memory/mcp/tools/schema.py`_

## Diagnostics

### `basic_memory_diagnostics`

Return version, system, and configuration diagnostics for Basic Memory.

Provides:
- Basic Memory package version
- Python version and platform details
- Config file path and its contents (secrets redacted)

Useful for troubleshooting installations and gathering information for
support requests. Read-only; never emits secrets or API keys.

**Parameters:**

_No parameters._

_Source: `src/basic_memory/mcp/tools/basic_memory_diagnostics.py`_
