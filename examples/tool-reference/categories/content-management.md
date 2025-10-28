---
title: Content Management
type: guide
permalink: categories/content-management
tags:
- mcp-tools
- reference
- category
- content-management
created: 2025-10-28T00:00:00
modified: 2025-10-28T00:00:00
---

# Content Management

Tools for creating, reading, editing, and managing markdown notes in Basic Memory.

## Overview

The Content Management category contains 7 core MCP tools that handle all aspects of note lifecycle:
creating, reading, viewing, editing, moving, and deleting notes. These tools work with Basic Memory's
knowledge graph format, supporting observations, relations, and semantic content.

## Observations

- [category] Contains 7 MCP tools #mcp #tools
- [purpose] Content Management functionality for Basic Memory #functionality
- [feature] All tools support project resolution (Single Project → parameter → default) #project-resolution
- [feature] All tools validate paths to prevent security issues #security
- [design] Tools use consistent markdown format with frontmatter, observations, and relations #format

## Tools in This Category

### write_note

Create or update markdown notes with semantic observations and relations.

**Key Features:**
- Supports YAML frontmatter
- Observations with `[category]` syntax
- Relations using `[[Entity]]` wikilinks
- Tags for categorization
- Custom entity types
- Security: validates folder paths

**Use When:**
- Creating new notes
- Updating existing notes
- Building knowledge graph entries

### read_note

Read markdown notes by title, permalink, or memory:// URL.

**Key Features:**
- Multiple lookup strategies (permalink → title → text search)
- Returns full markdown content
- Pagination support
- Helpful error messages when not found

**Use When:**
- Reading specific notes
- Retrieving content for processing
- Loading context for conversations

### read_content

Read raw file content without knowledge graph processing.

**Key Features:**
- Reads any file type (text, images, binaries)
- Returns raw content or base64 encoding
- No parsing or processing
- Direct file access

**Use When:**
- Reading non-markdown files
- Accessing images or binaries
- Need raw content without processing

### view_note

View notes as formatted artifacts for better readability.

**Key Features:**
- Formats notes as artifacts
- Better visual presentation
- Same lookup as read_note
- Pagination support

**Use When:**
- Presenting notes to users
- Better readability needed
- Viewing long-form content

### edit_note

Edit notes incrementally with various operations.

**Key Features:**
- Append content to end
- Prepend content to beginning
- Find and replace text
- Replace specific sections
- Maintains existing structure

**Use When:**
- Adding to existing notes
- Updating specific sections
- Find/replace operations
- Incremental edits

### move_note

Move notes to new locations while maintaining links.

**Key Features:**
- Updates file system
- Updates database
- Maintains relations
- Updates permalinks

**Use When:**
- Reorganizing content
- Moving between folders
- Renaming notes

### delete_note

Delete notes from the knowledge base.

**Key Features:**
- Removes from file system
- Removes from database
- Cleans up relations
- Confirmation required

**Use When:**
- Removing outdated content
- Cleaning up test notes
- Content no longer needed

## Common Workflows

### Create and Edit Workflow

```python
# 1. Create initial note
write_note(
    project="my-project",
    title="New Feature",
    folder="features",
    content="# New Feature\\n\\nInitial draft..."
)

# 2. Add more content
edit_note(
    "my-project",
    "features/new-feature",
    operation="append",
    content="\\n## Implementation\\n\\nDetails..."
)

# 3. Read to verify
read_note("my-project", "features/new-feature")
```

### Read and Move Workflow

```python
# 1. Read current note
content = read_note("my-project", "drafts/note")

# 2. Move to published
move_note(
    "my-project",
    "drafts/note",
    "published/note"
)

# 3. Verify new location
read_note("my-project", "published/note")
```

### Content Organization Workflow

```python
# 1. Create in temporary location
write_note(
    project="research",
    title="Quick Note",
    folder="temp",
    content="Quick thoughts..."
)

# 2. Refine content
edit_note(
    "research",
    "temp/quick-note",
    operation="replace_section",
    content="Quick thoughts...|# Refined Thoughts\\n\\nPolished version..."
)

# 3. Move to permanent location
move_note(
    "research",
    "temp/quick-note",
    "concepts/refined-thoughts"
)
```

## Relations

- contains [[Write Note]]
- contains [[Read Note]]
- contains [[Read Content]]
- contains [[View Note]]
- contains [[Edit Note]]
- contains [[Move Note]]
- contains [[Delete Note]]
- part_of [[MCP Tool Reference]]
