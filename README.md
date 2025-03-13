[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![PyPI version](https://badge.fury.io/py/basic-memory.svg)](https://badge.fury.io/py/basic-memory)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Tests](https://github.com/basicmachines-co/basic-memory/workflows/Tests/badge.svg)](https://github.com/basicmachines-co/basic-memory/actions)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![smithery badge](https://smithery.ai/badge/@basicmachines-co/basic-memory)](https://smithery.ai/server/@basicmachines-co/basic-memory)

## Glama.ai

<a href="https://glama.ai/mcp/servers/o90kttu9ym">
  <img width="380" height="200" src="https://glama.ai/mcp/servers/o90kttu9ym/badge" alt="basic-memory MCP server" />
</a>

# Basic Memory

Basic Memory lets you build persistent knowledge through natural conversations with Large Language Models (LLMs) like
Claude, while keeping everything in simple Markdown files on your computer. It uses the Model Context Protocol (MCP) to
enable any compatible LLM to read and write to your local knowledge base.

- Website: http://basicmachines.co
- Documentation: http://memory.basicmachines.co

![Example Gif](docs/attachments/Obsidian-CoffeeKnowledgeBase-examples-overlays.gif)

Basic Memory provides persistent contextual awareness across sessions through a structured knowledge graph.
The system enables LLMs to access and reference prior conversations, track semantic relationships between concepts, and
incorporate human edits made directly to knowledge files.

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

### CLI Tools

You can also install the CLI tools to sync files or manage projects.

```bash 
uv tool install basic-memory

# create a new project in a different directory
basic-memory project add coffee ./examples/coffee

# you can set the project to the default 
basic-memory project default coffee
```

View available projects

```bash 
basic-memory project list
                             Basic Memory Projects
┏━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━┓
┃ Name   ┃ Path                                             ┃ Default ┃ Active ┃
┡━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━┩
│ main   │ ~/basic-memory                                   │ ✓       │ ✓      │
│ coffee │ ~/dev/basicmachines/basic-memory/examples/coffee │         │        │
└────────┴──────────────────────────────────────────────────┴─────────┴────────┘
```

Basic Memory will write notes in Markdown format. Open your project directory in your text editor to view project files
while you have conversations with an LLM.

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

The note embeds semantic content and links to other topics via simple Markdown
formatting.

3. You see this file on your computer in real time in the `~/$HOME/basic-memory` directory:

```markdown
---
title: Coffee Brewing Methods
permalink: coffee-brewing-methods
type: note
---

# Coffee Brewing Methods

## Observations

- [method] Pour over provides more clarity and highlights subtle flavors
- [technique] Water temperature at 205°F (96°C) extracts optimal compounds
- [principle] Freshly ground beans preserve aromatics and flavor
- [preference] Medium-light roasts work best for pour over # Added by you

## Relations

- relates_to [[Coffee Bean Origins]]
- requires [[Proper Grinding Technique]]
- affects [[Flavor Extraction]]
- pairs_with [[Breakfast Pastries]]  # Added by you
```

4. In a new chat with the LLM, you can reference this knowledge:

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

Each related document can lead to more context, building a rich semantic understanding of your knowledge base. All of
this context comes from standard Markdown files that both humans and LLMs can read and write.

Every time the LLM writes notes, they are saved in local Markdown files that you can:

- Edit in any text editor
- Version via git
- Back up normally
- Share when you want to

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

### Complete Example

Here's a complete example of a note with frontmatter, observations, and relations:

```markdown
---
title: Pour Over Coffee Method
type: note
permalink: pour-over-coffee-method
tags:
- brewing
- coffee
- techniques
---

# Pour Over Coffee Method

This note documents the pour over brewing method and my experiences with it.

## Overview

The pour over method involves pouring hot water through coffee grounds in a filter. The water drains through the coffee
and filter into a carafe or cup.

## Observations

- [equipment] Hario V60 dripper produces clean, bright cup #gear
- [technique] Pour in concentric circles to ensure even extraction
- [ratio] 1:16 coffee-to-water ratio works best for balanced flavor
- [timing] Total brew time should be 2:30-3:00 minutes for medium roast
- [temperature] Water at 205°F (96°C) extracts optimal flavor compounds
- [grind] Medium-fine grind similar to table salt texture
- [tip] 30-45 second bloom with double the coffee weight in water
- [result] Produces a cleaner cup with more distinct flavor notes than immersion methods

## Relations

- complements [[Light Roast Beans]]
- requires [[Gooseneck Kettle]]
- contrasts_with [[French Press Method]]
- pairs_with [[Breakfast Pastries]]
- documented_in [[Brewing Journal]]
- inspired_by [[Japanese Brewing Techniques]]
- affects [[Flavor Extraction]]
- part_of [[Morning Ritual]]
```

Basic Memory will parse the Markdown and derive the semantic relationships in the content. When you run
`basic-memory sync`:

1. New and changed files are detected
2. Markdown patterns become semantic knowledge:

- `[tech]` becomes a categorized observation
- `[[WikiLink]]` creates a relation in the knowledge graph
- Tags and metadata are indexed for search

3. A SQLite database maintains these relationships for fast querying
4. MCP-compatible LLMs can access this knowledge via memory:// URLs

This creates a two-way flow where:

- Humans write and edit Markdown files
- LLMs read and write through the MCP protocol
- Sync keeps everything consistent
- All knowledge stays in local files.

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

3. In Claude Desktop, the LLM can now use these tools:

```
write_note(title, content, folder, tags) - Create or update notes
read_note(identifier, page, page_size) - Read notes by title or permalink
build_context(url, depth, timeframe) - Navigate knowledge graph via memory:// URLs
search(query, page, page_size) - Search across your knowledge base
recent_activity(type, depth, timeframe) - Find recently updated information
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

## Multiple Projects

Basic Memory supports managing multiple separate knowledge bases through projects. This feature allows you to maintain
separate knowledge graphs for different purposes (e.g., personal notes, work projects, research topics).

### Managing Projects

```bash
# List all configured projects
basic-memory project list

# Add a new project
basic-memory project add work ~/work-basic-memory

# Set the default project
basic-memory project default work

# Remove a project (doesn't delete files)
basic-memory project remove personal

# Show current project
basic-memory project current
```

### Using Projects in Commands

All commands support the `--project` flag to specify which project to use:

```bash
# Sync a specific project
basic-memory --project=work sync

# Run MCP server for a specific project
basic-memory --project=personal mcp
```

You can also set the `BASIC_MEMORY_PROJECT` environment variable:

```bash
BASIC_MEMORY_PROJECT=work basic-memory sync
```

### Project Isolation

Each project maintains:

- Its own collection of markdown files in the specified directory
- A separate SQLite database for that project
- Complete knowledge graph isolation from other projects

## Design Philosophy

Basic Memory is built on some key ideas:

- Your knowledge should stay in files you control
- Both humans and AI should use natural formats
- Simple text patterns can capture rich meaning
- Local-first doesn't mean feature-poor
- Knowledge should persist across conversations
- AI assistants should build on past context
- File formats should be human-readable and editable
- Semantic structure should emerge from natural patterns
- Knowledge graphs should be both AI and human navigable
- Systems should augment human memory, not replace it

## Importing Existing Data

Basic Memory provides CLI commands to import data from various sources, converting them into the structured Markdown
format:

### Claude.ai

First, request an export of your data from your Claude account. The data will be emailed to you in several files,
including
`conversations.json` and `projects.json`.

Import Claude.ai conversation data

```bash
 basic-memory import claude conversations 
```

The conversations will be turned into Markdown files and placed in the "conversations" folder by default (this can be
changed with the --folder arg).

Example:

```bash
Importing chats from conversations.json...writing to .../basic-memory
  Reading chat data... ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 100%
╭────────────────────────────╮
│ Import complete!           │
│                            │
│ Imported 307 conversations │
│ Containing 7769 messages   │
╰────────────────────────────╯
```

Next, you can run the `sync` command to import the data into basic-memory

```bash
basic-memory sync
```

You can also import project data from Claude.ai

```bash 
➜  basic-memory import claude projects
Importing projects from projects.json...writing to .../basic-memory/projects
  Reading project data... ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 100%
╭────────────────────────────────╮
│ Import complete!               │
│                                │
│ Imported 101 project documents │
│ Imported 32 prompt templates   │
╰────────────────────────────────╯

Run 'basic-memory sync' to index the new files.
```

### OpenAI ChatGPT

```bash
 ➜  basic-memory import chatgpt
Importing chats from conversations.json...writing to .../basic-memory/conversations

  Reading chat data... ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 100%
╭────────────────────────────╮
│ Import complete!           │
│                            │
│ Imported 198 conversations │
│ Containing 11777 messages  │
╰────────────────────────────╯


```

### Knowledge Graph Memory Server

From the MCP Server: https://github.com/modelcontextprotocol/servers/tree/main/src/memory

```bash
➜  basic-memory import memory-json
Importing from memory.json...writing to .../basic-memory
  Reading memory.json... ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 100%
  Creating entities...   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 100%
╭──────────────────────╮
│ Import complete!     │
│                      │
│ Created 126 entities │
│ Added 252 relations  │
╰──────────────────────╯
```

## Working with Your Knowledge Base

Once you've built up a knowledge base, you can interact with it in several ways:

### Command Line Interface

Basic Memory provides a powerful CLI for managing your knowledge:

```bash
# See all available commands
basic-memory --help

# Check the status of your knowledge sync
basic-memory status

# Access specific tool functionality directly
basic-memory tools

# Start a continuous sync process
basic-memory sync --watch
```

### Obsidian Integration

Basic Memory works seamlessly with [Obsidian](https://obsidian.md/), a popular knowledge management app:

1. Point Obsidian to your Basic Memory directory
2. Use standard Obsidian features like backlinks and graph view
3. See your knowledge graph visually
4. Use the canvas visualization generated by Basic Memory

### File Organization

Basic Memory is flexible about how you organize your files:

- Group by topic in folders
- Use a flat structure with descriptive filenames
- Add custom metadata in frontmatter
- Tag files for better searchability

The system will build the semantic knowledge graph regardless of your file organization preference.

## Using stdin with Basic Memory's `write_note` Tool

The `write-note` tool supports reading content from standard input (stdin), allowing for more flexible workflows when
creating or updating notes in your Basic Memory knowledge base.

### Use Cases

This feature is particularly useful for:

1. **Piping output from other commands** directly into Basic Memory notes
2. **Creating notes with multi-line content** without having to escape quotes or special characters
3. **Integrating with AI assistants** like Claude Code that can generate content and pipe it to Basic Memory
4. **Processing text data** from files or other sources

## Basic Usage

### Method 1: Using a Pipe

You can pipe content from another command into `write_note`:

```bash
# Pipe output of a command into a new note
echo "# My Note\n\nThis is a test note" | basic-memory tools write-note --title "Test Note" --folder "notes"

# Pipe output of a file into a new note
cat README.md | basic-memory tools write-note --title "Project README" --folder "documentation"

# Process text through other tools before saving as a note
cat data.txt | grep "important" | basic-memory tools write-note --title "Important Data" --folder "data"
```

### Method 2: Using Heredoc Syntax

For multi-line content, you can use heredoc syntax:

```bash
# Create a note with heredoc
cat << EOF | basic-memory tools write_note --title "Project Ideas" --folder "projects"
# Project Ideas for Q2

## AI Integration
- Improve recommendation engine
- Add semantic search to product catalog

## Infrastructure
- Migrate to Kubernetes
- Implement CI/CD pipeline
EOF
```

### Method 3: Input Redirection

You can redirect input from a file:

```bash
# Create a note from file content
basic-memory tools write-note --title "Meeting Notes" --folder "meetings" < meeting_notes.md
```

## License

AGPL-3.0

Built with ♥️ by Basic Machines