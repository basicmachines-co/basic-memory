# Basic Memory Plugin — Agent Guide

Reference notes on using the plugin's tools. This file is not shipped in the npm package;
runtime agents get their guidance from the tool descriptions in `tools/` and the bundled
`skills/`.

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
**Best practices**: Use clear titles, organize in appropriate folders, structure the Markdown body with headings, and pass frontmatter through the dedicated parameters instead of embedding YAML in `content`

**Frontmatter parameters**:
- `tags`: A string array or comma-separated string
- `note_type`: The note type stored as `type`; defaults to `note`
- `metadata`: Additional frontmatter fields, including nested objects

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
  tags=["client", "planning"],
  note_type="Meeting",
  metadata={
    "status": "complete",
    "attendees": ["John", "Sarah"],
  },
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
**Frontmatter updates**: Pass `metadata` to merge custom frontmatter fields in the same call. Provided keys replace existing values, unrelated fields remain unchanged, and nested objects are supported.

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
  metadata={"status": "review", "progress": {"completed": 4, "total": 6}},
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

Any "remember/record/save/note this" request maps to a `write_note` (or `edit_note`) call in that same turn — an acknowledgement without the call saves nothing.

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
- **Never claim an unwritten save**: Only say something was saved or recorded after a `write_note`/`edit_note` call returned a result, and cite the `permalink` or `file_path` from that result — never a filename recalled from memory. If you decline to write, say plainly that nothing new was saved
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
