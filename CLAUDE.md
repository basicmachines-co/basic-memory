# CLAUDE.md - Basic Memory Project Guide

## Project Overview

Basic Memory is a local-first knowledge management system built on the Model Context Protocol (MCP). It enables
bidirectional communication between LLMs (like Claude) and markdown files, creating a personal knowledge graph that can
be traversed using links between documents.

## CODEBASE DEVELOPMENT

### Project information

See the [README.md](README.md) file for a project overview.

### Build and Test Commands

- Install: `just install` or `pip install -e ".[dev]"`
- Run all tests (SQLite + Postgres): `just test`
- Run all tests against SQLite: `just test-sqlite`
- Run all tests against Postgres: `just test-postgres` (uses testcontainers)
- Run unit tests (SQLite): `just test-unit-sqlite`
- Run unit tests (Postgres): `just test-unit-postgres`
- Run integration tests (SQLite): `just test-int-sqlite`
- Run integration tests (Postgres): `just test-int-postgres`
- Generate HTML coverage: `just coverage`
- Single test: `pytest tests/path/to/test_file.py::test_function_name`
- Run benchmarks: `pytest test-int/test_sync_performance_benchmark.py -v -m "benchmark and not slow"`
- Lint: `just lint` or `ruff check . --fix`
- Type check: `just typecheck` or `uv run pyright`
- Format: `just format` or `uv run ruff format .`
- Run all code checks: `just check` (runs lint, format, typecheck, test)
- Create db migration: `just migration "Your migration message"`
- Run development MCP Inspector: `just run-inspector`

**Note:** Project requires Python 3.12+ (uses type parameter syntax and `type` aliases introduced in 3.12)

