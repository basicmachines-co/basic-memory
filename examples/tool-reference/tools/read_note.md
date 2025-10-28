---
title: Read Note
type: guide
permalink: tools/read_note
tags:
- mcp-tools
- reference
- read_note
created: 2025-10-28T00:00:00
modified: 2025-10-28T00:00:00
---

# Read Note

Read markdown notes by title, permalink, or memory:// URL with automatic fallback search.

## Function Signature

```python
read_note(identifier, project=None, page=1, page_size=10)
```

## Parameters

- **identifier** (str): Title, permalink, or memory:// URL of the note to read
- **project** (str, optional): Project name to read from
- **page** (int): Page number for paginated results (default 1)
- **page_size** (int): Number of items per page (default 10)

## Returns

Full markdown content of the note if found, or helpful guidance if not found.
Content includes frontmatter, observations, relations, and all markdown formatting.

## Observations

- [tool] MCP tool for reading markdown notes by title, permalink, or memory:// URL #mcp #read
- [returns] Full markdown content with frontmatter and formatting #output
- [category] Content Management tool #classification
- [feature] Multiple lookup strategies (permalink → title → text search) #smart-lookup
- [feature] Provides helpful suggestions when note not found #ux
- [feature] Validates identifiers to prevent path traversal #security

## Lookup Strategies

The tool tries multiple strategies to find the note:

1. **Direct permalink lookup** (fastest)
2. **Title search fallback** (if permalink fails)
3. **Text search** as last resort

This makes it flexible - you can use any identifier type.

## Usage Examples

### Read by Permalink

```python
# Direct permalink lookup (fastest)
read_note("my-research", "specs/search-spec")
read_note("work-project", "meetings/weekly-standup")
```

### Read by Title

```python
# Title search with automatic fallback
read_note("work-project", "Search Specification")
read_note("team-docs", "Weekly Standup")
```

### Read with Memory URL

```python
# Using memory:// protocol
read_note("my-research", "memory://specs/search-spec")
read_note("work-project", "memory://meetings/weekly")
```

### With Pagination

```python
# Large notes with pagination
read_note("work-project", "Project Updates", page=2, page_size=5)
```

## Not Found Handling

If the note isn't found, the tool provides helpful suggestions:

- Checks if identifier is title vs permalink
- Suggests using search instead
- Recommends checking recent activity
- Provides template for creating new note

Example error response:

```markdown
# Note Not Found in my-research: "search-spec"

I couldn't find any notes matching "search-spec". Here are some suggestions:

## Check Identifier Type
- If you provided a title, try using the exact permalink instead
- If you provided a permalink, check for typos or try a broader search

## Search Instead
Try searching for related content:
search_notes(project="my-research", query="search-spec")

## Create New Note
This might be a good opportunity to create a new note on this topic...
```

## Security

- Validates identifiers to prevent path traversal attacks
- Paths must stay within project boundaries
- Both raw and processed paths are validated

## Project Resolution

Server resolves projects in this order:
1. Single Project Mode
2. project parameter
3. default project

If project unknown, use `list_memory_projects()` or `recent_activity()` first.

## Relations

- part_of [[Content Management]]
- documented_in [[MCP Tool Reference]]
- complements [[Write Note]]
- alternative [[View Note]]
- uses [[Search Notes]] (for fallback lookup)
