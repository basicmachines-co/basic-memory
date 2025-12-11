---
name: knowledge-organize
description: Help organize, link, and maintain the Basic Memory knowledge graph - find orphan notes, suggest relations, identify duplicates, and improve overall knowledge structure
---

# Knowledge Organize

This skill helps users maintain a healthy, well-connected knowledge graph. As notes accumulate, it becomes valuable to periodically organize, link, and curate the knowledge base.

## When to Use

Use this skill when:
- User asks to organize their notes
- User wants to find connections between notes
- User mentions orphan or unlinked notes
- User wants to clean up or improve their knowledge base
- User asks about duplicate or similar notes
- User wants help with folder organization
- User asks to review or audit their notes
- Phrases like "help me organize", "find related notes", "what's not linked", "clean up my notes"

## Organization Capabilities

### 1. Find Orphan Notes

Identify notes that have no relations to other notes - they're isolated in the knowledge graph.

```python
# Get all notes
mcp__basic-memory__search_notes(
    query="*",
    page_size=50,
    project="main"
)

# For each note, check if it has relations
# Orphans have empty Relations sections
```

**What to do with orphans:**
- Suggest potential relations based on content similarity
- Ask if they should be linked to existing topics
- Propose creating hub notes to connect related orphans

### 2. Suggest Relations

Analyze note content and suggest meaningful connections.

```python
# Read a note
mcp__basic-memory__read_note(
    identifier="note-to-analyze",
    project="main"
)

# Search for potentially related notes
mcp__basic-memory__search_notes(
    query="key terms from the note",
    project="main"
)

# Suggest relations based on:
# - Shared topics or concepts
# - Complementary content (problem/solution, question/answer)
# - Sequential relationship (part 1, part 2)
# - Hierarchical (parent concept, child detail)
```

**Relation types to suggest:**
- `relates-to` - General topical connection
- `extends` - Builds upon or expands
- `implements` - Realizes a concept
- `depends-on` - Requires understanding of
- `contradicts` - Presents alternative view
- `learned-from` - Source of insight
- `enables` - Makes something possible

### 3. Identify Similar/Duplicate Notes

Find notes that may cover the same topic.

```python
# Search for notes with similar titles or content
mcp__basic-memory__search_notes(
    query="topic keywords",
    project="main"
)

# Compare results for overlap
# Look for:
# - Similar titles
# - Overlapping observations
# - Same tags
# - Related timestamps (created around same time)
```

**Actions for duplicates:**
- Merge into a single comprehensive note
- Link them with `supersedes` or `updates` relations
- Differentiate by adding context about their distinct focus

### 4. Folder Organization Review

Analyze folder structure and suggest improvements.

```python
# List directory structure
mcp__basic-memory__list_directory(
    dir_name="/",
    depth=3,
    project="main"
)

# Identify:
# - Overcrowded folders
# - Single-note folders
# - Inconsistent naming
# - Notes that might belong elsewhere
```

**Organization suggestions:**
- Group related notes into topic folders
- Create subfolders for large categories
- Suggest consistent naming conventions
- Move misplaced notes

### 5. Tag Consistency

Review and normalize tags across notes.

```python
# Search notes to analyze tag patterns
mcp__basic-memory__search_notes(
    query="*",
    page_size=100,
    project="main"
)

# Look for:
# - Similar tags (architecture vs arch)
# - Unused tags
# - Over-used generic tags
# - Missing tags on relevant notes
```

**Tag improvements:**
- Suggest tag standardization (pick one variant)
- Propose new tags for common themes
- Identify notes missing obvious tags

### 6. Create Index/Hub Notes

Generate notes that serve as navigation hubs for related topics.

```python
# After identifying a cluster of related notes
mcp__basic-memory__write_note(
    title="Architecture Decisions Index",
    content="""---
title: Architecture Decisions Index
type: index
tags:
- architecture
- index
---

# Architecture Decisions Index

A hub linking all architecture-related decisions and patterns.

## Decisions

- [[Database Selection Decision]]
- [[API Design Patterns]]
- [[Authentication Architecture]]

## Patterns

- [[Repository Pattern]]
- [[Async Client Pattern]]

## Observations

- [index] Central hub for architecture knowledge #navigation

## Relations

- indexes [[Architecture]]
""",
    folder="indexes",
    project="main"
)
```

### 7. Enrich Sparse Notes

Find notes lacking observations or structure and suggest improvements.

```python
# Read a sparse note
mcp__basic-memory__read_note(
    identifier="sparse-note",
    project="main"
)

# If missing:
# - Observations section → suggest categories
# - Relations section → suggest links
# - Tags → suggest relevant tags
# - Context → suggest adding background
```

## Organization Workflows

### Quick Health Check

A fast overview of knowledge base status:

1. Count total notes
2. Identify orphan count
3. List recently modified
4. Check for obvious duplicates
5. Report folder distribution

### Deep Organization Session

Thorough review and improvement:

1. **Audit phase** - Catalog all notes, identify issues
2. **Orphan phase** - Address unlinked notes
3. **Relation phase** - Suggest new connections
4. **Duplicate phase** - Merge or differentiate similar notes
5. **Structure phase** - Reorganize folders if needed
6. **Index phase** - Create hub notes for major topics

### Topic-Focused Organization

Organize around a specific subject:

1. Find all notes related to topic
2. Map existing relations
3. Identify gaps in the topic graph
4. Suggest new notes to fill gaps
5. Create topic index note

## Best Practices

1. **Work incrementally** - Don't reorganize everything at once
2. **Confirm before changing** - Always ask before moving/editing notes
3. **Preserve permalinks** - Moving is okay, changing permalinks breaks links
4. **Show the graph** - Help user visualize connections
5. **Explain suggestions** - Say why a relation makes sense
6. **Respect user's system** - Enhance their organization, don't impose a new one

## Example Conversations

**User:** "Help me organize my notes"

**Claude:**
1. Runs health check on the knowledge base
2. Reports: "You have 47 notes. I found 12 orphan notes and 3 potential duplicates."
3. Asks: "Would you like to start by connecting the orphan notes, or review the duplicates first?"

**User:** "Find notes that should be linked to my API design note"

**Claude:**
1. Reads the API design note
2. Searches for related content
3. Suggests: "I found 5 notes that could relate:
   - 'REST Best Practices' → relates-to
   - 'Authentication Flow' → implements
   - 'Rate Limiting Decision' → extends
   Would you like me to add any of these relations?"

**User:** "Are there any notes about similar topics?"

**Claude:**
1. Analyzes note titles and content
2. Identifies clusters of similar notes
3. Reports: "I found these potential overlaps:
   - 'Auth Flow' and 'Authentication Design' cover similar ground
   - 'DB Schema v1' and 'DB Schema v2' might need a 'supersedes' relation
   Would you like to review any of these?"
