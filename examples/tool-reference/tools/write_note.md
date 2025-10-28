---
title: Write Note
type: guide
permalink: tools/write_note
tags:
- mcp-tools
- reference
- write_note
created: 2025-10-28T00:00:00
modified: 2025-10-28T00:00:00
---

# Write Note

Create or update markdown notes with semantic observations and relations.

## Function Signature

```python
write_note(title, content, folder, project=None, tags=None, entity_type="note")
```

## Parameters

- **title** (str): The title of the note
- **content** (str): Markdown content for the note, can include observations and relations
- **folder** (str): Folder path relative to project root (use "/" or "" for root)
- **project** (str, optional): Project name to write to
- **tags** (str|list, optional): Tags to categorize the note
- **entity_type** (str): Type of entity ("note", "guide", "report", etc.)

## Returns

A markdown formatted summary of the semantic content, including:
- Creation/update status with project name
- File path and checksum
- Observation counts by category
- Relation counts (resolved/unresolved)
- Tags if present

## Observations

- [tool] MCP tool for creating or updating markdown notes with semantic observations and relations #mcp #basic-memory
- [returns] Markdown formatted summary of semantic content #output
- [category] Content Management tool #classification
- [feature] Supports YAML frontmatter, observations with [category] syntax, and relations #knowledge-graph
- [feature] Validates folder paths to prevent path traversal attacks #security

## Usage Examples

### Basic Usage

```python
# Create a simple note
write_note(
    project="my-research",
    title="Meeting Notes",
    folder="meetings",
    content="# Weekly Standup\\n\\n- [decision] Use SQLite for storage #tech"
)
```

### With Semantic Content

```python
write_note(
    project="work-project",
    title="API Design",
    folder="specs",
    content="""
# REST API Specification

## Overview
Core API endpoints for user management.

## Observations
- [design] RESTful architecture #api #architecture
- [tech] FastAPI framework #implementation #python
- [decision] JWT for authentication #security

## Relations
- implements [[Authentication System]]
- uses [[PostgreSQL Database]]
- specified_by [[OpenAPI Spec]]
    """,
    tags=["api", "design"],
    entity_type="guide"
)
```

### Creating Knowledge Graph Entries

```python
# Entity with relations to other concepts
write_note(
    project="knowledge-base",
    title="Machine Learning Basics",
    folder="concepts",
    content="""
# Machine Learning Basics

Core concepts in machine learning and AI.

## Observations
- [definition] ML is subset of AI focused on learning from data #ai #ml
- [technique] Supervised learning requires labeled data #training
- [application] Image recognition, NLP, recommendation systems #use-cases

## Relations
- part_of [[Artificial Intelligence]]
- requires [[Training Data]]
- enables [[Predictive Analytics]]
- contrasts_with [[Rule-Based Systems]]
    """
)
```

### Updating Existing Notes

```python
# Update existing note (same title/folder)
write_note(
    project="my-research",
    title="Meeting Notes",
    folder="meetings",
    content="# Weekly Standup\\n\\n- [decision] Use PostgreSQL instead #tech"
)
```

## Project Resolution

Server resolves projects in this order:
1. Single Project Mode (if only one project exists)
2. project parameter (if provided)
3. default project (from configuration)

If project is unknown, use `list_memory_projects()` or `recent_activity()` first.

## Security

- Validates folder paths to prevent path traversal attacks
- Folder paths must stay within project boundaries
- Paths like "../../../etc/passwd" are blocked

## Relations

- part_of [[Content Management]]
- documented_in [[MCP Tool Reference]]
- complements [[Read Note]]
- complements [[Edit Note]]
- uses [[Entity Parser]]