**Postgres Testing:** Uses [testcontainers](https://testcontainers-python.readthedocs.io/) which automatically spins up a Postgres instance in Docker. No manual database setup required - just have Docker running.

### Test Structure

- `tests/` - Unit tests for individual components (mocked, fast)
- `test-int/` - Integration tests for real-world scenarios (no mocks, realistic)
- Both directories are covered by unified coverage reporting
- Benchmark tests in `test-int/` are marked with `@pytest.mark.benchmark`
- Slow tests are marked with `@pytest.mark.slow`

### Code Style Guidelines

- Line length: 100 characters max
- Python 3.12+ with full type annotations (uses type parameters and type aliases)
- Format with ruff (consistent styling)
- Import order: standard lib, third-party, local imports
- Naming: snake_case for functions/variables, PascalCase for classes
- Prefer async patterns with SQLAlchemy 2.0
- Use Pydantic v2 for data validation and schemas
- CLI uses Typer for command structure
- API uses FastAPI for endpoints
- Follow the repository pattern for data access
- Tools communicate to api routers via the httpx ASGI client (in process)

### Code Change Guidelines

- **Full file read before edits**: Before editing any file, read it in full first to ensure complete context; partial reads lead to corrupted edits
- **Minimize diffs**: Prefer the smallest change that satisfies the request. Avoid unrelated refactors or style rewrites unless necessary for correctness
- **No speculative getattr**: Never use `getattr(obj, "attr", default)` when unsure about attribute names. Check the class definition or source code first
- **Fail fast**: Write code with fail-fast logic by default. Do not swallow exceptions with errors or warnings
- **No fallback logic**: Do not add fallback logic unless explicitly told to and agreed with the user
- **No guessing**: Do not say "The issue is..." before you actually know what the issue is. Investigate first.

### Literate Programming Style

Code should tell a story. Comments must explain the "why" and narrative flow, not just the "what".

**Section Headers:**
For files with multiple phases of logic, add section headers so the control flow reads like chapters:
```python
# --- Authentication ---
# ... auth logic ...

# --- Data Validation ---
# ... validation logic ...

# --- Business Logic ---
# ... core logic ...
```

**Decision Point Comments:**
For conditionals that materially change behavior (gates, fallbacks, retries, feature flags), add comments with:
- **Trigger**: what condition causes this branch
- **Why**: the rationale (cost, correctness, UX, determinism)
- **Outcome**: what changes downstream

```python
# Trigger: project has no active sync watcher
# Why: avoid duplicate file system watchers consuming resources
# Outcome: starts new watcher, registers in active_watchers dict
if project_id not in active_watchers:
    start_watcher(project_id)
```

**Constraint Comments:**
If code exists because of a constraint (async requirements, rate limits, schema compatibility), explain the constraint near the code:
```python
# SQLite requires WAL mode for concurrent read/write access
connection.execute("PRAGMA journal_mode=WAL")
```

**What NOT to Comment:**
Avoid comments that restate obvious code:
```python
# Bad - restates code
counter += 1  # increment counter

# Good - explains why
counter += 1  # track retries for backoff calculation
```

### Codebase Architecture

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for detailed architecture documentation.

**Directory Structure:**
- `/alembic` - Alembic db migrations
- `/api` - FastAPI REST endpoints + `container.py` composition root
- `/cli` - Typer CLI + `container.py` composition root
- `/deps` - Feature-scoped FastAPI dependencies (config, db, projects, repositories, services, importers)
- `/importers` - Import functionality for Claude, ChatGPT, and other sources
- `/markdown` - Markdown parsing and processing
- `/mcp` - MCP server + `container.py` composition root + `clients/` typed API clients
- `/models` - SQLAlchemy ORM models
- `/repository` - Data access layer
- `/schemas` - Pydantic models for validation
- `/services` - Business logic layer
- `/sync` - File synchronization services + `coordinator.py` for lifecycle management

**Composition Roots:**
Each entrypoint (API, MCP, CLI) has a composition root that:
- Reads `ConfigManager` (the only place that reads global config)
- Resolves runtime mode via `RuntimeMode` enum (TEST > CLOUD > LOCAL)
- Provides dependencies to downstream code explicitly

**Typed API Clients (MCP):**
MCP tools use typed clients in `mcp/clients/` to communicate with the API:
- `KnowledgeClient` - Entity CRUD operations
- `SearchClient` - Search operations
- `MemoryClient` - Context building
- `DirectoryClient` - Directory listing
- `ResourceClient` - Resource reading
- `ProjectClient` - Project management

Flow: MCP Tool → Typed Client → HTTP API → Router → Service → Repository

### Development Notes

- MCP tools are defined in src/basic_memory/mcp/tools/
- MCP prompts are defined in src/basic_memory/mcp/prompts/
- MCP tools should be atomic, composable operations
- Use `textwrap.dedent()` for multi-line string formatting in prompts and tools
- MCP Prompts are used to invoke tools and format content with instructions for an LLM
- Schema changes require Alembic migrations
- SQLite is used for indexing and full text search, files are source of truth
- Testing uses pytest with asyncio support (strict mode)
- Unit tests (`tests/`) use mocks when necessary; integration tests (`test-int/`) use real implementations
- By default, tests run against SQLite (fast, no Docker needed)
- Set `BASIC_MEMORY_TEST_POSTGRES=1` to run against Postgres (uses testcontainers - Docker required)
- Each test runs in a standalone environment with isolated database and tmp_path directory
- CI runs SQLite and Postgres tests in parallel for faster feedback
- Performance benchmarks are in `test-int/test_sync_performance_benchmark.py`
- Use pytest markers: `@pytest.mark.benchmark` for benchmarks, `@pytest.mark.slow` for slow tests
- **Coverage must stay at 100%**: Write tests for new code. Only use `# pragma: no cover` when tests would require excessive mocking (e.g., TYPE_CHECKING blocks, error handlers that need failure injection, runtime-mode-dependent code paths)

### Async Client Pattern (Important!)

**All MCP tools and CLI commands use the context manager pattern for HTTP clients:**

```python
from basic_memory.mcp.async_client import get_client

async def my_mcp_tool():
    async with get_client() as client:
        # Use client for API calls
        response = await call_get(client, "/path")
        return response
```

**Do NOT use:**
- ❌ `from basic_memory.mcp.async_client import client` (deprecated module-level client)
- ❌ Manual auth header management
- ❌ `inject_auth_header()` (deleted)

**Key principles:**
- Auth happens at client creation, not per-request
- Proper resource management via context managers
- Supports three modes: Local (ASGI), CLI cloud (HTTP + auth), Cloud app (factory injection)
- Factory pattern enables dependency injection for cloud consolidation

**For cloud app integration:**
```python
from basic_memory.mcp import async_client

# Set custom factory before importing tools
async_client.set_client_factory(your_custom_factory)
```

See SPEC-16 for full context manager refactor details.

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

**Local Commands:**
- Check sync status: `basic-memory status`
- Import from Claude: `basic-memory import claude conversations`
- Import from ChatGPT: `basic-memory import chatgpt`
- Import from Memory JSON: `basic-memory import memory-json`
- Tool access: `basic-memory tool` (provides CLI access to MCP tools)
    - Continue: `basic-memory tool continue-conversation --topic="search"`

**Project Management:**
- List projects: `basic-memory project list`
- Add project: `basic-memory project add "name" ~/path`
- Project info: `basic-memory project info`
- One-way sync (local -> cloud): `basic-memory project sync`
- Bidirectional sync: `basic-memory project bisync`
- Integrity check: `basic-memory project check`

**Cloud Commands (requires subscription):**
- Authenticate: `basic-memory cloud login`
- Logout: `basic-memory cloud logout`
- Check cloud status: `basic-memory cloud status`
- Setup cloud sync: `basic-memory cloud setup`

### MCP Capabilities

- Basic Memory exposes these MCP tools to LLMs:

  **Content Management:**
    - `write_note(title, content, folder, tags)` - Create/update markdown notes with semantic observations and relations
    - `read_note(identifier, page, page_size)` - Read notes by title, permalink, or memory:// URL with knowledge graph awareness
    - `read_content(path)` - Read raw file content (text, images, binaries) without knowledge graph processing
    - `view_note(identifier, page, page_size)` - View notes as formatted artifacts for better readability
    - `edit_note(identifier, operation, content)` - Edit notes incrementally (append, prepend, find/replace, replace_section)
    - `move_note(identifier, destination_path)` - Move notes to new locations, updating database and maintaining links
    - `delete_note(identifier)` - Delete notes from the knowledge base

  **Knowledge Graph Navigation:**
    - `build_context(url, depth, timeframe)` - Navigate the knowledge graph via memory:// URLs for conversation continuity
    - `recent_activity(type, depth, timeframe)` - Get recently updated information with specified timeframe (e.g., "1d", "1 week")
    - `list_directory(dir_name, depth, file_name_glob)` - Browse directory contents with filtering and depth control

  **Search & Discovery:**
    - `search_notes(query, page, page_size, search_type, types, entity_types, after_date)` - Full-text search across all content with advanced filtering options

  **Project Management:**
    - `list_memory_projects()` - List all available projects with their status
    - `create_memory_project(project_name, project_path, set_default)` - Create new Basic Memory projects
    - `delete_project(project_name)` - Delete a project from configuration

  **Visualization:**
    - `canvas(nodes, edges, title, folder)` - Generate Obsidian canvas files for knowledge graph visualization

  **ChatGPT-Compatible Tools:**
    - `search(query)` - Search across knowledge base (OpenAI actions compatible)
    - `fetch(id)` - Fetch full content of a search result document

- MCP Prompts for better AI interaction:
    - `ai_assistant_guide()` - Guidance on effectively using Basic Memory tools for AI assistants
    - `continue_conversation(topic, timeframe)` - Continue previous conversations with relevant historical context
    - `search(query, after_date)` - Search with detailed, formatted results for better context understanding
    - `recent_activity(timeframe)` - View recently changed items with formatted output

### Cloud Features (v0.15.0+)

Basic Memory now supports cloud synchronization and storage (requires active subscription):

**Authentication:**
- JWT-based authentication with subscription validation
- Secure session management with token refresh
- Support for multiple cloud projects

**Bidirectional Sync:**
- rclone bisync integration for two-way synchronization
- Conflict resolution and integrity verification
- Real-time sync with change detection
- Mount/unmount cloud storage for direct file access

**Cloud Project Management:**
- Create and manage projects in the cloud
- Toggle between local and cloud modes
- Per-project sync configuration
- Subscription-based access control

**Security & Performance:**
- Removed .env file loading for improved security
- .gitignore integration (respects gitignored files)
- WAL mode for SQLite performance
- Background relation resolution (non-blocking startup)
- API performance optimizations (SPEC-11)

## AI-Human Collaborative Development

Basic Memory emerged from and enables a new kind of development process that combines human and AI capabilities. Instead
of using AI just for code generation, we've developed a true collaborative workflow:

1. AI (LLM) writes initial implementation based on specifications and context
2. Human reviews, runs tests, and commits code with any necessary adjustments
3. Knowledge persists across conversations using Basic Memory's knowledge graph
4. Development continues seamlessly across different AI sessions with consistent context
5. Results improve through iterative collaboration and shared understanding

This approach has allowed us to tackle more complex challenges and build a more robust system than either humans or AI
could achieve independently.

**Problem-Solving Guidance:**
- If a solution isn't working after reasonable effort, suggest alternative approaches
- Don't persist with a problematic library or pattern when better alternatives exist
- Example: When py-pglite caused cascading test failures, switching to testcontainers-postgres was the right call

## GitHub Integration

Basic Memory has taken AI-Human collaboration to the next level by integrating Claude directly into the development workflow through GitHub:

### GitHub MCP Tools

Using the GitHub Model Context Protocol server, Claude can now:

- **Repository Management**:
  - View repository files and structure
  - Read file contents
  - Create new branches
  - Create and update files

- **Issue Management**:
  - Create new issues
  - Comment on existing issues
  - Close and update issues
  - Search across issues

- **Pull Request Workflow**:
  - Create pull requests
  - Review code changes
  - Add comments to PRs

This integration enables Claude to participate as a full team member in the development process, not just as a code generation tool. Claude's GitHub account ([bm-claudeai](https://github.com/bm-claudeai)) is a member of the Basic Machines organization with direct contributor access to the codebase.

### Collaborative Development Process

With GitHub integration, the development workflow includes:

1. **Direct code review** - Claude can analyze PRs and provide detailed feedback
2. **Contribution tracking** - All of Claude's contributions are properly attributed in the Git history
3. **Branch management** - Claude can create feature branches for implementations
4. **Documentation maintenance** - Claude can keep documentation updated as the code evolves
5. **Code Commits**: ALWAYS sign off commits with `git commit -s`

This level of integration represents a new paradigm in AI-human collaboration, where the AI assistant becomes a full-fledged team member rather than just a tool for generating code snippets.
