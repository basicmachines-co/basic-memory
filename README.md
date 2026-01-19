[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![PyPI version](https://badge.fury.io/py/basic-memory.svg)](https://badge.fury.io/py/basic-memory)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Tests](https://github.com/basicmachines-co/basic-memory/workflows/Tests/badge.svg)](https://github.com/basicmachines-co/basic-memory/actions)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
![](https://badge.mcpx.dev?type=server 'MCP Server')
![](https://badge.mcpx.dev?type=dev 'MCP Dev')
[![smithery badge](https://smithery.ai/badge/@basicmachines-co/basic-memory)](https://smithery.ai/server/@basicmachines-co/basic-memory)

## 🚀 Basic Memory Cloud is Live!

- **Cross-device and multi-platform support is here.** Your knowledge graph now works on desktop, web, and mobile - seamlessly synced across all your AI tools (Claude, ChatGPT, Gemini, Claude Code, and Codex) 
- **Early Supporter Pricing:** Early users get 25% off forever. 
The open source project continues as always. Cloud just makes it work everywhere.

[Sign up now →](https://basicmemory.com/beta)

with a 7 day free trial

# Basic Memory

Basic Memory lets you build persistent knowledge through natural conversations with Large Language Models (LLMs) like
Claude, while keeping everything in simple Markdown files on your computer. It uses the Model Context Protocol (MCP) to
enable any compatible LLM to read and write to your local knowledge base.

- Website: https://basicmachines.co
- Documentation: https://memory.basicmachines.co

## Pick up your conversation right where you left off

- AI assistants can load context from local files in a new conversation
- Notes are saved locally as Markdown files in real time
- No project knowledge or special prompting required

https://github.com/user-attachments/assets/a55d8238-8dd0-454a-be4c-8860dbbd0ddc

## Quick Start

```bash
# Install with uv (recommended)
uv tool install basic-memory

# Configure Claude Desktop (edit ~/Library/Application Support/Claude/claude_desktop_config.json)
# Add this to your config:
{
  "mcpServers": {
    "basic-memory": {
      "command": "uvx",
      "args": [
        "basic-memory",
        "mcp"
      ]
    }
  }
}
# Now in Claude Desktop, you can:
# - Write notes with "Create a note about coffee brewing methods"
# - Read notes with "What do I know about pour over coffee?"
# - Search with "Find information about Ethiopian beans"

```

You can view shared context via files in `~/basic-memory` (default directory location).

### Alternative Installation via Smithery

You can use [Smithery](https://smithery.ai/server/@basicmachines-co/basic-memory) to automatically configure Basic
Memory for Claude Desktop:

```bash
npx -y @smithery/cli install @basicmachines-co/basic-memory --client claude
```

This installs and configures Basic Memory without requiring manual edits to the Claude Desktop configuration file. The
Smithery server hosts the MCP server component, while your data remains stored locally as Markdown files.

### Glama.ai

<a href="https://glama.ai/mcp/servers/o90kttu9ym">
  <img width="380" height="200" src="https://glama.ai/mcp/servers/o90kttu9ym/badge" alt="basic-memory MCP server" />
</a>

## Why Basic Memory?

Most LLM interactions are ephemeral - you ask a question, get an answer, and everything is forgotten. Each conversation
starts fresh, without the context or knowledge from previous ones. Current workarounds have limitations:

- Chat histories capture conversations but aren't structured knowledge
- RAG systems can query documents but don't let LLMs write back
- Vector databases require complex setups and often live in the cloud
- Knowledge graphs typically need specialized tools to maintain

Basic Memory addresses these problems with a simple approach: structured Markdown files that both humans and LLMs can
read
and write to. The key advantages:

- **Local-first:** All knowledge stays in files you control
- **Bi-directional:** Both you and the LLM read and write to the same files
- **Structured yet simple:** Uses familiar Markdown with semantic patterns
- **Traversable knowledge graph:** LLMs can follow links between topics
- **Standard formats:** Works with existing editors like Obsidian
- **Lightweight infrastructure:** Just local files indexed in a local SQLite database

With Basic Memory, you can:

- Have conversations that build on previous knowledge
- Create structured notes during natural conversations
- Have conversations with LLMs that remember what you've discussed before
- Navigate your knowledge graph semantically
- Keep everything local and under your control
- Use familiar tools like Obsidian to view and edit notes
- Build a personal knowledge base that grows over time
- Sync your knowledge to the cloud with bidirectional synchronization
- Authenticate and manage cloud projects with subscription validation
- Mount cloud storage for direct file access

## How It Works in Practice

Let's say you're exploring coffee brewing methods and want to capture your knowledge. Here's how it works:

1. Start by chatting normally:

```
I've been experimenting with different coffee brewing methods. Key things I've learned:

- Pour over gives more clarity in flavor than French press
- Water temperature is critical - around 205°F seems best
- Freshly ground beans make a huge difference
```

... continue conversation.

2. Ask the LLM to help structure this knowledge:

```
"Let's write a note about coffee brewing methods."
```

LLM creates a new Markdown file on your system (which you can see instantly in Obsidian or your editor):

```markdown
---
title: Coffee Brewing Methods
permalink: coffee-brewing-methods
tags:
- coffee
- brewing
---

# Coffee Brewing Methods

## Observations

- [method] Pour over provides more clarity and highlights subtle flavors
- [technique] Water temperature at 205°F (96°C) extracts optimal compounds
- [principle] Freshly ground beans preserve aromatics and flavor

## Relations

- relates_to [[Coffee Bean Origins]]
- requires [[Proper Grinding Technique]]
- affects [[Flavor Extraction]]
```

The note embeds semantic content and links to other topics via simple Markdown formatting.

3. You see this file on your computer in real time in the current project directory (default `~/$HOME/basic-memory`).

- Realtime sync can be enabled via running `basic-memory sync --watch`

4. In a chat with the LLM, you can reference a topic:

```
Look at `coffee-brewing-methods` for context about pour over coffee
```

The LLM can now build rich context from the knowledge graph. For example:

```
Following relation 'relates_to [[Coffee Bean Origins]]':
- Found information about Ethiopian Yirgacheffe
- Notes on Colombian beans' nutty profile
- Altitude effects on bean characteristics

Following relation 'requires [[Proper Grinding Technique]]':
- Burr vs. blade grinder comparisons
- Grind size recommendations for different methods
- Impact of consistent particle size on extraction
```

Each related document can lead to more context, building a rich semantic understanding of your knowledge base.

This creates a two-way flow where:

- Humans write and edit Markdown files
- LLMs read and write through the MCP protocol
- Sync keeps everything consistent
- All knowledge stays in local files.

## Technical Implementation

Under the hood, Basic Memory:

1. Stores everything in Markdown files
2. Uses a SQLite database for searching and indexing
3. Extracts semantic meaning from simple Markdown patterns
    - Files become `Entity` objects
    - Each `Entity` can have `Observations`, or facts associated with it
    - `Relations` connect entities together to form the knowledge graph
4. Maintains the local knowledge graph derived from the files
5. Provides bidirectional synchronization between files and the knowledge graph
6. Implements the Model Context Protocol (MCP) for AI integration
7. Exposes tools that let AI assistants traverse and manipulate the knowledge graph
8. Uses memory:// URLs to reference entities across tools and conversations

The file format is just Markdown with some simple markup:

Each Markdown file has:

### Frontmatter

```markdown
title: <Entity title>
type: <The type of Entity> (e.g. note)
permalink: <a uri slug>

- <optional metadata> (such as tags) 
```

### Observations

Observations are facts about a topic.
They can be added by creating a Markdown list with a special format that can reference a `category`, `tags` using a
"#" character, and an optional `context`.

Observation Markdown format:

```markdown
- [category] content #tag (optional context)
```

Examples of observations:

```markdown
- [method] Pour over extracts more floral notes than French press
- [tip] Grind size should be medium-fine for pour over #brewing
- [preference] Ethiopian beans have bright, fruity flavors (especially from Yirgacheffe)
- [fact] Lighter roasts generally contain more caffeine than dark roasts
- [experiment] Tried 1:15 coffee-to-water ratio with good results
- [resource] James Hoffman's V60 technique on YouTube is excellent
- [question] Does water temperature affect extraction of different compounds differently?
- [note] My favorite local shop uses a 30-second bloom time
```

### Relations

Relations are links to other topics. They define how entities connect in the knowledge graph.

Markdown format:

```markdown
- relation_type [[WikiLink]] (optional context)
```

Examples of relations:

```markdown
- pairs_well_with [[Chocolate Desserts]]
- grown_in [[Ethiopia]]
- contrasts_with [[Tea Brewing Methods]]
- requires [[Burr Grinder]]
- improves_with [[Fresh Beans]]
- relates_to [[Morning Routine]]
- inspired_by [[Japanese Coffee Culture]]
- documented_in [[Coffee Journal]]
```

## Using with VS Code

Add the following JSON block to your User Settings (JSON) file in VS Code. You can do this by pressing `Ctrl + Shift + P` and typing `Preferences: Open User Settings (JSON)`.

```json
{
  "mcp": {
    "servers": {
      "basic-memory": {
        "command": "uvx",
        "args": ["basic-memory", "mcp"]
      }
    }
  }
}
```

Optionally, you can add it to a file called `.vscode/mcp.json` in your workspace. This will allow you to share the configuration with others.

```json
{
  "servers": {
    "basic-memory": {
      "command": "uvx",
      "args": ["basic-memory", "mcp"]
    }
  }
}
```

You can use Basic Memory with VS Code to easily retrieve and store information while coding.

## Using with Claude Desktop

Basic Memory is built using the MCP (Model Context Protocol) and works with the Claude desktop app (https://claude.ai/):

1. Configure Claude Desktop to use Basic Memory:

Edit your MCP configuration file (usually located at `~/Library/Application Support/Claude/claude_desktop_config.json`
for OS X):

```json
{
  "mcpServers": {
    "basic-memory": {
      "command": "uvx",
      "args": [
        "basic-memory",
        "mcp"
      ]
    }
  }
}
```

If you want to use a specific project (see [Multiple Projects](#multiple-projects) below), update your Claude Desktop
config:

```json
{
  "mcpServers": {
    "basic-memory": {
      "command": "uvx",
      "args": [
        "basic-memory",
        "mcp",
        "--project",
        "your-project-name"
      ]
    }
  }
}
```

2. Sync your knowledge:

```bash
# One-time sync of local knowledge updates
basic-memory sync

# Run realtime sync process (recommended)
basic-memory sync --watch
```

3. Cloud features (optional, requires subscription):

```bash
# Authenticate with cloud
basic-memory cloud login

# Bidirectional sync with cloud
basic-memory cloud sync

# Verify cloud integrity
basic-memory cloud check

# Mount cloud storage
basic-memory cloud mount
```

4. In Claude Desktop, the LLM can now use these tools:

**Content Management:**
```
write_note(title, content, folder, tags) - Create or update notes
read_note(identifier, page, page_size) - Read notes by title or permalink
read_content(path) - Read raw file content (text, images, binaries)
view_note(identifier) - View notes as formatted artifacts
edit_note(identifier, operation, content) - Edit notes incrementally
move_note(identifier, destination_path) - Move notes with database consistency
delete_note(identifier) - Delete notes from knowledge base
```

**Knowledge Graph Navigation:**
```
build_context(url, depth, timeframe) - Navigate knowledge graph via memory:// URLs
recent_activity(type, depth, timeframe) - Find recently updated information
list_directory(dir_name, depth) - Browse directory contents with filtering
```

**Search & Discovery:**
```
search(query, page, page_size) - Search across your knowledge base
```

**Project Management:**
```
list_memory_projects() - List all available projects
create_memory_project(project_name, project_path) - Create new projects
get_current_project() - Show current project stats
sync_status() - Check synchronization status
```

**Visualization:**
```
canvas(nodes, edges, title, folder) - Generate knowledge visualizations
```

5. Example prompts to try:

```
"Create a note about our project architecture decisions"
"Find information about JWT authentication in my notes"
"Create a canvas visualization of my project components"
"Read my notes on the authentication system"
"What have I been working on in the past week?"
```

## Futher info

See the [Documentation](https://memory.basicmachines.co/) for more info, including:

- [Complete User Guide](https://docs.basicmemory.com/user-guide/)
- [CLI tools](https://docs.basicmemory.com/guides/cli-reference/)
- [Cloud CLI and Sync](https://docs.basicmemory.com/guides/cloud-cli/)
- [Managing multiple Projects](https://docs.basicmemory.com/guides/cli-reference/#project)
- [Importing data from OpenAI/Claude Projects](https://docs.basicmemory.com/guides/cli-reference/#import)

## Logging

Basic Memory uses [Loguru](https://github.com/Delgan/loguru) for logging. The logging behavior varies by entry point:

| Entry Point | Default Behavior | Use Case |
|-------------|------------------|----------|
| CLI commands | File only | Prevents log output from interfering with command output |
| MCP server | File only | Stdout would corrupt the JSON-RPC protocol |
| API server | File (local) or stdout (cloud) | Docker/cloud deployments use stdout |

**Log file location:** `~/.basic-memory/basic-memory.log` (10MB rotation, 10 days retention)

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `BASIC_MEMORY_LOG_LEVEL` | `INFO` | Log level: DEBUG, INFO, WARNING, ERROR |
| `BASIC_MEMORY_CLOUD_MODE` | `false` | When `true`, API logs to stdout with structured context |
| `BASIC_MEMORY_ENV` | `dev` | Set to `test` for test mode (stderr only) |

### Examples

```bash
# Enable debug logging
BASIC_MEMORY_LOG_LEVEL=DEBUG basic-memory sync

# View logs
tail -f ~/.basic-memory/basic-memory.log

# Cloud/Docker mode (stdout logging with structured context)
BASIC_MEMORY_CLOUD_MODE=true uvicorn basic_memory.api.app:app
```

## Development

### Running Tests

Basic Memory supports dual database backends (SQLite and Postgres). By default, tests run against SQLite. Set `BASIC_MEMORY_TEST_POSTGRES=1` to run against Postgres (uses testcontainers - Docker required).

**Quick Start:**
```bash
# Run all tests against SQLite (default, fast)
just test-sqlite

# Run all tests against Postgres (uses testcontainers)
just test-postgres

# Run both SQLite and Postgres tests
just test
```

**Available Test Commands:**

- `just test` - Run all tests against both SQLite and Postgres
- `just test-sqlite` - Run all tests against SQLite (fast, no Docker needed)
- `just test-postgres` - Run all tests against Postgres (uses testcontainers)
- `just test-unit-sqlite` - Run unit tests against SQLite
- `just test-unit-postgres` - Run unit tests against Postgres
- `just test-int-sqlite` - Run integration tests against SQLite
- `just test-int-postgres` - Run integration tests against Postgres
- `just test-windows` - Run Windows-specific tests (auto-skips on other platforms)
- `just test-benchmark` - Run performance benchmark tests

**Postgres Testing:**

Postgres tests use [testcontainers](https://testcontainers-python.readthedocs.io/) which automatically spins up a Postgres instance in Docker. No manual database setup required - just have Docker running.

**Test Markers:**

Tests use pytest markers for selective execution:
- `windows` - Windows-specific database optimizations
- `benchmark` - Performance tests (excluded from default runs)

**Other Development Commands:**
```bash
just install          # Install with dev dependencies
just lint             # Run linting checks
just typecheck        # Run type checking
just format           # Format code with ruff
just check            # Run all quality checks
just migration "msg"  # Create database migration
```

See the [justfile](justfile) for the complete list of development commands.

## License

AGPL-3.0

Contributions are welcome. See the [Contributing](CONTRIBUTING.md) guide for info about setting up the project locally
and submitting PRs.

## Star History

<a href="https://www.star-history.com/#basicmachines-co/basic-memory&Date">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=basicmachines-co/basic-memory&type=Date&theme=dark" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=basicmachines-co/basic-memory&type=Date" />
   <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=basicmachines-co/basic-memory&type=Date" />
 </picture>
</a>

Built with ♥️ by Basic Machines
