---
name: knowledge-capture
description: Capture insights, decisions, and learnings from conversations into structured Basic Memory notes with observations and relations
---

# Knowledge Capture

This skill helps you capture valuable information from conversations into Basic Memory's knowledge graph using structured notes with observations and relations.

## When to Use

Use this skill when:
- Important decisions are made during a conversation
- Technical insights or patterns are discovered
- Problems are solved and the solution should be preserved
- Design trade-offs are discussed
- Architecture or implementation approaches are chosen
- Learnings from debugging or investigation emerge

## Capture Process

### 1. Identify Valuable Information

Look for:
- **Decisions**: Choices made and their rationale
- **Insights**: New understanding or "aha" moments
- **Patterns**: Reusable approaches or solutions
- **Trade-offs**: Options considered and why one was chosen
- **Learnings**: What worked, what didn't, and why
- **Context**: Background that would help future understanding

### 2. Structure the Note

Use Basic Memory's knowledge format:

```markdown
---
title: Descriptive Title
type: note
tags:
- relevant
- tags
---

# Title

## Context
Brief background explaining the situation.

## Content
Main content organized logically.

## Observations

- [decision] What was decided and why #tag
- [insight] Key understanding gained #tag
- [pattern] Reusable approach identified #tag
- [learning] What we learned from this #tag
- [tradeoff] Option A chosen over B because... #tag

## Relations

- relates-to [[Related Concept]]
- implements [[Parent Spec or Design]]
- learned-from [[Source of Learning]]
```

### 3. Choose Appropriate Categories

Common observation categories:
- `[decision]` - Choices made
- `[insight]` - Understanding gained
- `[pattern]` - Reusable approaches
- `[learning]` - Lessons learned
- `[tradeoff]` - Options weighed
- `[problem]` - Issues identified
- `[solution]` - Fixes applied
- `[architecture]` - Structural decisions
- `[implementation]` - Code-level choices
- `[constraint]` - Limitations discovered
- `[requirement]` - Needs identified

### 4. Create Meaningful Relations

Link to related knowledge:
- `relates-to` - General association
- `implements` - Realizes a spec or design
- `extends` - Builds upon existing concept
- `learned-from` - Source of insight
- `enables` - Makes something possible
- `depends-on` - Requires another concept
- `solves` - Addresses a problem

## MCP Tools to Use

```python
# Write a new note
mcp__basic-memory__write_note(
    title="Your Note Title",
    content="Full markdown content...",
    folder="appropriate/folder",
    tags=["tag1", "tag2"],
    project="main"  # or appropriate project
)

# Search for related notes to link
mcp__basic-memory__search_notes(
    query="relevant terms",
    project="main"
)

# Read existing notes for context
mcp__basic-memory__read_note(
    identifier="note-title-or-permalink",
    project="main"
)
```

## Folder Organization

Choose appropriate folders:
- `decisions/` - Architecture and design decisions
- `learnings/` - Insights and lessons learned
- `patterns/` - Reusable approaches
- `debug-logs/` - Problem investigations
- `conversations/` - Imported conversation summaries

## Examples

### Capturing a Technical Decision

```markdown
---
title: FastAPI Async Client Pattern
type: note
tags:
- architecture
- fastapi
- async
---

# FastAPI Async Client Pattern

## Context
During implementation of MCP tools, we needed to decide how to handle HTTP client lifecycle.

## Decision
Use context manager pattern for HTTP clients instead of module-level singletons.

## Rationale
- Proper resource management
- Supports three deployment modes (local ASGI, CLI cloud, cloud app)
- Auth happens at client creation, not per-request
- Enables dependency injection for testing

## Observations

- [decision] Context manager pattern for HTTP clients enables proper resource cleanup #architecture
- [pattern] Factory pattern allows different client configurations per deployment mode #flexibility
- [tradeoff] Slightly more verbose than singleton but much more flexible #engineering

## Relations

- implements [[SPEC-16 MCP Cloud Service Consolidation]]
- enables [[Cloud App Integration]]
```

### Capturing a Debugging Insight

```markdown
---
title: SQLite WAL Mode Performance Fix
type: note
tags:
- debugging
- sqlite
- performance
---

# SQLite WAL Mode Performance Fix

## Problem
Sync operations were slow with multiple concurrent writes.

## Investigation
Found that default SQLite journaling was causing lock contention.

## Solution
Enabled WAL (Write-Ahead Logging) mode for the database connection.

## Observations

- [problem] Default SQLite journaling causes lock contention under concurrent writes #performance
- [solution] WAL mode significantly improves concurrent write performance #sqlite
- [learning] Always consider WAL mode for SQLite in applications with concurrent access #database

## Relations

- solves [[Sync Performance Issues]]
- relates-to [[SPEC-19 Sync Performance]]
```

## Best Practices

1. **Capture immediately** - Write notes while context is fresh
2. **Be specific** - Include concrete details, not vague summaries
3. **Link liberally** - More relations = better knowledge graph
4. **Use tags** - Enable discovery via search
5. **Include context** - Future you won't remember the situation
6. **Prefer facts over opinions** - Observations should be verifiable
7. **Keep notes atomic** - One concept per note when possible
