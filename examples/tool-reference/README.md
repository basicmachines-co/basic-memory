# Basic Memory MCP Tool Reference Project

A downloadable Basic Memory project containing comprehensive documentation for all MCP tools.

## What This Is

This is a complete Basic Memory project that you can import into your own Basic Memory installation.
It provides detailed reference documentation for all 16 core MCP tools, organized as a knowledge graph
that you can navigate and search using Basic Memory's tools.

## How to Use

### Option 1: Import into Existing Project

1. Copy the contents of this directory into your Basic Memory project
2. Run `basic-memory sync` to index the new content
3. Use `search_notes` or `build_context` to explore the documentation

### Option 2: Create New Project

1. Create a new Basic Memory project:
   ```bash
   basic-memory tools create-memory-project \
     --project-name "tool-reference" \
     --project-path "~/basic-memory-tool-reference"
   ```

2. Copy these files into the new project directory

3. Sync the project:
   ```bash
   basic-memory sync
   ```

### Option 3: Browse in Your Editor

The files are standard markdown with Basic Memory frontmatter.
You can browse them in any markdown editor or Obsidian.

## Project Structure

```
tool-reference/
├── README.md (this file)
├── index.md (main reference guide)
├── tools/
│   ├── write_note.md
│   ├── read_note.md
│   ├── search_notes.md
│   └── ... (all 16 tools)
└── categories/
    ├── content-management.md
    ├── knowledge-graph-navigation.md
    ├── search-and-discovery.md
    ├── project-management.md
    └── visualization.md
```

## Using the Documentation

### Search for Tools

```python
# Find all content management tools
search_notes("content management", search_type="text")

# Find write_note examples
search_notes("write_note")
```

### Navigate by Category

```python
# Read a category overview
read_note("content-management")

# Build context around search tools
build_context("memory://categories/search-and-discovery")
```

### View Tool Details

```python
# Read specific tool documentation
read_note("tools/write_note")
read_note("tools/search_notes")

# View as artifact for better formatting
view_note("tools/build_context")
```

## What's Included

- **16 tool reference documents** with detailed examples
- **5 category guides** organizing tools by function
- **Knowledge graph relations** connecting related tools
- **Copy-paste examples** ready for LLM instructions
- **Search syntax guide** for advanced queries
- **Real-world workflows** showing tool combinations

## Tool Categories

### Content Management

- `write_note`: Create or update markdown notes with semantic observations and relations
- `read_note`: Read markdown notes by title, permalink, or memory:// URL
- `read_content`: Read raw file content (text, images, binaries) without knowledge graph processing
- `view_note`: View notes as formatted artifacts for better readability
- `edit_note`: Edit notes incrementally with append, prepend, find/replace, replace_section
- `move_note`: Move notes to new locations, updating database and maintaining links
- `delete_note`: Delete notes from the knowledge base

### Knowledge Graph Navigation

- `build_context`: Navigate knowledge graph via memory:// URLs for conversation continuity
- `recent_activity`: Get recently updated information with specified timeframe
- `list_directory`: Browse directory contents with filtering and depth control

### Search & Discovery

- `search_notes`: Full-text search with advanced filtering and boolean operators

### Project Management

- `list_memory_projects`: List all available projects with their status
- `create_memory_project`: Create new Basic Memory projects
- `delete_project`: Delete a project from configuration
- `get_current_project`: Get current project information and stats
- `sync_status`: Check file synchronization and background operation status

### Visualization

- `canvas`: Generate Obsidian canvas files for knowledge graph visualization

## Generated Documentation

This project was automatically generated from the Basic Memory MCP tool source code.
You can regenerate it by running:

```bash
python scripts/generate_tool_reference_project.py
```

## License

Same as Basic Memory - AGPL v3
