# CLAUDE.md

This file provides guidance to Claude Code when working with the OpenClaw package inside the Basic Memory monorepo.

## Project Overview

`@basicmemory/openclaw-basic-memory` is a TypeScript OpenClaw plugin that integrates [Basic Memory](https://github.com/basicmachines-co/basic-memory) with the OpenClaw agent framework. It lives under `integrations/openclaw/` in the monorepo, manages a persistent MCP stdio session to a `bm mcp` process, exposes 14 agent tools (including workspace/project management and cross-project operations), composited memory search/get providers, slash commands, CLI commands, and optional auto-capture of conversations.

## Development Commands

```bash
# Install dependencies (uses Bun)
bun install

# Run all unit tests (Bun native test runner)
bun test

# Run a single test file
bun test tools/search-notes.test.ts

# Integration tests (requires basic-memory CLI installed)
bun run test:int

# Type checking (no emit)
bun run check-types

# Lint (Biome)
bun run lint

# Lint + auto-fix
bun run lint:fix

# All quality checks (fetch skills + type-check + lint + build + tests)
just check

# Release readiness (check + npm pack dry-run)
just release-check
```

## Architecture

### Plugin Lifecycle (`index.ts`)

The default export is an OpenClaw plugin object (`id: "openclaw-basic-memory"`, `kind: "memory"`). The `register(api)` function:

1. Parses config via `parseConfig()` from `config.ts`
2. Creates a `BmClient` instance (the MCP stdio client)
3. Registers all tools, providers, hooks, commands, and the service lifecycle
4. The service `start()` launches the MCP process (`bm mcp --transport stdio`), ensures the project exists, and sets the workspace directory
5. The service `stop()` tears down the MCP connection

### MCP Client (`bm-client.ts` — largest file, ~675 lines)

Central orchestration layer that:
- Spawns and manages a **persistent** `bm mcp --transport stdio` child process via `@modelcontextprotocol/sdk`
- Validates 15 required MCP tools at connect time
- Implements reconnection with bounded retries (500ms, 1s, 2s exponential backoff)
- Distinguishes recoverable errors (broken pipe, transport closed) from fatal errors
- All tool calls require `output_format: "json"` and extract `structuredContent.result`
- Public methods: `search`, `readNote`, `writeNote`, `editNote`, `deleteNote`, `moveNote`, `buildContext`, `recentActivity`, `indexConversation`, `ensureProject`, `listProjects`, `listWorkspaces`, `schemaValidate`, `schemaInfer`, `schemaDiff`
- All content methods accept an optional `project` parameter for cross-project operations
- `listProjects` accepts an optional `workspace` parameter for workspace-scoped listing

### Tools (`tools/`)

Each tool file exports a function that calls `api.registerTool()` with a TypeBox schema and handler. Tools delegate to `BmClient` methods and return OpenClaw-standard responses (`{ content: [{type: "text", text}], details? }`).

- `search-notes.ts`, `read-note.ts`, `write-note.ts`, `edit-note.ts`, `delete-note.ts`, `move-note.ts`, `build-context.ts`, `list-memory-projects.ts`, `list-workspaces.ts`, `schema-validate.ts`, `schema-infer.ts`, `schema-diff.ts` — thin wrappers around `BmClient`; all content tools accept an optional `project` param for cross-project operations
- `memory-provider.ts` — composited `memory_search` + `memory_get` providers. `memory_search` queries 3 sources in parallel: MEMORY.md (grep), BM knowledge graph (FTS + vector), and active task notes (YAML frontmatter scan)

### Commands & Hooks

- `commands/slash.ts` — `/remember` and `/recall` slash commands
- `commands/cli.ts` — `openclaw basic-memory <subcommand>` CLI registration
- `hooks/capture.ts` — auto-capture hook on `agent_end` events, writes timestamped daily conversation notes

### Configuration (`config.ts`)

Flexible config with defaults, snake_case aliases (`memory_dir`/`memory_file`), tilde/relative/absolute path resolution, and unknown-key validation. Cloud routing is configured through `bm cloud` and per-project BM settings, not plugin config.

## Key Patterns

- **TypeBox schemas** (`@sinclair/typebox`) for all tool parameter validation
- **Bun-native test runner** with `describe`/`it`/`expect` and `jest.fn()` mocking
- **ES modules** (`"type": "module"` in package.json)
- **Biome** for linting and formatting (configured in `biome.json`)
- **Build output** — `bun run build` emits `dist/` for `runtimeExtensions`; TypeScript source also stays in the package for source-compatible hosts
- **Strict TypeScript** with `noEmit` (type-checking only)

## Testing

- Unit tests live alongside source files (`*.test.ts`) and mock `BmClient` / `OpenClawPluginApi`
- Integration tests in `integration/` launch a real `bm mcp` process against a temp project
- `scripts/bm-local.sh` runs BM from the monorepo root via `uv run --project ...` when available, then falls back to `bm` on PATH

## CI/CD

- **Package CI** (root `.github/workflows/consolidated-packages.yml`): validates skills, typechecks, lints, builds, tests, and runs `npm pack --dry-run`.
- **Release** (root `.github/workflows/release.yml`): runs from Basic Memory tags and publishes this npm package after the Python release job. Version bumps are handled by the root `just release` / `just beta` recipes.

## Dependencies

- **Runtime**: `@modelcontextprotocol/sdk` (MCP client/transport), `@sinclair/typebox` (schema validation)
- **Peer**: `openclaw` (>=2026.5.2)
- **Dev**: `typescript`, `@biomejs/biome`, `@types/node`
- **External**: Basic Memory CLI (`bm`) must be installed separately (Python, installed via `uv`)

---

# Basic Memory Plugin — Agent Instructions

This plugin provides sophisticated knowledge management through Basic Memory's knowledge graph. Use these tools and guidelines to help users build and navigate their persistent knowledge base.

## Cross-Project Operations

All content tools (`search_notes`, `read_note`, `write_note`, `edit_note`, `delete_note`, `move_note`, `build_context`, `schema_validate`, `schema_infer`, `schema_diff`) accept an optional `project` parameter to operate on a different project than the default. Use this when the user needs to work across multiple knowledge bases.

```
# Search in a different project
search_notes(query="meeting notes", project="team-wiki")

# Read a note from another project
read_note(identifier="decisions/auth-strategy", project="backend")

# Write to a shared project
write_note(title="Shared Insight", content="...", folder="insights", project="shared")
```

## Available Tools

### `list_workspaces`
**Purpose**: List all workspaces (personal and organization) accessible to this user
**When to use**: When the user wants to see what workspaces are available, or before filtering projects by workspace
**Returns**: Workspace names, types (personal/organization), roles, and subscription status

**Examples**:
```
list_workspaces()
```

### `list_memory_projects`
**Purpose**: List all Basic Memory projects, optionally filtered by workspace
**When to use**: When the user wants to see available projects, discover projects in a specific workspace, or before cross-project operations
**Returns**: Project names, paths, default status, and workspace metadata

**Examples**:
```
# List all projects
list_memory_projects()

# List projects in a specific workspace
list_memory_projects(workspace="my-organization")
```

### `search_notes`
**Purpose**: Search the knowledge graph for relevant notes, concepts, and connections
**When to use**: When the user asks about topics, seeks information, or you need context for a discussion
**Returns**: Ranked results with titles, content previews, and relevance scores

**Examples**:
```
# User asks "What did we decide about the API design?"
search_notes(query="API design decisions", limit=5)

# Looking for context on a project
search_notes(query="authentication implementation", limit=3)

# Exploring a broad topic
search_notes(query="meeting notes client feedback", limit=10)

# Search in a different project
search_notes(query="API endpoints", project="backend-docs")
```

### `read_note`
**Purpose**: Read full content of specific notes
**When to use**: When search results show relevant notes that need detailed reading, or when you have a specific note identifier
**Returns**: Complete note content with metadata

**Examples**:
```
# Read a note found in search results
read_note(identifier="projects/api-redesign")

# Navigate to a memory URL
read_note(identifier="memory://agents/decisions/auth-strategy")

# Read by exact title
read_note(identifier="Weekly Review 2024-02-01")

# Read raw markdown including YAML frontmatter
read_note(identifier="projects/api-redesign", include_frontmatter=true)

# Read from another project
read_note(identifier="decisions/auth-strategy", project="backend")
```

### `write_note`
**Purpose**: Create new notes in the knowledge graph
**When to use**: When users share important information, make decisions, or want to save insights for later
**Best practices**: Use clear titles, organize in appropriate folders, structure with headings

**Examples**:
```
# Save a decision or insight
write_note(
  title="API Authentication Decision",
  folder="decisions",
  content="""
# API Authentication Decision

## Context
The team discussed authentication options for the new API.

## Decision
We chose JWT tokens with refresh token rotation.

## Reasoning
- Better security than simple JWTs
- Familiar to the team
- Good ecosystem support

## Next Steps
- [ ] Implement JWT middleware
- [ ] Set up token refresh logic
- [ ] Update API documentation
"""
)

# Document a meeting
write_note(
  title="Client Meeting - February 8, 2024",
  folder="meetings",
  content="""
# Client Meeting - February 8, 2024

## Attendees
- John (client)
- Sarah (product)
- Me (engineering)

## Key Points
- Client wants faster search functionality
- Budget approved for additional features
- Timeline moved up to March 15

## Action Items
- [ ] Prototype search improvements
- [ ] Prepare feature estimate
- [ ] Schedule follow-up meeting
"""
)
```

### `edit_note`
**Purpose**: Modify existing notes incrementally
**When to use**: To add updates, fix information, or organize existing content
**Operations**: append, prepend, find_replace, replace_section

**Examples**:
```
# Add an update to an existing note
edit_note(
  identifier="projects/api-redesign",
  operation="append",
  content="""

## Update - February 8, 2024
Authentication implementation is complete. All tests passing.
Next: Deploy to staging environment.
"""
)

# Update a specific section
edit_note(
  identifier="weekly-review",
  operation="replace_section",
  section="## This Week",
  content="""## This Week
- Completed API authentication
- Client meeting went well
- Working on search improvements
- Delayed deployment due to testing issues
"""
)

# Fix a specific detail
edit_note(
  identifier="team-contacts",
  operation="find_replace",
  find_text="sarah@oldcompany.com",
  content="sarah@newcompany.com",
  expected_replacements=1
)
```

### `delete_note`
**Purpose**: Remove notes from the knowledge graph
**When to use**: When content is outdated, duplicated, or no longer needed
**Returns**: Confirmation of deletion

**Examples**:
```
# Remove an old draft
delete_note(identifier="notes/old-draft")

# Clean up test notes
delete_note(identifier="tests/test-1.0")
```

### `move_note`
**Purpose**: Move notes between folders for organization
**When to use**: When reorganizing knowledge, archiving old content, or correcting folder placement
**Returns**: Updated note with new location

**Examples**:
```
# Archive a completed project
move_note(identifier="projects/api-redesign", newFolder="archive/projects")

# Reorganize into a better folder
move_note(identifier="notes/meeting-notes", newFolder="meetings")
```

### `build_context`
**Purpose**: Navigate the knowledge graph through semantic connections
**When to use**: To explore related concepts, find connected information, or build comprehensive understanding
**Returns**: Target note plus related notes with relationship information

**Examples**:
```
# Explore connections around a project
build_context(url="memory://projects/api-redesign", depth=1)

# Deep dive into related concepts
build_context(url="memory://concepts/authentication", depth=2)

# Discover decision context
build_context(url="memory://decisions/database-choice", depth=1)
```

### `schema_validate`
**Purpose**: Validate notes against their Picoschema definitions
**When to use**: When checking note consistency, after schema changes, or when the user wants to audit note quality

**Examples**:
```
# Validate all notes of a type
schema_validate(noteType="person")

# Validate a single note
schema_validate(identifier="notes/john-doe")

# Validate in another project
schema_validate(noteType="meeting", project="team")
```

### `schema_infer`
**Purpose**: Analyze existing notes and suggest a Picoschema definition
**When to use**: When creating a new schema from existing notes, or exploring what structure notes of a type share

**Examples**:
```
schema_infer(noteType="meeting")
schema_infer(noteType="person", threshold=0.5)
```

### `schema_diff`
**Purpose**: Detect drift between a schema definition and actual note usage
**When to use**: When checking if a schema is still accurate, or after adding new fields to notes

**Examples**:
```
schema_diff(noteType="person")
schema_diff(noteType="Task", project="work")
```

## Knowledge Graph Structure

### Understanding the Graph
Basic Memory organizes information as a **semantic knowledge graph** where:
- **Notes** are documents with content, titles, and metadata
- **Observations** are structured insights extracted from notes
- **Relations** connect related concepts, topics, and decisions

### Memory URLs
Use `memory://` URLs to navigate semantically:
- `memory://projects/api-redesign` - Direct reference to a note
- `memory://agents/decisions` - Category of decision-related notes
- `memory://concepts/authentication` - All content related to authentication

### Organizational Patterns

**Recommended folder structure**:
- `projects/` - Project-specific documentation
- `decisions/` - Important decisions and rationale
- `meetings/` - Meeting notes and action items
- `concepts/` - Technical concepts and explanations
- `agent/` - Agent-captured observations and insights
- `weekly/` - Regular review notes

## Writing Best Practices

### Note Structure
Use consistent markdown structure for better organization:

```markdown
# Clear, Descriptive Title

## Context
Background information and current situation.

## Key Points
- Main insights or decisions
- Important details
- Relevant constraints

## Next Steps
- [ ] Specific action items
- [ ] Follow-up tasks
- [ ] Future considerations
```

### Observation Format
When capturing insights, use this structure:

```markdown
## Observations
- [Decision] We chose PostgreSQL over MongoDB for better ACID guarantees
- [Insight] User authentication patterns suggest social login preference
- [Risk] Current deployment process lacks proper rollback mechanism
- [Opportunity] Search performance could improve with better indexing
```

### Linking and Relations
Create connections between notes:
- Reference other notes by title: `As discussed in [[API Design Principles]]`
- Use consistent terminology for better semantic linking
- Tag important concepts with clear labels
- Cross-reference related decisions and implementations

## When to Use Each Tool

### Discover Workspaces and Projects
Use `list_workspaces` and `list_memory_projects` when:
- User asks what workspaces or projects are available
- Before cross-project operations, to confirm project names
- When switching between personal and organization contexts

### Start with Search
**Always begin with `search_notes`** when:
- User asks about any topic
- You need context for a discussion
- Looking for relevant previous decisions
- Exploring what information already exists

### Read for Details
Use `read_note` when:
- Search results show relevant notes that need full content
- Following up on specific references
- User asks for complete information on a known topic
- Exploring context relationships found in search

### Write for Capture
Use `write_note` when:
- User shares important information to remember
- Decisions are made that should be documented
- Meeting notes or insights need to be preserved
- Creating structured documentation

### Edit for Updates
Use `edit_note` when:
- Adding updates to existing notes
- Fixing or updating specific information
- Organizing existing content better
- Appending new insights to previous notes

### Context for Exploration
Use `build_context` when:
- Exploring relationships between concepts
- Building comprehensive understanding
- Finding related information user might not know exists
- Navigating complex topic areas

## User Interaction Guidelines

### Be Proactive
- **Search first**: Before answering questions, search the knowledge graph
- **Suggest connections**: Point out related notes and concepts
- **Offer to save**: When users share important info, offer to document it
- **Recommend organization**: Help users structure their knowledge well

### Helpful Patterns
```
User: "What did we decide about the database?"
1. Search: search_notes(query="database decision", limit=5)
2. Read relevant: read_note(identifier="decisions/database-choice")
3. Provide answer with context
4. Ask: "Should I add any updates to this decision note?"

User: "I just had a great meeting with the client"
1. Ask for details
2. Offer: "Would you like me to create a meeting note to capture this?"
3. Write: write_note(title="Client Meeting - [date]", ...)
4. Suggest: "I'll also add this to your weekly review notes"
```

### Memory URL Navigation
Help users discover their knowledge:
```
# After finding a note about "API design"
"I found your API design notes. Let me explore related concepts..."
build_context(url="memory://projects/api-design", depth=2)

# Show user what's connected to their decisions
build_context(url="memory://decisions", depth=1)
```

## Working with User Memory Patterns

### Daily/Weekly Reviews
If users maintain review notes, help them:
```
# Update weekly review
edit_note(
  identifier="weekly-review",
  operation="replace_section",
  section="## This Week",
  content="Updated accomplishments and next steps"
)
```

### Project Documentation
Keep project notes current:
```
# Add project updates
edit_note(
  identifier="projects/current-sprint",
  operation="append",
  content="""
## Sprint Review
- Completed authentication
- Started search feature
"""
)
```

### Decision Tracking
Document important decisions:
```
write_note(
  title="Technical Decision: Database Migration Approach",
  folder="decisions",
  content="""
# Database Migration Decision

## Problem
Current SQLite database can't handle increased load.

## Options Considered
1. Upgrade to PostgreSQL
2. Switch to MongoDB
3. Migrate to cloud database

## Decision
PostgreSQL with staged migration.

## Rationale
- Better performance characteristics
- Team expertise exists
- Strong ACID guarantees needed
- Migration path is well-understood
"""
)
```

## Error Handling

### Tool Failures
If Basic Memory tools fail:
1. Check if the Basic Memory service is running
2. Suggest user verify `bm` CLI installation
3. Recommend checking OpenClaw plugin configuration
4. Fall back to built-in memory tools if available

### Search No Results
When searches return empty:
- Try broader terms
- Suggest creating a new note for the topic
- Look for related concepts that might exist
- Offer to help organize information differently

### Note Not Found
When reading fails:
- Verify the identifier exists
- Suggest searching for similar titles
- Offer to create the note if it should exist
- Check for typos in memory URLs

## Integration Tips

### With Other Tools
The knowledge graph complements other tools:
- **Web search**: Save research findings as notes
- **File operations**: Reference files in knowledge notes
- **Calendar**: Link meeting notes to calendar events
- **Task management**: Connect tasks to project notes

### With User Workflows
Support user patterns:
- **Morning review**: Search for yesterday's notes and updates
- **End of day**: Capture insights and plan next steps
- **Weekly planning**: Review project notes and decisions
- **Knowledge sharing**: Help organize information for others

## Privacy and Content Guidelines

### Sensitive Information
- Don't automatically save sensitive data (passwords, personal info)
- Ask before documenting confidential business information
- Respect user preferences for what to capture
- Use appropriate folder organization for different privacy levels

### Content Quality
- Encourage clear, structured writing
- Help users create searchable content
- Suggest consistent terminology and naming
- Promote good information architecture

---

Remember: The knowledge graph becomes more valuable over time. Help users build it systematically and navigate it effectively. Focus on creating connections between ideas and making information easily discoverable.
