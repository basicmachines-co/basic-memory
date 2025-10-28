---
title: Build Context
type: guide
permalink: tools/build_context
tags:
- mcp-tools
- reference
- build_context
- context
- knowledge-graph
created: 2025-10-28T00:00:00
modified: 2025-10-28T00:00:00
---

# Build Context

Navigate the knowledge graph via memory:// URLs to build context for conversation continuity.

## Function Signature

```python
build_context(url, depth=1, timeframe=None, project=None)
```

## Parameters

- **url** (str): memory:// URL to start context building from
- **depth** (int): How many relation levels to follow (default 1)
- **timeframe** (str, optional): Filter by recency (e.g., "1w", "2d", "2024-01-01")
- **project** (str, optional): Project name to use

## Returns

Contextual information from knowledge graph traversal including:
- Starting entity content
- Related entities (based on depth)
- Temporal filtering if timeframe specified

## Observations

- [tool] MCP tool for navigating knowledge graph via memory:// URLs #mcp #navigation
- [returns] Contextual information from knowledge graph traversal #output
- [category] Knowledge Graph Navigation tool #classification
- [feature] Follows relations to specified depth #graph-traversal
- [feature] Filters by timeframe for recent context #temporal-filter
- [purpose] Enables conversation continuity across sessions #continuity

## Usage Examples

### Basic Context Building

```python
# Build context from a starting point
build_context("memory://projects/current-project")

# Build context from a specific note
build_context("memory://meetings/weekly-standup")
```

### With Depth Control

```python
# Follow relations to depth of 2
build_context("memory://concepts/machine-learning", depth=2)

# Shallow context (depth=1, default)
build_context("memory://notes/today")

# Deep context traversal
build_context("memory://projects/main", depth=3)
```

### With Timeframe Filter

```python
# Recent context from last week
build_context("memory://meetings/weekly", timeframe="1w")

# Context from last 2 days
build_context("memory://projects/current", timeframe="2d")

# Context since specific date
build_context("memory://research/ml", timeframe="2024-01-01")
```

### For Conversation Continuity

```python
# Load previous conversation context
build_context("memory://conversations/project-discussion", depth=3)

# Continue research topic
build_context("memory://research/literature-review", depth=2, timeframe="1w")
```

### Combining Parameters

```python
# Deep, recent context
build_context(
    "memory://projects/active",
    depth=3,
    timeframe="1w"
)

# Focused context for specific topic
build_context(
    "memory://concepts/neural-networks",
    depth=2,
    timeframe="2024-01-01"
)
```

## How It Works

1. **Starts at URL**: Loads the entity at the memory:// URL
2. **Follows Relations**: Traverses relations up to specified depth
3. **Filters by Time**: If timeframe specified, includes only recent updates
4. **Returns Context**: Aggregates all discovered content

### Depth Example

With depth=1:
```
Start → Related1
     → Related2
     → Related3
```

With depth=2:
```
Start → Related1 → SubRelated1
                 → SubRelated2
     → Related2 → SubRelated3
     → Related3 → SubRelated4
```

## Use Cases

### Continue Previous Conversations

```python
# Pick up where you left off
build_context("memory://conversations/last-discussion", depth=2)
```

### Research Topic with Context

```python
# Load all related research
build_context("memory://research/machine-learning", depth=3)
```

### Project Status Update

```python
# Get current project state
build_context("memory://projects/current", depth=2, timeframe="1w")
```

### Explore Related Concepts

```python
# Discover connections
build_context("memory://concepts/neural-networks", depth=2)
```

## memory:// URL Format

Memory URLs follow this pattern:
```
memory://[folder/]permalink
```

Examples:
- `memory://index`
- `memory://meetings/weekly-standup`
- `memory://projects/2024/q1-planning`
- `memory://concepts/machine-learning`

## Project Resolution

Server resolves projects in this order:
1. Single Project Mode
2. project parameter
3. default project

If project unknown, use `list_memory_projects()` or `recent_activity()` first.

## Relations

- part_of [[Knowledge Graph Navigation]]
- documented_in [[MCP Tool Reference]]
- complements [[Recent Activity]]
- complements [[Search Notes]]
- uses [[Relation Resolver]]
