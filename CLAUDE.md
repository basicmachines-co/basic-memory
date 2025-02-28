# CLAUDE.md - Basic Memory Project Guide

## Project Overview

Basic Memory is a local-first knowledge management system built on the Model Context Protocol (MCP). It enables
bidirectional communication between LLMs (like Claude) and markdown files, creating a personal knowledge graph that can
be traversed using links between documents.

## CODEBASE DEVELOPMENT

### Build and Test Commands

- Install: `make install` or `pip install -e ".[dev]"`
- Run tests: `uv run pytest -p pytest_mock -v` or `make test`
- Single test: `pytest tests/path/to/test_file.py::test_function_name`
- Lint: `make lint` or `ruff check . --fix`
- Type check: `make type-check` or `uv run pyright`
- Format: `make format` or `uv run ruff format .`
- Run all code checks: `make check` (runs lint, format, type-check, test)
- Create db migration: `make migration m="Your migration message"`
- Run development MCP Inspector: `make run-inspector`

### Code Style Guidelines

- Line length: 100 characters max
- Python 3.12+ with full type annotations
- Format with ruff (consistent styling)
- Import order: standard lib, third-party, local imports
- Naming: snake_case for functions/variables, PascalCase for classes
- Prefer async patterns with SQLAlchemy 2.0
- Use Pydantic v2 for data validation and schemas
- CLI uses Typer for command structure
- API uses FastAPI for endpoints
- Follow the repository pattern for data access
- Tools communicate to api routers via the httpx asgi client (in process)

### Codebase Architecture

- `/alembic` - Alembic db migrations
- `/api` - FastAPI implementation of REST endpoints
- `/cli` - Typer command-line interface
- `/markdown` - Markdown parsing and processing
- `/mcp` - Model Context Protocol server implementation
- `/models` - SQLAlchemy ORM models
- `/repository` - Data access layer
- `/schemas` - Pydantic models for validation
- `/services` - Business logic layer
- `/sync` - File synchronization services

### Development Notes

- MCP tools are defined in src/basic_memory/mcp/tools/
- MCP prompts are defined in src/basic_memory/mcp/prompts/
- MCP tools should be atomic, composable operations
- Use `textwrap.dedent()` for multi-line string formatting in prompts and tools
- Prompts are special types of tools that format content for user consumption
- Schema changes require Alembic migrations
- SQLite is used for indexing and full text search, files are source of truth
- Testing uses pytest with asyncio support (strict mode)
- Test database uses in-memory SQLite

## BASIC MEMORY PRODUCT USAGE

### Knowledge Structure

- Entity: Any concept, document, or idea represented as a markdown file
- Observation: A categorized fact about an entity (`- [category] content`)
- Relation: A directional link between entities (`- relation_type [[Target]]`)
- Frontmatter: YAML metadata at the top of markdown files
- Knowledge representation follows precise markdown format:
    - Observations with [category] prefixes
    - Relations with WikiLinks [[Entity]]
    - Frontmatter with metadata

### Basic Memory Commands

- Sync knowledge: `basic-memory sync` or `basic-memory sync --watch`
- Import from Claude: `basic-memory import claude conversations`
- Import from ChatGPT: `basic-memory import chatgpt`
- Import from Memory JSON: `basic-memory import memory-json`
- Check sync status: `basic-memory status`
- Tool access: `basic-memory tools` (provides CLI access to MCP tools)
    - Guide: `basic-memory tools basic-memory-guide`
    - Continue: `basic-memory tools continue-conversation --topic="search"`

### MCP Capabilities

- Basic Memory exposes these MCP tools to LLMs:

    **Content Management:**
    - `write_note(title, content, folder, tags)` - Create/update markdown notes with semantic observations and relations
    - `read_note(identifier, page, page_size)` - Read notes by title, permalink, or memory:// URL with knowledge graph awareness
    - `read_file(path)` - Read raw file content (text, images, binaries) without knowledge graph processing

    **Knowledge Graph Navigation:**
    - `build_context(url, depth, timeframe)` - Navigate the knowledge graph via memory:// URLs for conversation continuity
    - `recent_activity(type, depth, timeframe)` - Get recently updated information with specified timeframe (e.g., "1d", "1 week")
    
    **Search & Discovery:**
    - `search(query, page, page_size)` - Full-text search across all content with filtering options
    
    **Visualization:**
    - `canvas(nodes, edges, title, folder)` - Generate Obsidian canvas files for knowledge graph visualization

- MCP Prompts for better AI interaction:
    - `ai_assistant_guide()` - Guidance on effectively using Basic Memory tools for AI assistants
    - `continue_conversation(topic, timeframe)` - Continue previous conversations with relevant historical context
    - `search(query, after_date)` - Search with detailed, formatted results for better context understanding
    - `recent_activity(timeframe)` - View recently changed items with formatted output
    - `json_canvas_spec()` - Full JSON Canvas specification for Obsidian visualization

## AI-Human Collaborative Development

Basic Memory emerged from and enables a new kind of development process that combines human and AI capabilities. Instead of using AI just for code generation, we've developed a true collaborative workflow:

1. AI (Claude) writes initial implementation based on specifications and context
2. Human reviews, runs tests, and commits code with any necessary adjustments
3. Knowledge persists across conversations using Basic Memory's knowledge graph
4. Development continues seamlessly across different AI sessions with consistent context
5. Results improve through iterative collaboration and shared understanding

This approach has allowed us to tackle more complex challenges and build a more robust system than either humans or AI could achieve independently.