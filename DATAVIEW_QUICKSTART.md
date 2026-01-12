# Dataview Quick Start

## Installation

The Dataview module is now part of Basic Memory at:
```
/Users/donaldo/Developer/basic-memory/src/basic_memory/dataview/
```

## Quick Test

```python
# Test the parser
from basic_memory.dataview import DataviewParser

query = DataviewParser.parse('TABLE file.name, status FROM "projects"')
print(f"Query type: {query.query_type.value}")
print(f"Fields: {len(query.fields)}")
print(f"FROM: {query.from_source}")
```

## Structure

```
src/basic_memory/dataview/
├── __init__.py              # Public API
├── README.md                # Full documentation
├── errors.py                # Exceptions
├── ast.py                   # AST definitions
├── lexer.py                 # Tokenizer
├── parser.py                # Parser
├── detector.py              # Query detector
└── executor/                # Query execution
    ├── __init__.py
    ├── field_resolver.py
    ├── expression_eval.py
    ├── task_extractor.py
    ├── executor.py
    └── result_formatter.py
```

## Features

✅ **Parser** : Tokenize and parse Dataview queries
✅ **Detector** : Find queries in markdown
✅ **Executor** : Execute queries against notes
✅ **Formatter** : Format results as markdown

## Next Steps

1. **Run tests** : `pytest tests/dataview/ -v` (after creating tests)
2. **MCP Integration** : Create `integration.py` for MCP server
3. **Documentation** : See `src/basic_memory/dataview/README.md`

## Status

- ✅ Phase 1: Parser (Complete)
- ✅ Phase 2: Executor (Complete)
- ⏳ Phase 3: Tests (To create)
- ⏳ Phase 4: MCP Integration (To create)

Total: ~2,600 lines of code, 12 Python files
