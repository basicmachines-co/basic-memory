# Dataview MCP Integration

This document describes how Dataview queries are integrated into Basic Memory's MCP tools.

## Overview

The Dataview integration allows MCP tools (`read_note`, `search_notes`, `build_context`) to automatically detect and execute Dataview queries embedded in markdown notes.

## Architecture

```
┌─────────────────┐
│   MCP Tools     │
│  (read_note,    │
│  search_notes,  │
│  build_context) │
└────────┬────────┘
         │
         ▼
┌─────────────────────────┐
│ DataviewIntegration     │
│  - Detect queries       │
│  - Parse & execute      │
│  - Format results       │
└────────┬────────────────┘
         │
         ├──────────────────┐
         ▼                  ▼
┌──────────────┐   ┌──────────────┐
│   Detector   │   │   Executor   │
│  (find       │   │  (run        │
│   queries)   │   │   queries)   │
└──────────────┘   └──────────────┘
```

## Usage

### read_note

Execute Dataview queries when reading a note (enabled by default):

```python
# With Dataview execution (default)
result = await read_note("my-project", "notes/project-status")

# Without Dataview execution
result = await read_note("my-project", "notes/project-status", enable_dataview=False)
```

**Output format:**
```markdown
# Original Note Content

... note content ...

---

## Dataview Query Results

*Found 2 Dataview queries*

### Query dv-1 (Line 15)

**Type:** LIST  
**Status:** success  
**Execution time:** 12ms  

**Results:** 5 item(s)

- [[Project A]]
- [[Project B]]
- [[Project C]]

**Discovered links:** 3

---

### Query dv-2 (Line 25)

**Type:** TABLE  
**Status:** success  
**Execution time:** 8ms  

**Results:** 3 item(s)

| file.name | status |
|-----------|--------|
| Task 1    | Done   |
| Task 2    | In Progress |

---
```

### search_notes

Execute Dataview queries in search results (disabled by default for performance):

```python
# Without Dataview (default, faster)
results = await search_notes("project planning")

# With Dataview execution
results = await search_notes("project planning", enable_dataview=True)
```

**Output format:**

The `SearchResponse` object includes Dataview results in the `metadata` field of each `SearchResult`:

```python
{
    "results": [
        {
            "title": "Project Status",
            "content": "...",
            "metadata": {
                "dataview_results": [
                    {
                        "query_id": "dv-1",
                        "query_type": "LIST",
                        "status": "success",
                        "result_count": 5,
                        "execution_time_ms": 12
                    }
                ],
                "dataview_query_count": 1
            }
        }
    ]
}
```

### build_context

Execute Dataview queries in context notes (enabled by default):

```python
# With Dataview execution (default)
context = await build_context("memory://projects/basic-memory")

# Without Dataview execution
context = await build_context("memory://projects/basic-memory", enable_dataview=False)
```

**Output format:**

The `GraphContext` object includes Dataview summaries appended to entity/observation content:

```markdown
# Entity Content

... original content ...

---
**Dataview:** 2 queries executed
```

## Integration Details

### DataviewIntegration Class

The main integration class that bridges MCP tools and Dataview execution:

```python
from basic_memory.dataview.integration import create_dataview_integration

# Create integration
integration = create_dataview_integration()

# Process a note
results = integration.process_note(note_content)
```

### Result Format

Each executed query returns a dictionary with:

```python
{
    "query_id": str,              # Unique ID (e.g., "dv-1")
    "query_type": str,            # "LIST", "TABLE", or "TASK"
    "query_source": str,          # Original query with markdown formatting
    "line_number": int,           # Line where query appears
    "status": str,                # "success" or "error"
    "result_markdown": str,       # Formatted results (if success)
    "result_count": int,          # Number of results
    "discovered_links": list,     # Extracted links for graph traversal
    "execution_time_ms": int,     # Execution time in milliseconds
    "error": str,                 # Error message (if status == "error")
    "error_type": str,            # "syntax", "execution", or "unexpected"
}
```

