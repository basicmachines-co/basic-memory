---
title: Search Notes
type: guide
permalink: tools/search_notes
tags:
- mcp-tools
- reference
- search_notes
- search
created: 2025-10-28T00:00:00
modified: 2025-10-28T00:00:00
---

# Search Notes

Full-text search across all content with advanced syntax support including boolean operators, phrase matching, and filtering.

## Function Signature

```python
search_notes(query, project=None, page=1, page_size=10, search_type="text",
             types=None, entity_types=None, after_date=None)
```

## Parameters

- **query** (str): Search query string (supports boolean operators, phrases, patterns)
- **project** (str, optional): Project name to search in
- **page** (int): Page number of results (default 1)
- **page_size** (int): Number of results per page (default 10)
- **search_type** (str): Type of search - "text", "title", or "permalink" (default "text")
- **types** (list, optional): Filter by note types (e.g., ["note", "person"])
- **entity_types** (list, optional): Filter by entity types (e.g., ["entity", "observation"])
- **after_date** (str, optional): Filter recent content (e.g., "1 week", "2d", "2024-01-01")

## Returns

SearchResponse with results and pagination info, or helpful error guidance if search fails.

## Observations

- [tool] MCP tool for full-text search with advanced filtering and boolean operators #mcp #search
- [returns] SearchResponse with paginated results #output
- [category] Search & Discovery tool #classification
- [feature] Supports boolean AND, OR, NOT operators #search-syntax
- [feature] Supports exact phrase matching with quotes #search-syntax
- [feature] Supports content-specific searches (tag:, category:, etc.) #advanced-search
- [feature] Supports pattern matching on permalinks #permalink-search
- [feature] Provides helpful error messages with suggestions #ux

## Search Syntax Guide

### Basic Searches

```python
# Simple keyword search
search_notes("project planning")

# Multiple terms (implicit AND)
search_notes("machine learning python")
```

### Boolean Operators

```python
# AND search (both terms required)
search_notes("project AND planning")

# OR search (either term)
search_notes("project OR meeting")

# NOT search (exclude term)
search_notes("project NOT archived")

# Complex boolean with grouping
search_notes("(project OR planning) AND notes")
```

### Phrase Search

```python
# Exact phrase match
search_notes('"weekly standup meeting"')

# Combine phrase and keyword
search_notes('"exact phrase" AND keyword')
```

### Search Types

```python
# Full-text search (default)
search_notes("keyword", search_type="text")

# Title-only search
search_notes("Machine Learning", search_type="title")

# Permalink pattern matching
search_notes("docs/meeting-*", search_type="permalink")
search_notes("archive/2024-*", search_type="permalink")
```

### Filtering Options

```python
# Filter by content type
search_notes("meeting notes", types=["entity"])

# Filter by entity type
search_notes("meeting notes", entity_types=["observation"])

# Recent content only
search_notes("bug report", after_date="1 week")
search_notes("updates", after_date="2024-01-01")

# Multiple filters combined
search_notes(
    "(bug OR issue) AND NOT resolved",
    types=["entity"],
    after_date="2024-01-01"
)
```

### Content-Specific Searches

```python
# Search within tags
search_notes("tag:example")

# Filter by category
search_notes("category:observation")

# Search by author (if metadata available)
search_notes("author:username")
```

### Advanced Patterns

```python
# Complex boolean logic
search_notes("project AND (meeting OR discussion)")

# Exclude specific terms
search_notes("bug NOT fixed")

# Year-based permalink search
search_notes("docs/2024-*", search_type="permalink")

# Combine multiple search techniques
search_notes(
    '("weekly meeting" OR standup) AND notes NOT archived',
    types=["entity"],
    after_date="1 week"
)
```

## Common Use Cases

### Find Recent Updates

```python
# Updates from last week
search_notes("", after_date="1 week")

# Specific topic, recent only
search_notes("machine learning", after_date="3d")
```

### Search by Topic

```python
# Find all machine learning content
search_notes("machine learning")

# Find ML content excluding basics
search_notes("machine learning NOT basics")
```

### Search Within Categories

```python
# Find all decisions
search_notes("category:decision")

# Find technical decisions
search_notes("category:decision AND tech")
```

### Explore Tags

```python
# All content with specific tag
search_notes("tag:api")

# Multiple tag search
search_notes("tag:api OR tag:design")
```

## Error Handling

The tool provides helpful error messages and suggestions for:
- Invalid syntax errors
- No results found
- Project not found
- Server errors
- Permission/access errors

Each error includes specific suggestions for how to fix the issue.

## Project Resolution

Server resolves projects in this order:
1. Single Project Mode
2. project parameter
3. default project

If project unknown, use `list_memory_projects()` or `recent_activity()` first.

## Relations

- part_of [[Search & Discovery]]
- documented_in [[MCP Tool Reference]]
- complements [[Recent Activity]]
- complements [[Build Context]]
- uses [[FTS5 Search Engine]]
