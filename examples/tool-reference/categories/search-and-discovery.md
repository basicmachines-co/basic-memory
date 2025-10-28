---
title: Search & Discovery
type: guide
permalink: categories/search-and-discovery
tags:
- mcp-tools
- reference
- category
- search
created: 2025-10-28T00:00:00
modified: 2025-10-28T00:00:00
---

# Search & Discovery

Full-text search capabilities with advanced syntax support for discovering content in your knowledge base.

## Overview

The Search & Discovery category focuses on finding content across your entire knowledge base.
The primary tool, `search_notes`, provides powerful full-text search with boolean operators,
phrase matching, filtering, and pattern matching.

## Observations

- [category] Contains 1 core MCP tool (search_notes) #mcp #tools
- [purpose] Search & Discovery functionality for Basic Memory #functionality
- [feature] Full-text search with FTS5 engine #search-engine
- [feature] Boolean operators (AND, OR, NOT) #advanced-search
- [feature] Phrase matching with quotes #search-syntax
- [feature] Content filtering (types, entity_types, after_date) #filtering
- [feature] Pattern matching on permalinks #pattern-matching
- [feature] Helpful error messages with suggestions #ux

## Tools in This Category

### search_notes

Full-text search across all content with advanced filtering and boolean operators.

**Search Types:**
- **Text search** (default): Full-text search across all content
- **Title search**: Search only in note titles
- **Permalink search**: Pattern matching on file paths

**Boolean Operators:**
- `AND`: Both terms must be present
- `OR`: Either term can be present
- `NOT`: Exclude specific terms
- Grouping with `()`

**Filtering Options:**
- `types`: Filter by note types (e.g., ["note", "person"])
- `entity_types`: Filter by entity types (e.g., ["observation"])
- `after_date`: Show only recent content

**Content-Specific Searches:**
- `tag:example`: Search within tags
- `category:observation`: Filter by category
- `author:username`: Search by author (if metadata available)

## Search Syntax Guide

### Basic Text Search

```python
# Simple keyword
search_notes("machine learning")

# Multiple keywords (implicit AND)
search_notes("python programming tutorial")
```

### Boolean Operators

```python
# Explicit AND
search_notes("machine AND learning")

# OR operator
search_notes("python OR javascript")

# NOT operator
search_notes("programming NOT beginner")

# Complex boolean
search_notes("(python OR javascript) AND tutorial NOT beginner")
```

### Phrase Search

```python
# Exact phrase
search_notes('"machine learning basics"')

# Phrase with boolean
search_notes('"neural networks" AND python')
```

### Search Types

```python
# Full-text (default)
search_notes("keyword", search_type="text")

# Title only
search_notes("Tutorial", search_type="title")

# Permalink patterns
search_notes("docs/2024-*", search_type="permalink")
search_notes("meetings/weekly-*", search_type="permalink")
```

### Filtering

```python
# By content type
search_notes("meeting", types=["entity"])

# By entity type
search_notes("decision", entity_types=["observation"])

# By date
search_notes("update", after_date="1 week")
search_notes("report", after_date="2024-01-01")

# Multiple filters
search_notes(
    "bug OR issue",
    types=["entity"],
    entity_types=["observation"],
    after_date="1 week"
)
```

### Content-Specific

```python
# Tag search
search_notes("tag:api")
search_notes("tag:urgent OR tag:important")

# Category search
search_notes("category:decision")
search_notes("category:observation AND tech")
```

## Common Search Patterns

### Find Recent Work

```python
# All recent updates
search_notes("", after_date="1 week")

# Recent on specific topic
search_notes("machine learning", after_date="3d")
```

### Topic Exploration

```python
# Broad topic
search_notes("artificial intelligence")

# Narrow focus
search_notes("artificial intelligence AND ethics")

# Exclude basics
search_notes("AI NOT basics NOT introduction")
```

### Project Management

```python
# Open bugs
search_notes("bug NOT fixed NOT resolved")

# Recent decisions
search_notes("category:decision", after_date="1 week")

# Meeting notes
search_notes("meeting OR standup", search_type="title")
```

### Research Workflows

```python
# Literature review
search_notes('"literature review" OR paper OR study')

# Research by topic
search_notes("(neural networks OR deep learning) AND research")

# Recent research
search_notes("research", after_date="1 month")
```

## Error Handling

`search_notes` provides helpful error messages for:

### Syntax Errors

If query has invalid syntax, get suggestions like:
- Simplify the query
- Remove special characters
- Use basic boolean operators
- Example corrections

### No Results

If no results found, get guidance on:
- Broadening the search
- Trying variations
- Different search types
- Using filters
- Exploring what exists

### Other Errors

- Project not found
- Server errors
- Permission errors

Each error includes specific next steps.

## Comparison with Related Tools

### search_notes vs read_note

- **search_notes**: Find notes you don't know exist
- **read_note**: Retrieve specific known notes

### search_notes vs recent_activity

- **search_notes**: Find by content/keywords
- **recent_activity**: Find by modification time

### search_notes vs build_context

- **search_notes**: Keyword-based discovery
- **build_context**: Graph-based navigation

## Relations

- contains [[Search Notes]]
- part_of [[MCP Tool Reference]]
- complements [[Knowledge Graph Navigation]]
- uses [[FTS5 Search Engine]]