### Discovered Links

The integration extracts links from query results for graph traversal:

```python
{
    "discovered_links": [
        {
            "target": "Project A",
            "type": "note",
            "metadata": {
                "status": "active",
                "priority": "high"
            }
        },
        {
            "target": "Fix bug in parser",
            "type": "task",
            "metadata": {
                "completed": false
            }
        }
    ]
}
```

## Error Handling

The integration handles errors gracefully:

1. **Syntax errors**: Returned as error results, don't crash the tool
2. **Execution errors**: Logged and returned as error results
3. **Unexpected errors**: Caught and logged, original content returned

Example error result:

```python
{
    "query_id": "dv-1",
    "query_type": "unknown",
    "status": "error",
    "error": "Unexpected token 'INVALID'",
    "error_type": "syntax",
    "result_count": 0,
    "execution_time_ms": 2
}
```

## Performance Considerations

### Defaults

- `read_note`: **enabled** (users typically read one note at a time)
- `search_notes`: **disabled** (can return many notes, performance impact)
- `build_context`: **enabled** (context is already filtered and limited)

### Overhead

- Detection: < 1ms per note
- Parsing: 1-5ms per query
- Execution: 5-50ms per query (depends on data size)

### Optimization Tips

1. **Disable for large searches**: Use `enable_dataview=False` when searching many notes
2. **Limit query complexity**: Simple queries execute faster
3. **Use pagination**: Limit `page_size` to reduce processing

## Backward Compatibility

The integration is fully backward compatible:

- All existing MCP tool calls work without modification
- `enable_dataview` parameter is optional with sensible defaults
- Errors in Dataview execution don't break the tools
- Original content is always returned, Dataview results are additive

## Testing

Run the integration tests:

```bash
uv run pytest tests/dataview/test_mcp_integration.py -v
```

Test coverage:

- ✅ Query detection (codeblock and inline)
- ✅ Query execution (LIST, TABLE, TASK)
- ✅ Error handling (syntax, execution, unexpected)
- ✅ Result formatting
- ✅ Link extraction
- ✅ Performance tracking
- ✅ MCP tool integration
- ✅ Backward compatibility

## Examples

### Example 1: Project Dashboard

**Note content:**
```markdown
# Project Dashboard

## Active Projects

```dataview
LIST FROM "1. projects" WHERE status = "active"
```

## Recent Tasks

```dataview
TASK FROM "1. projects" WHERE !completed
SORT due ASC
LIMIT 10
```
```

**MCP call:**
```python
content = await read_note("my-vault", "dashboards/project-dashboard")
```

**Result:**
- Original content with 2 Dataview query results appended
- Execution time for each query
- Discovered links to active projects and tasks

### Example 2: Search with Dataview

**MCP call:**
```python
results = await search_notes(
    "project status",
    enable_dataview=True,
    page_size=5
)
```

**Result:**
- 5 search results
- Each result includes `dataview_results` in metadata (if queries found)
- Total execution time tracked per query

### Example 3: Context Building

**MCP call:**
```python
context = await build_context(
    "memory://projects/basic-memory",
    depth=2,
    enable_dataview=True
)
```

**Result:**
- Graph context with primary and related results
- Dataview summaries appended to entity content
- Links extracted for further traversal

## Future Enhancements

Potential improvements:

1. **Caching**: Cache query results for frequently accessed notes
2. **Async execution**: Execute multiple queries in parallel
3. **Result streaming**: Stream results for large queries
4. **Query optimization**: Analyze and optimize slow queries
5. **Custom formatters**: Allow custom result formatting
6. **Query validation**: Validate queries before execution

## See Also

- [Dataview README](README.md) - Core Dataview implementation
- [MCP Tools Documentation](../mcp/tools/README.md) - MCP tools overview
- [Integration Tests](../../tests/dataview/test_mcp_integration.py) - Test suite
