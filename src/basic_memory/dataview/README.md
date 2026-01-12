# Dataview Query Parser and Executor

This module provides parsing and execution of Dataview queries for Basic Memory.

## Features

### Phase 1: Parser ✅
- **Lexer**: Tokenizes Dataview queries
- **Parser**: Builds Abstract Syntax Tree (AST)
- **Detector**: Finds Dataview queries in markdown
- **AST**: Complete query representation
- **Errors**: Custom exception types

### Phase 2: Executor ✅
- **Field Resolver**: Resolves field values from notes
- **Expression Evaluator**: Evaluates query expressions
- **Task Extractor**: Extracts tasks from markdown
- **Executor**: Executes queries against note collections
- **Result Formatter**: Formats results as markdown

## Architecture

```
dataview/
├── __init__.py           # Public API
├── errors.py             # Custom exceptions
├── ast.py                # AST node definitions
├── lexer.py              # Tokenizer
├── parser.py             # Parser
├── detector.py           # Query detector
└── executor/
    ├── __init__.py
    ├── field_resolver.py     # Field resolution
    ├── expression_eval.py    # Expression evaluation
    ├── task_extractor.py     # Task extraction
    ├── executor.py           # Main executor
    └── result_formatter.py   # Result formatting
```

## Usage

### Parsing

```python
from basic_memory.dataview import DataviewParser

query_text = '''
TABLE file.name, status, priority
FROM "projects"
WHERE status = "active"
SORT priority DESC
LIMIT 10
'''

query = DataviewParser.parse(query_text)
print(query.query_type)  # QueryType.TABLE
print(query.fields)      # List of TableField objects
print(query.from_source) # "projects"
```

### Detecting Queries

```python
from basic_memory.dataview import DataviewDetector

markdown = '''
# My Note

```dataview
TABLE file.name FROM "projects"
```
'''

blocks = DataviewDetector.detect_queries(markdown)
for block in blocks:
    print(f"Found query at lines {block.start_line}-{block.end_line}")
```

### Executing Queries

```python
from basic_memory.dataview import DataviewParser
from basic_memory.dataview.executor import DataviewExecutor

# Parse query
query = DataviewParser.parse('TABLE file.name, status FROM "projects"')

# Prepare notes
notes = [
    {"title": "Project A", "path": "projects/a.md", "frontmatter": {"status": "active"}},
    {"title": "Project B", "path": "projects/b.md", "frontmatter": {"status": "done"}},
]

# Execute
executor = DataviewExecutor(notes)
result = executor.execute(query)
print(result)  # Markdown table
```

## Supported Query Types

- ✅ **TABLE**: Tabular data with custom fields
- ✅ **LIST**: Simple list of notes
- ✅ **TASK**: Task list extraction
- ⏳ **CALENDAR**: Calendar view (future)

## Supported Clauses

- ✅ **FROM**: Filter by path/folder
- ✅ **WHERE**: Filter by conditions
- ✅ **SORT**: Sort results
- ✅ **LIMIT**: Limit number of results
- ⏳ **GROUP BY**: Group results (future)
- ⏳ **FLATTEN**: Flatten arrays (future)

## Supported Operators

- Comparison: `=`, `!=`, `<`, `>`, `<=`, `>=`
- Logical: `AND`, `OR`, `NOT`
- Functions: `contains()`, `length()`, `lower()`, `upper()`

## Field Resolution

Special fields:
- `file.name`: Note title
- `file.link`: Wikilink to note
- `file.path`: Full path
- `file.folder`: Parent folder
- `file.size`: File size
- `file.ctime`: Creation time
- `file.mtime`: Modification time

Frontmatter fields are accessed directly by name.

## Testing

```bash
# Run tests
pytest tests/dataview/

# Run specific test
pytest tests/dataview/test_parser.py -v
```

## Integration with Basic Memory

This module is designed to integrate with Basic Memory's MCP server to provide
Dataview query execution for notes stored in the vault.

See `integration.py` for MCP integration details.

## Status

- ✅ Phase 1: Parser (Complete)
- ✅ Phase 2: Executor (Complete)
- ⏳ Phase 3: Tests (In Progress)
- ⏳ Phase 4: MCP Integration (Planned)

## License

Same as Basic Memory project.
