# AI Assistant Guide for Basic Memory - Extended Edition

**This is the comprehensive guide for AI assistants using Basic Memory through MCP.**

> **Note for Developers**: This guide is organized into self-contained sections. You can copy/paste individual sections to create customized guides for specific use cases or AI assistants. Each section is designed to stand alone while also working as part of the complete guide.

## Table of Contents

1. [Understanding Basic Memory](#understanding-basic-memory)
2. [Project Management](#project-management)
3. [Knowledge Graph Fundamentals](#knowledge-graph-fundamentals)
4. [Writing Knowledge](#writing-knowledge)
5. [Reading and Navigation](#reading-and-navigation)
6. [Search and Discovery](#search-and-discovery)
7. [Building Context](#building-context)
8. [Recording Conversations](#recording-conversations)
9. [Editing Notes](#editing-notes)
10. [Moving and Organizing](#moving-and-organizing)
11. [Error Handling](#error-handling)
12. [Advanced Patterns](#advanced-patterns)
13. [Tool Reference](#tool-reference)
14. [Best Practices](#best-practices)

---

## Understanding Basic Memory

**Core Concept**: Basic Memory is a local-first knowledge management system that creates a semantic knowledge graph from markdown files. It enables persistent, structured knowledge that survives across AI sessions.

### Key Principles

**Local-First Architecture**
- All knowledge stored as plain text markdown files on user's computer
- SQLite database indexes files for fast search and navigation
- Files are the source of truth, database is derived state
- User maintains complete control over their data

**Semantic Knowledge Graph**
- Entities: Individual markdown files representing concepts
- Observations: Categorized facts with optional tags
- Relations: Directional links between entities
- Graph traversal enables context building and exploration

**Persistent Context**
- Knowledge persists across conversations
- AI can reference previous discussions
- Context builds over time through accumulated knowledge
- Enables long-term collaborative development

### AI as Knowledge Collaborator

Basic Memory's semantic knowledge graph - observations, relations, context building - is designed to help you (the AI assistant) provide better help to humans. You use the graph structure to:
- Build relevant context from past conversations
- Navigate connections between ideas
- Understand relationships and dependencies
- Provide continuity across sessions

**The distinction**: You're helping humans build enduring knowledge they'll own forever, not creating disposable agent memory. The better you use these tools, the more valuable their knowledge becomes over time. Think of markdown files as artifacts that will outlast any particular AI model - your job is to help create knowledge worth keeping.

### Architecture Overview

```
User's Markdown Files (Source of Truth)
         ↓
    File Sync
         ↓
SQLite Database (Index)
         ↓
    MCP Server
         ↓
   AI Assistant
```

**Data Flow**:
1. User creates/edits markdown files
2. Sync process detects changes
3. Files are parsed and indexed in SQLite
4. MCP server exposes indexed data to AI
5. AI can query, traverse, and update knowledge graph
---

## Project Management

**Project Concept**: A project is a directory of markdown files with its own knowledge graph. Users can have multiple independent projects.

### Discovering Projects

**Always start by discovering available projects:**

```python
# List all projects
projects = await list_memory_projects()

# Response structure:
# [
#   {
#     "name": "main",
#     "path": "/Users/name/notes",
#     "is_default": True,
#     "note_count": 156,
#     "last_synced": "2025-01-15T10:30:00Z"
#   },
#   {
#     "name": "work",
#     "path": "/Users/name/work-notes",
#     "is_default": False,
#     "note_count": 89,
#     "last_synced": "2025-01-14T16:45:00Z"
#   }
# ]
```

**When to discover projects**:
- Start of conversation when project unknown
- User asks about available projects
- Before any operation requiring project selection
- After errors related to project not found

### Project Selection Patterns

**Single-Project Users**:

```python
# Enable default_project_mode in config
# ~/.basic-memory/config.json
{
  "default_project": "main",
  "default_project_mode": true
}

# Then tools work without project parameter
await write_note("Note", "Content", "folder")
await search_notes(query="test")
```

**Multi-Project Users**:

```python
# Keep default_project_mode disabled (default)
# Always specify project explicitly

# All tool calls require project
await write_note("Note", "Content", "folder", project="main")
await search_notes(query="test", project="work")

# Can target different projects in same conversation
results_main = await search_notes(query="auth", project="main")
results_work = await search_notes(query="auth", project="work")
```

**Recommended Workflow**:

```python
# 1. Discover projects
projects = await list_memory_projects()

# 2. Ask user which to use (if ambiguous)
# "I found 2 projects: 'main' and 'work'. Which should I use?"

# 3. Store choice for session
active_project = "main"

# 4. Use in all subsequent calls
results = await search_notes(query="topic", project=active_project)
```

### Cross-Project Operations

**Some tools work across all projects when project parameter omitted:**

```python
# Recent activity across all projects
activity = await recent_activity(timeframe="7d")
# Returns activity from all projects

# Recent activity for specific project
activity = await recent_activity(timeframe="7d", project="main")
# Returns activity only from "main" project
```

**Tools supporting cross-project mode**:
- `recent_activity()` - aggregate activity across projects
- `list_memory_projects()` - always returns all projects
- `sync_status()` - can show all projects or specific

### Creating Projects

**Create new projects programmatically:**

```python
# Create new project
await create_memory_project(
    project_name="research",
    project_path="/Users/name/Documents/research",
    set_default=False
)

# Create and set as default
await create_memory_project(
    project_name="primary",
    project_path="/Users/name/notes",
    set_default=True
)
```

**Use cases**:
- User requests new knowledge base
- Separating work/personal notes
- Project-specific documentation
- Client-specific knowledge

### Project Status

**Check sync status before operations:**

```python
# Check if sync complete
status = await sync_status(project="main")

# Response indicates:
# - sync_in_progress: bool
# - files_processed: int
# - files_remaining: int
# - last_sync: datetime
# - errors: list

# Wait for sync if needed
if status["sync_in_progress"]:
    # Inform user: "Sync in progress, please wait..."
    # Or proceed with available data
```

---

## Knowledge Graph Fundamentals

**The knowledge graph is built from three core elements: entities, observations, and relations.**

### Entities

**What is an Entity?**
- Any concept, document, or idea represented as a markdown file
- Has a unique title and permalink
- Contains frontmatter metadata
- Includes observations and relations

**Entity Structure**:

```markdown
---
title: Authentication System
permalink: authentication-system
tags: [security, auth, api]
type: note
created: 2025-01-10T14:30:00Z
updated: 2025-01-15T09:15:00Z
---

# Authentication System

## Context
Brief description of the entity

## Observations
- [category] Facts about this entity

## Relations
- relation_type [[Other Entity]]
```

**Entity Types**:
- `note`: General knowledge (default)
- `person`: People and contacts
- `project`: Projects and initiatives
- `meeting`: Meeting notes
- `decision`: Documented decisions
- `spec`: Technical specifications

### Observations

**Observations are categorized facts with optional tags.**

**Syntax**: `- [category] content #tag1 #tag2`

**Common Categories**:
- `[fact]`: Objective information
- `[idea]`: Thoughts and concepts
- `[decision]`: Choices made
- `[technique]`: Methods and approaches
- `[requirement]`: Needs and constraints
- `[question]`: Open questions
- `[insight]`: Key realizations
- `[problem]`: Issues identified
- `[solution]`: Resolutions

**Examples**:

```markdown
## Observations
- [decision] Use JWT tokens for authentication #security
- [technique] Hash passwords with bcrypt before storage #best-practice
- [requirement] Support OAuth 2.0 providers (Google, GitHub) #auth
- [fact] Session timeout set to 24 hours #configuration
- [problem] Password reset emails sometimes delayed #bug
- [solution] Implemented retry queue for email delivery #fix
- [insight] 2FA adoption increased security by 40% #metrics
```

**Why Categorize?**:
- Enables semantic search by observation type
- Helps AI understand context and intent
- Makes knowledge more queryable
- Provides structure for analysis

### Relations

**Relations are directional links between entities.**

**Syntax**: `- relation_type [[Target Entity]]`

**Common Relation Types**:
- `relates_to`: General connection
- `implements`: Implementation of spec/design
- `requires`: Dependency relationship
- `extends`: Extension or enhancement
- `part_of`: Hierarchical membership
- `contrasts_with`: Opposite or alternative
- `caused_by`: Causal relationship
- `leads_to`: Sequential relationship
- `similar_to`: Similarity relationship

**Examples**:

```markdown
## Relations
- implements [[Authentication Spec v2]]
- requires [[User Database Schema]]
- extends [[Base Security Model]]
- part_of [[API Backend Services]]
- contrasts_with [[API Key Authentication]]
- leads_to [[Session Management]]
```

**Bidirectional Links**:

```markdown
# In "Login Flow" note
## Relations
- part_of [[Authentication System]]

# In "Authentication System" note
## Relations
- includes [[Login Flow]]
```

**Why explicit relation types matter**:
- Enables semantic graph traversal
- AI can understand relationship meaning
- Supports sophisticated context building
- Makes knowledge more navigable

### Forward References

**You can reference entities that don't exist yet:**

```python
# Create note referencing non-existent entity
await write_note(
    title="API Implementation",
    content="""# API Implementation

## Relations
- implements [[API Specification]]
- requires [[Database Models]]
""",
    folder="api",
    project="main"
)
# Creates forward references to "API Specification" and "Database Models"

# Later, create referenced entities
await write_note(
    title="API Specification",
    content="# API Specification\n...",
    folder="specs",
    project="main"
)
# Forward reference automatically resolved!

await write_note(
    title="Database Models",
    content="# Database Models\n...",
    folder="database",
    project="main"
)
# Second forward reference resolved!
```

**How it works**:
1. Forward reference creates placeholder in knowledge graph
2. When target entity is created, relation is automatically resolved
3. Graph traversal works in both directions
4. No manual linking required

**Use cases**:
- Planning features before implementation
- Creating outlines with linked topics
- Bottom-up knowledge building
- Incremental documentation

---

## Writing Knowledge

**Creating rich, well-structured notes is fundamental to building a useful knowledge graph.**

### Basic Note Creation

**Minimal note**:

```python
await write_note(
    title="Quick Note",
    content="# Quick Note\n\nSome basic content.",
    folder="notes",
    project="main"
)
```

**Well-structured note**:

```python
await write_note(
    title="Database Design Decisions",
    content="""# Database Design Decisions

## Context
Documenting our database architecture choices for the authentication system.

## Observations
- [decision] PostgreSQL chosen over MySQL for better JSON support #database
- [technique] Using UUID primary keys instead of auto-increment #design
- [requirement] Must support multi-tenant data isolation #security
- [fact] Expected load is 10K requests/minute #performance
- [insight] UUID keys enable easier horizontal scaling #scalability

## Relations
- implements [[Authentication System Spec]]
- requires [[Database Infrastructure]]
- relates_to [[API Design]]
- contrasts_with [[Previous MySQL Design]]
""",
    folder="architecture",
    tags=["database", "design", "authentication"],
    project="main"
)
```

### Effective Observation Writing

**Good observations are**:
- **Specific**: Avoid vague statements
- **Categorized**: Use appropriate category
- **Tagged**: Add relevant tags
- **Atomic**: One fact per observation
- **Contextual**: Include enough detail

**Examples**:

**❌ Poor observations**:
```markdown
- [fact] We use a database
- [idea] Security is important
- [decision] Made some changes
```

**✓ Good observations**:
```markdown
- [fact] PostgreSQL 14 database runs on AWS RDS with 16GB RAM #infrastructure
- [decision] Implemented rate limiting at 100 requests/minute per user #security
- [technique] Using bcrypt with cost factor 12 for password hashing #cryptography
```

### Writing Effective Relations

**Relations should be**:
- **Directional**: Clear source and target
- **Typed**: Use meaningful relation type
- **Accurate**: Use exact entity titles
- **Purposeful**: Add value to graph

**Choosing relation types**:

```markdown
# Implementation relationship
- implements [[Feature Specification]]

# Dependency relationship
- requires [[User Authentication]]
- depends_on [[Database Connection]]

# Hierarchical relationship
- part_of [[Payment System]]
- includes [[Payment Validation]]

# Contrast relationship
- contrasts_with [[Alternative Approach]]
- alternative_to [[Previous Design]]

# Temporal relationship
- leads_to [[Next Phase]]
- follows [[Initial Setup]]

# Causal relationship
- caused_by [[Performance Issue]]
- results_in [[Optimization]]
```

### Note Templates

**Decision Record**:

```python
await write_note(
    title="Decision: Use GraphQL for API",
    content="""# Decision: Use GraphQL for API

## Context
Evaluating API architecture for new product features.

## Decision
Adopt GraphQL instead of REST for our API layer.

## Observations
- [decision] GraphQL chosen for flexible client queries #api
- [requirement] Frontend needs to minimize round trips #performance
- [technique] Apollo Server for GraphQL implementation #technology
- [fact] REST API still maintained for legacy clients #compatibility
- [insight] GraphQL reduced API calls by 60% in prototype #metrics

## Rationale
- Type safety reduces runtime errors
- Single endpoint simplifies deployment
- Built-in schema documentation
- Better mobile performance

## Consequences
- Team needs GraphQL training
- More complex caching strategy
- Additional monitoring required

## Relations
- implements [[API Architecture Plan]]
- requires [[GraphQL Schema Design]]
- affects [[Frontend Development]]
- replaces [[REST API v1]]
""",
    folder="decisions",
    tags=["decision", "api", "graphql"],
    note_type="decision",
    project="main"
)
```

**Meeting Notes**:

```python
await write_note(
    title="API Review Meeting 2025-01-15",
    content="""# API Review Meeting 2025-01-15

## Attendees
- Alice (Backend Lead)
- Bob (Frontend Lead)
- Carol (Product)

## Observations
- [decision] Finalized GraphQL schema for user endpoints #api
- [action] Bob to implement Apollo client integration by Friday #task
- [problem] Rate limiting causing issues in staging #bug
- [insight] GraphQL subscriptions reduce polling load significantly #performance
- [requirement] Need better error handling for network failures #frontend

## Action Items
- [ ] Implement rate limiting improvements (Alice)
- [ ] Apollo client setup (Bob)
- [ ] Document error handling patterns (Alice)
- [ ] Update API documentation (Carol)

## Relations
- relates_to [[API Architecture Plan]]
- references [[GraphQL Implementation]]
- follows_up [[API Planning Meeting 2025-01-08]]
""",
    folder="meetings",
    tags=["meeting", "api", "team"],
    note_type="meeting",
    project="main"
)
```

**Technical Specification**:

```python
await write_note(
    title="User Authentication Spec",
    content="""# User Authentication Spec

## Overview
Specification for user authentication system using JWT tokens.

## Observations
- [requirement] Support email/password and OAuth authentication #auth
- [requirement] JWT tokens expire after 24 hours #security
- [requirement] Refresh tokens valid for 30 days #security
- [technique] Use RS256 algorithm for token signing #cryptography
- [fact] Tokens include user_id, email, and roles claims #implementation
- [decision] Store refresh tokens in HTTP-only cookies #security
- [technique] Implement rate limiting on login endpoints #protection

## Technical Details

### Authentication Flow
1. User submits credentials
2. Server validates against database
3. Generate JWT access token
4. Generate refresh token
5. Return tokens to client

### Token Structure
```json
{
  "user_id": "uuid",
  "email": "user@example.com",
  "roles": ["user"],
  "exp": 1234567890,
  "iat": 1234567890
}
```

## Relations
- implemented_by [[Authentication Service]]
- requires [[User Database Schema]]
- part_of [[Security Architecture]]
- extends [[OAuth 2.0 Spec]]
""",
    folder="specs",
    tags=["spec", "auth", "security"],
    note_type="spec",
    project="main"
)
```

### Tags Strategy

**Effective tagging**:

```python
# Technology tags
#python #fastapi #graphql #postgresql

# Domain tags
#auth #security #api #frontend #backend

# Status tags
#wip #completed #deprecated #planned

# Priority tags
#urgent #important #nice-to-have

# Category tags
#bug #feature #refactor #docs #test
```

**Example with strategic tags**:

```python
await write_note(
    title="OAuth Integration",
    content="""# OAuth Integration

## Observations
- [feature] Google OAuth integration completed #oauth #google #completed
- [feature] GitHub OAuth in progress #oauth #github #wip
- [requirement] Add Microsoft OAuth support #oauth #microsoft #planned
- [technique] Using authlib for OAuth flow #python #authlib
- [insight] OAuth reduces password reset requests by 80% #metrics #security
""",
    folder="features",
    tags=["oauth", "authentication", "integration"],
    project="main"
)
```

---

## Reading and Navigation

**Reading notes and navigating the knowledge graph is fundamental to building context.**

### Reading by Identifier

**Read by title**:

```python
# Simple title
note = await read_note(
    identifier="Authentication System",
    project="main"
)

# Title in specific folder
note = await read_note(
    identifier="specs/Authentication System",
    project="main"
)
```

**Read by permalink**:

```python
# Permalink is auto-generated from title
note = await read_note(
    identifier="authentication-system",
    project="main"
)

# Permalink with folder
note = await read_note(
    identifier="specs/authentication-system",
    project="main"
)
```

### Reading by memory:// URL

**URL formats**:

```python
# By title
note = await read_note(
    identifier="memory://Authentication System",
    project="main"
)

# By folder and title
note = await read_note(
    identifier="memory://specs/Authentication System",
    project="main"
)

# By permalink
note = await read_note(
    identifier="memory://authentication-system",
    project="main"
)

# Wildcards for folder contents
notes = await read_note(
    identifier="memory://specs/*",
    project="main"
)
```

```python
# Underscores automatically converted to hyphens
note = await read_note(
    identifier="memory://my_note_title",
    project="main"
)
# Finds entity with permalink "my-note-title"

# Both forms work
note1 = await read_note("memory://api_design", project="main")
note2 = await read_note("memory://api-design", project="main")
# Both find same entity
```

### Response Structure

**read_note response includes**:

```python
{
    "title": "Authentication System",
    "permalink": "authentication-system",
    "content": "# Authentication System\n\n...",
    "folder": "specs",
    "tags": ["auth", "security"],
    "type": "spec",
    "created": "2025-01-10T14:30:00Z",
    "updated": "2025-01-15T09:15:00Z",
    "observations": [
        {
            "category": "decision",
            "content": "Use JWT for authentication",
            "tags": ["security"]
        }
    ],
    "relations": [
        {
            "type": "implemented_by",
            "target": "Authentication Service",
            "target_permalink": "authentication-service"
        }
    ]
}
```

### Pagination

**For long notes, use pagination**:

```python
# First page (default: 10 items)
page1 = await read_note(
    identifier="Long Document",
    page=1,
    page_size=10,
    project="main"
)

# Second page
page2 = await read_note(
    identifier="Long Document",
    page=2,
    page_size=10,
    project="main"
)

# Large page size for complete content
full = await read_note(
    identifier="Long Document",
    page=1,
    page_size=1000,
    project="main"
)
```

### Reading Raw Content

**For non-markdown files or raw access**:

```python
# Read text file
content = await read_content(
    path="config/settings.json",
    project="main"
)

# Read image (returned as base64)
image = await read_content(
    path="diagrams/architecture.png",
    project="main"
)

# Read any file type
data = await read_content(
    path="data/export.csv",
    project="main"
)
```

**Difference from read_note**:
- `read_note`: Parses markdown, extracts knowledge graph
- `read_content`: Returns raw file content
- Use `read_note` for knowledge graph navigation
- Use `read_content` for non-markdown files

### Viewing as Artifact

**For better readability, use view_note**:

```python
# Display as formatted artifact
artifact = await view_note(
    identifier="Authentication System",
    project="main"
)

# Returns formatted markdown suitable for display
# - Syntax highlighting
# - Rendered markdown
# - Better visual presentation
```

**When to use view_note**:
- Showing content to user
- Presenting documentation
- Displaying specifications
- Better than raw markdown for reading

### Directory Browsing

**List directory contents**:

```python
# List top-level folders
root = await list_directory(
    dir_name="/",
    project="main"
)

# List specific folder
specs = await list_directory(
    dir_name="specs",
    project="main"
)

# Recursive listing
all_files = await list_directory(
    dir_name="/",
    depth=3,
    project="main"
)

# Filter by pattern
markdown_files = await list_directory(
    dir_name="docs",
    file_name_glob="*.md",
    project="main"
)
```

**Response structure**:

```python
{
    "path": "specs",
    "files": [
        {
            "name": "authentication-system.md",
            "path": "specs/authentication-system.md",
            "type": "file",
            "size": 2048,
            "modified": "2025-01-15T09:15:00Z"
        }
    ],
    "directories": [
        {
            "name": "api",
            "path": "specs/api",
            "type": "directory",
            "file_count": 5
        }
    ]
}
```

---

## Search and Discovery

**Search is the primary way to discover relevant knowledge.**

### Basic Search

**Simple text search**:

```python
# Search across all content
results = await search_notes(
    query="authentication",
    project="main"
)

# Search with pagination
results = await search_notes(
    query="authentication",
    page=1,
    page_size=10,
    project="main"
)

# Get more results
results = await search_notes(
    query="authentication",
    page=1,
    page_size=50,
    project="main"
)
```

### Advanced Search

**Filter by entity type**:

```python
# Search only specifications
specs = await search_notes(
    query="authentication",
    types=["spec"],
    project="main"
)

# Search decisions and meetings
decisions = await search_notes(
    query="api design",
    types=["decision", "meeting"],
    project="main"
)
```

**Filter by observation category**:

```python
# Find all decisions
decisions = await search_notes(
    query="",
    entity_types=["decision"],
    project="main"
)

# Find problems and solutions
issues = await search_notes(
    query="performance",
    entity_types=["problem", "solution"],
    project="main"
)
```

**Date filtering**:

```python
# Find recent changes
recent = await search_notes(
    query="api",
    after_date="2025-01-01",
    project="main"
)

# Combine with other filters
recent_decisions = await search_notes(
    query="authentication",
    types=["decision"],
    after_date="2025-01-01",
    project="main"
)
```

### Search Types

**Text search (default)**:

```python
# Full-text search across all content
results = await search_notes(
    query="JWT authentication",
    search_type="text",
    project="main"
)
```

**Semantic search**:

```python
# Semantic/vector search (if enabled)
results = await search_notes(
    query="user login security",
    search_type="semantic",
    project="main"
)
```

### Search Response

**Result structure**:

```python
{
    "results": [
        {
            "title": "Authentication System",
            "permalink": "authentication-system",
            "folder": "specs",
            "snippet": "...JWT authentication for user login...",
            "score": 0.95,
            "tags": ["auth", "security"],
            "type": "spec",
            "updated": "2025-01-15T09:15:00Z"
        }
    ],
    "total": 15,
    "page": 1,
    "page_size": 10,
    "has_more": true
}
```

### Search Strategies

**Broad to narrow**:

```python
# Start broad
all_auth = await search_notes(
    query="authentication",
    project="main"
)

# Narrow down
jwt_auth = await search_notes(
    query="JWT authentication",
    types=["spec", "decision"],
    project="main"
)

# Very specific
recent_jwt = await search_notes(
    query="JWT token implementation",
    types=["spec"],
    after_date="2025-01-01",
    project="main"
)
```

**Find related content**:

```python
# 1. Search for main topic
auth_notes = await search_notes(
    query="authentication",
    project="main"
)

# 2. Read top result
main_note = await read_note(
    identifier=auth_notes["results"][0]["permalink"],
    project="main"
)

# 3. Build context from relations
context = await build_context(
    url=f"memory://{main_note['permalink']}",
    depth=2,
    project="main"
)

# 4. Search for related terms from relations
for relation in main_note["relations"]:
    related = await search_notes(
        query=relation["target"],
        project="main"
    )
```

**Multi-faceted search**:

```python
# Search by different aspects
by_topic = await search_notes(query="API design", project="main")
by_author = await search_notes(query="Alice", project="main")
by_date = await search_notes(query="", after_date="2025-01-15", project="main")
by_tag = await search_notes(query="#security", project="main")
by_type = await search_notes(query="", types=["decision"], project="main")

# Combine for precision
precise = await search_notes(
    query="API security",
    types=["decision"],
    after_date="2025-01-01",
    project="main"
)
```

---

## Building Context

**Context building enables conversation continuity by traversing the knowledge graph.**

### Basic Context Building

**Simple context**:

```python
# Build context from entity
context = await build_context(
    url="memory://Authentication System",
    project="main"
)

# Returns:
# - The root entity
# - Directly related entities
# - Recent observations
# - Connection paths
```

### Depth Control

**Shallow context (depth=1)**:

```python
# Only immediate connections
shallow = await build_context(
    url="memory://Authentication System",
    depth=1,
    project="main"
)

# Returns:
# - Root entity
# - Entities with direct relations
# - First-degree connections only
```

**Deep context (depth=2)**:

```python
# Two levels of connections
deep = await build_context(
    url="memory://Authentication System",
    depth=2,
    project="main"
)

# Returns:
# - Root entity
# - Direct relations (depth 1)
# - Relations of relations (depth 2)
# - More comprehensive context
```

**Very deep context (depth=3+)**:

```python
# Three or more levels
very_deep = await build_context(
    url="memory://Authentication System",
    depth=3,
    project="main"
)

# Warning: Can return a lot of data
# Use for comprehensive understanding
# May be slow for large graphs
```

### Timeframe Filtering

**Recent context**:

```python
# Last 7 days
recent = await build_context(
    url="memory://Authentication System",
    timeframe="7d",
    project="main"
)

# Natural language timeframes
last_week = await build_context(
    url="memory://API Design",
    timeframe="1 week",
    project="main"
)

last_month = await build_context(
    url="memory://Project Planning",
    timeframe="30 days",
    project="main"
)

# Minimum: 1 day (enforced since v0.15.0)
```

**All-time context**:

```python
# No timeframe = all history
complete = await build_context(
    url="memory://Authentication System",
    depth=2,
    project="main"
)
```

### Context Response Structure

**Response includes**:

```python
{
    "root_entity": {
        "title": "Authentication System",
        "permalink": "authentication-system",
        "content": "...",
        "observations": [...],
        "relations": [...]
    },
    "related_entities": [
        {
            "title": "User Database",
            "permalink": "user-database",
            "relation_type": "requires",
            "distance": 1,
            "content": "...",
            "observations": [...],
            "relations": [...]
        },
        {
            "title": "Login API",
            "permalink": "login-api",
            "relation_type": "implemented_by",
            "distance": 1,
            "content": "...",
            "observations": [...],
            "relations": [...]
        }
    ],
    "paths": [
        {
            "from": "authentication-system",
            "to": "login-api",
            "path": [
                {"entity": "authentication-system", "relation": "implemented_by"},
                {"entity": "login-api"}
            ]
        }
    ],
    "summary": {
        "total_entities": 5,
        "total_relations": 8,
        "max_depth": 2,
        "timeframe": "7d"
    }
}
```

### Context Building Patterns

**Continuing conversations**:

```python
# User: "Let's discuss authentication"

# 1. Search for topic
results = await search_notes(
    query="authentication",
    project="main"
)

# 2. Build context from most relevant
context = await build_context(
    url=f"memory://{results['results'][0]['permalink']}",
    depth=2,
    timeframe="30d",
    project="main"
)

# 3. Use context to inform response
# "Based on our previous work on authentication, including
#  the JWT implementation and OAuth integration..."
```

**Exploring knowledge areas**:

```python
# Start with broad topic
initial = await build_context(
    url="memory://API Design",
    depth=1,
    project="main"
)

# Explore interesting branches
for entity in initial["related_entities"]:
    if "GraphQL" in entity["title"]:
        graphql_context = await build_context(
            url=f"memory://{entity['permalink']}",
            depth=2,
            project="main"
        )
```

**Finding connection paths**:

```python
# Build context from both endpoints
start_context = await build_context(
    url="memory://Frontend App",
    depth=2,
    project="main"
)

end_context = await build_context(
    url="memory://Database Schema",
    depth=2,
    project="main"
)

# Analyze paths in response
# Shows how frontend connects to database through API layer
```

---

## Recording Conversations

**Capturing conversations in Basic Memory enables long-term context and knowledge accumulation.**

### Permission and Transparency

**Always ask before recording**:

```
AI: "Would you like me to save our discussion about API authentication
     to Basic Memory? This will help us continue this conversation later
     and build on what we've learned."

User: "Yes, please"

AI: [Saves to Basic Memory]
    "I've saved our discussion to Basic Memory as 'API Authentication Discussion'."
```

**Be transparent**:
- Ask permission before saving
- Confirm after saving
- Explain what was saved
- Mention how it helps future conversations

**Your role**: As an AI collaborator, you're helping build knowledge that will outlast any particular AI model or session. Write observations and relations that will be valuable to the human (and future AI assistants) years from now. Think: enduring insights, not ephemeral chat logs. These plain-text markdown files are artifacts worth keeping - make them count.

### What to Record

**Good candidates for recording**:

1. **Decisions and Rationales**
```python
await write_note(
    title="Decision: GraphQL vs REST",
    content="""# Decision: GraphQL vs REST

## Context
User asked about API architecture choice.

## Decision
Chose GraphQL for new features, maintain REST for legacy.

## Observations
- [decision] GraphQL for flexibility and performance #api
- [requirement] Mobile app needs efficient data loading #mobile
- [fact] REST API has 50K existing clients #legacy
- [insight] Hybrid approach minimizes migration risk #strategy

## Relations
- implements [[API Modernization Plan]]
- affects [[Mobile Development]]
""",
    folder="decisions",
    project="main"
)
```

2. **Important Discoveries**
```python
await write_note(
    title="Discovery: Database Performance Issue",
    content="""# Discovery: Database Performance Issue

## Context
User reported slow login times.

## Observations
- [problem] Login queries taking 2-3 seconds #performance
- [insight] Missing index on users.email column #database
- [solution] Added index, login now <100ms #fix
- [technique] Used EXPLAIN ANALYZE to identify bottleneck #debugging
- [fact] 80% of queries were sequential scans #metrics

## Resolution
Created index on email column, query time improved 20x.

## Relations
- relates_to [[User Authentication]]
- caused_by [[Database Schema Migration]]
""",
    folder="troubleshooting",
    project="main"
)
```

3. **Action Items and Plans**
```python
await write_note(
    title="Plan: API v2 Migration",
    content="""# Plan: API v2 Migration

## Overview
Discussed migration strategy from REST v1 to GraphQL v2.

## Observations
- [plan] Phased migration over 3 months #roadmap
- [action] Create GraphQL schema this week #task
- [action] Implement parallel APIs next month #task
- [decision] Deprecate v1 after 6-month notice #timeline
- [requirement] Must maintain backward compatibility #constraint

## Timeline
- Week 1-2: Schema design
- Week 3-4: Core API implementation
- Month 2: Client migration support
- Month 3: Documentation and training

## Relations
- implements [[API Modernization Strategy]]
- requires [[GraphQL Schema Design]]
- affects [[All API Clients]]
""",
    folder="planning",
    project="main"
)
```

4. **Connected Topics**
```python
await write_note(
    title="Conversation: Security Best Practices",
    content="""# Conversation: Security Best Practices

## Discussion Summary
User asked about security measures for new API.

## Observations
- [recommendation] Implement rate limiting on all endpoints #security
- [technique] Use JWT with short expiry + refresh tokens #auth
- [requirement] HTTPS only in production #infrastructure
- [technique] Input validation with Pydantic schemas #validation
- [recommendation] Regular security audits quarterly #process

## Key Insights
- Defense in depth approach is essential
- Rate limiting prevents most automated attacks
- Token rotation improves security posture

## Related Topics
- Authentication mechanisms
- Authorization patterns
- Data encryption
- Audit logging

## Relations
- relates_to [[API Security Architecture]]
- implements [[Security Policy]]
- requires [[Rate Limiting Service]]
""",
    folder="conversations",
    project="main"
)
```

### Recording Patterns

**Conversation summary**:

```python
# After substantial discussion
await write_note(
    title=f"Conversation: {topic} - {date}",
    content=f"""# Conversation: {topic}

## Summary
{brief_summary}

## Key Points Discussed
{key_points}

## Observations
{categorized_observations}

## Decisions Made
{decisions}

## Action Items
{action_items}

## Relations
{relevant_relations}
""",
    folder="conversations",
    tags=["conversation", topic_tags],
    project="main"
)
```

**Decision record**:

```python
# For important decisions
await write_note(
    title=f"Decision: {decision_title}",
    content=f"""# Decision: {decision_title}

## Context
{why_decision_needed}

## Decision
{what_was_decided}

## Observations
{categorized_observations}

## Rationale
{reasoning}

## Consequences
{implications}

## Relations
{related_entities}
""",
    folder="decisions",
    note_type="decision",
    project="main"
)
```

**Learning capture**:

```python
# For new knowledge or insights
await write_note(
    title=f"Learning: {topic}",
    content=f"""# Learning: {topic}

## What We Learned
{insights}

## Observations
{categorized_facts}

## How This Helps
{practical_applications}

## Relations
{connected_knowledge}
""",
    folder="learnings",
    project="main"
)
```

### Building on Past Conversations

**Reference previous discussions**:

```python
# 1. Search for related past conversations
past = await search_notes(
    query="API authentication",
    types=["conversation", "decision"],
    project="main"
)

# 2. Build context
context = await build_context(
    url=f"memory://{past['results'][0]['permalink']}",
    depth=2,
    timeframe="30d",
    project="main"
)

# 3. Reference in new conversation
# "Building on our previous discussion about JWT authentication,
#  let's now address the refresh token implementation..."

# 4. Link new note to previous
await write_note(
    title="Refresh Token Implementation",
    content="""# Refresh Token Implementation

## Relations
- builds_on [[Conversation: API Authentication]]
- implements [[JWT Authentication Decision]]
""",
    folder="implementation",
    project="main"
)
```

---

## Editing Notes

**Edit existing notes incrementally without rewriting entire content.**

### Edit Operations

**Available operations**:
- `append`: Add to end of note
- `prepend`: Add to beginning
- `find_replace`: Replace specific text
- `replace_section`: Replace markdown section

### Append Content

**Add to end of note**:

```python
await edit_note(
    identifier="Authentication System",
    operation="append",
    content="""

## New Section

Additional information discovered.

## Observations
- [fact] New security requirement identified #security
""",
    project="main"
)
```

**Use cases**:
- Adding new observations
- Appending related topics
- Adding follow-up information
- Extending discussions

### Prepend Content

**Add to beginning of note**:

```python
await edit_note(
    identifier="Meeting Notes",
    operation="prepend",
    content="""## Update

Important development since meeting.

---

""",
    project="main"
)
```

**Use cases**:
- Adding urgent updates
- Inserting warnings
- Adding important context
- Prepending summaries

### Find and Replace

**Replace specific text**:

```python
await edit_note(
    identifier="API Documentation",
    operation="find_replace",
    find_text="http://api.example.com",
    content="https://api.example.com",
    expected_replacements=3,
    project="main"
)
```

**With expected replacements count**:

```python
# Expects exactly 1 replacement
await edit_note(
    identifier="Config File",
    operation="find_replace",
    find_text="DEBUG = True",
    content="DEBUG = False",
    expected_replacements=1,
    project="main"
)

# Error if count doesn't match
# Prevents unintended changes
```

**Use cases**:
- Updating URLs
- Correcting terminology
- Fixing typos
- Updating version numbers

### Replace Section

**Replace markdown section by heading**:

```python
await edit_note(
    identifier="Project Status",
    operation="replace_section",
    section="## Current Status",
    content="""## Current Status

Project completed successfully.

All milestones achieved ahead of schedule.
""",
    project="main"
)
```

**Replace nested section**:

```python
await edit_note(
    identifier="Technical Docs",
    operation="replace_section",
    section="### Authentication",  # Finds h3 heading
    content="""### Authentication

Updated authentication flow using OAuth 2.0.

See [[OAuth Implementation]] for details.
""",
    project="main"
)
```

**Use cases**:
- Updating status sections
- Replacing outdated information
- Modifying specific topics
- Restructuring content

### Adding Observations

**Append new observations**:

```python
# Read current note
note = await read_note("API Design", project="main")

# Add new observations
await edit_note(
    identifier="API Design",
    operation="append",
    content="""
- [insight] GraphQL reduces API calls by 60% #performance
- [decision] Implement query complexity limiting #security
- [action] Document schema changes weekly #documentation
""",
    project="main"
)
```

### Adding Relations

**Append new relations**:

```python
await edit_note(
    identifier="Authentication System",
    operation="append",
    content="""
- integrates_with [[OAuth Provider]]
- requires [[Rate Limiting Service]]
""",
    project="main"
)
```

**Update relations section**:

```python
await edit_note(
    identifier="API Backend",
    operation="replace_section",
    section="## Relations",
    content="""## Relations
- implements [[API Specification v2]]
- requires [[Database Layer]]
- integrates_with [[Authentication Service]]
- monitored_by [[Logging System]]
- deployed_to [[Production Infrastructure]]
""",
    project="main"
)
```

### Bulk Updates

**Update multiple notes**:

```python
# Search for notes to update
notes = await search_notes(
    query="deprecated",
    project="main"
)

# Update each note
for note in notes["results"]:
    await edit_note(
        identifier=note["permalink"],
        operation="prepend",
        content="⚠️ **DEPRECATED** - See [[New Implementation]]\n\n---\n\n",
        project="main"
    )
```

### Collaborative Editing

**Track changes and updates**:

```python
# Add update log
await edit_note(
    identifier="Living Document",
    operation="append",
    content=f"""

## Update Log

### {current_date}
- Updated authentication section
- Added OAuth examples
- Fixed broken links

""",
    project="main"
)
```

---

## Moving and Organizing

**Organize notes by moving them between folders while maintaining knowledge graph integrity.**

### Basic Move

**Move to new folder**:

```python
await move_note(
    identifier="API Documentation",
    destination_path="docs/api/api-documentation.md",
    project="main"
)
```

**Move with auto-extension**:

```python
# Both work (v0.15.0+)
await move_note(
    identifier="Note",
    destination_path="new-folder/note.md",
    project="main"
)

await move_note(
    identifier="Note",
    destination_path="new-folder/note",  # .md added automatically
    project="main"
)
```

### Organizing Knowledge

**Create folder structure**:

```python
# Move related notes to dedicated folders

# Move specs
await move_note("Authentication Spec", "specs/auth/authentication.md", project="main")
await move_note("API Spec", "specs/api/api-spec.md", project="main")

# Move implementations
await move_note("Auth Service", "services/auth/auth-service.md", project="main")
await move_note("API Server", "services/api/api-server.md", project="main")

# Move decisions
await move_note("Decision: OAuth", "decisions/oauth-decision.md", project="main")

# Move meetings
await move_note("API Review 2025-01-15", "meetings/2025/01/api-review.md", project="main")
```

**Folder hierarchy**:

```
project/
├── specs/
│   ├── auth/
│   └── api/
├── services/
│   ├── auth/
│   └── api/
├── decisions/
├── meetings/
│   └── 2025/
│       └── 01/
├── conversations/
└── learnings/
```

### Batch Organization

**Organize multiple notes**:

```python
# Get all auth-related notes
auth_notes = await search_notes(
    query="authentication",
    project="main"
)

# Move to auth folder
for note in auth_notes["results"]:
    if note["type"] == "spec":
        await move_note(
            identifier=note["permalink"],
            destination_path=f"specs/auth/{note['permalink']}.md",
            project="main"
        )
    elif note["type"] == "decision":
        await move_note(
            identifier=note["permalink"],
            destination_path=f"decisions/auth/{note['permalink']}.md",
            project="main"
        )
```

### Preserving Relations

**Relations are automatically updated**:

```python
# Before move:
# Note A (folder: root) -> relates_to [[Note B]]
# Note B (folder: root)

# Move Note B
await move_note(
    identifier="Note B",
    destination_path="subfolder/note-b.md",
    project="main"
)

# After move:
# Note A (folder: root) -> relates_to [[Note B]]
# Note B (folder: subfolder) <- relation still works!
# Database updated automatically
```

### Renaming

**Move to rename**:

```python
# Rename by moving to same folder with new name
await move_note(
    identifier="Old Name",
    destination_path="same-folder/new-name.md",
    project="main"
)

# Title and permalink updated
# Relations preserved
```

### Archiving

**Move to archive folder**:

```python
# Archive old notes
await move_note(
    identifier="Deprecated Feature",
    destination_path="archive/deprecated/deprecated-feature.md",
    project="main"
)

# Batch archive by date
old_notes = await search_notes(
    query="",
    after_date="2024-01-01",
    project="main"
)

for note in old_notes["results"]:
    if note["updated"] < "2024-06-01":
        await move_note(
            identifier=note["permalink"],
            destination_path=f"archive/2024/{note['permalink']}.md",
            project="main"
        )
```

---

## Error Handling

**Robust error handling ensures reliable AI-human interaction.**

### Missing Project Parameter

**Error**: Tool called without project parameter

**Solution**:

```python
try:
    results = await search_notes(query="test")
except:
    # Show available projects
    projects = await list_memory_projects()

    # Ask user which to use
    # "I need to know which project to search. Available projects: ..."

    # Retry with project
    results = await search_notes(query="test", project="main")
```

**Prevention**:

```python
# Always discover projects first
projects = await list_memory_projects()

# Store active project for session
active_project = projects[0]["name"]

# Use in all calls
results = await search_notes(query="test", project=active_project)
```

### Entity Not Found

**Error**: Note doesn't exist

**Solution**:

```python
try:
    note = await read_note("Nonexistent Note", project="main")
except:
    # Search for similar
    results = await search_notes(query="Note", project="main")

    # Suggest alternatives
    # "I couldn't find 'Nonexistent Note'. Did you mean:"
    # - Similar Note 1
    # - Similar Note 2
```

### Forward Reference Resolution

**Not an error**: Forward references resolve automatically

```python
# Create note with forward reference
response = await write_note(
    title="Implementation",
    content="## Relations\n- implements [[Future Spec]]",
    folder="code",
    project="main"
)

# Response may indicate unresolved reference
# This is OK - will resolve when target created

# Later, create target
await write_note(
    title="Future Spec",
    content="# Future Spec\n...",
    folder="specs",
    project="main"
)

# Reference automatically resolved
# No action needed
```

### Sync Status Issues

**Error**: Data not found, sync in progress

**Solution**:

```python
# Check sync status
status = await sync_status(project="main")

if status["sync_in_progress"]:
    # Inform user
    # "The knowledge base is still syncing. Please wait..."

    # Wait or proceed with available data
    # Can still search/read synced content
else:
    # Sync complete, proceed normally
    results = await search_notes(query="topic", project="main")
```

### Ambiguous References

**Error**: Multiple entities match

**Solution**:

```python
# Ambiguous title
try:
    note = await read_note("API", project="main")
except:
    # Search to disambiguate
    results = await search_notes(query="API", project="main")

    # Show options to user
    # "Multiple notes found with 'API':"
    # - API Specification (specs/)
    # - API Implementation (services/)
    # - API Documentation (docs/)

    # Use specific identifier
    note = await read_note("specs/API Specification", project="main")
```

### Empty Search Results

**Not an error**: No matches found

**Solution**:

```python
results = await search_notes(query="rare topic", project="main")

if results["total"] == 0:
    # Broaden search
    broader = await search_notes(query="topic", project="main")

    # Or suggest creating note
    # "No notes found about 'rare topic'. Would you like me to create one?"
```

### Project Not Found

**Error**: Specified project doesn't exist

**Solution**:

```python
try:
    results = await search_notes(query="test", project="nonexistent")
except:
    # List available projects
    projects = await list_memory_projects()

    # Show to user
    # "Project 'nonexistent' not found. Available projects:"
    # - main
    # - work

    # Offer to create
    # "Would you like to create a new project called 'nonexistent'?"
```

### Edit Conflicts

**Error**: find_replace didn't match expected count

**Solution**:

```python
try:
    await edit_note(
        identifier="Config",
        operation="find_replace",
        find_text="old_value",
        content="new_value",
        expected_replacements=1,
        project="main"
    )
except:
    # Read note to check
    note = await read_note("Config", project="main")

    # Verify text exists
    if "old_value" in note["content"]:
        count = note["content"].count("old_value")
        # Inform user: "Found {count} occurrences, expected 1"

        # Adjust or use replace_all
        await edit_note(
            identifier="Config",
            operation="find_replace",
            find_text="old_value",
            content="new_value",
            replace_all=True,
            project="main"
        )
```

### Permission Errors

**Error**: Can't write to destination

**Solution**:

```python
try:
    await move_note(
        identifier="Note",
        destination_path="/restricted/note.md",
        project="main"
    )
except:
    # Inform user about permission issue
    # "Cannot move note to /restricted/ - permission denied"

    # Suggest alternative
    # "Try moving to a folder within the project directory"

    # Use valid path
    await move_note(
        identifier="Note",
        destination_path="archive/note.md",
        project="main"
    )
```

---

## Advanced Patterns

**Sophisticated techniques for knowledge management and AI collaboration.**

### Knowledge Graph Visualization

**Create visual representation using canvas**:

```python
# Gather entities to visualize
auth_context = await build_context(
    url="memory://Authentication System",
    depth=2,
    project="main"
)

# Create nodes
nodes = [
    {
        "id": "auth-system",
        "type": "file",
        "file": "specs/authentication-system.md",
        "x": 0,
        "y": 0,
        "width": 400,
        "height": 300
    },
    {
        "id": "user-db",
        "type": "file",
        "file": "services/user-database.md",
        "x": 500,
        "y": 0,
        "width": 400,
        "height": 300
    },
    {
        "id": "login-api",
        "type": "file",
        "file": "api/login-api.md",
        "x": 250,
        "y": 400,
        "width": 400,
        "height": 300
    }
]

# Create edges showing relations
edges = [
    {
        "id": "edge-1",
        "fromNode": "auth-system",
        "toNode": "user-db",
        "label": "requires"
    },
    {
        "id": "edge-2",
        "fromNode": "auth-system",
        "toNode": "login-api",
        "label": "implemented_by"
    }
]

# Generate canvas
canvas = await canvas(
    nodes=nodes,
    edges=edges,
    title="Authentication System Overview",
    folder="diagrams",
    project="main"
)

# Opens in Obsidian for interactive exploration
```

### Progressive Knowledge Building

**Build knowledge incrementally over time**:

```python
# Session 1: Create foundation
await write_note(
    title="API Design",
    content="""# API Design

## Observations
- [requirement] Need REST API for mobile app

## Relations
- relates_to [[Mobile Development]]
""",
    folder="planning",
    project="main"
)

# Session 2: Add details
await edit_note(
    identifier="API Design",
    operation="append",
    content="""
- [decision] Using FastAPI framework #python
- [technique] Auto-generate OpenAPI docs
""",
    project="main"
)

# Session 3: Add related entities
await write_note(
    title="API Authentication",
    content="""# API Authentication

## Relations
- part_of [[API Design]]
""",
    folder="specs",
    project="main"
)

# Update original with relation
await edit_note(
    identifier="API Design",
    operation="append",
    content="""
- includes [[API Authentication]]
""",
    project="main"
)

# Session 4: Add implementation
await write_note(
    title="API Implementation",
    content="""# API Implementation

## Relations
- implements [[API Design]]
""",
    folder="code",
    project="main"
)
```

### Cross-Project Knowledge Transfer

**Transfer knowledge between projects**:

```python
# Read from source project
template = await read_note(
    identifier="API Architecture Template",
    project="templates"
)

# Adapt for target project
adapted_content = template["content"].replace(
    "{{PROJECT_NAME}}",
    "New Project"
)

# Write to target project
await write_note(
    title="API Architecture",
    content=adapted_content,
    folder="architecture",
    project="new-project"
)
```

### Knowledge Graph Traversal

**Traverse graph to discover insights**:

```python
# Start with entry point
start = await read_note("Product Roadmap", project="main")

# Traverse relations
visited = set()
to_visit = [start["permalink"]]
all_related = []

while to_visit:
    current = to_visit.pop(0)
    if current in visited:
        continue

    visited.add(current)
    note = await read_note(current, project="main")
    all_related.append(note)

    # Add related entities to queue
    for relation in note["relations"]:
        if relation["target_permalink"] not in visited:
            to_visit.append(relation["target_permalink"])

# Analyze collected knowledge
# - All connected entities
# - Relation patterns
# - Knowledge clusters
```

### Temporal Analysis

**Track knowledge evolution over time**:

```python
# Get recent activity
week1 = await recent_activity(timeframe="7d", project="main")
week2 = await recent_activity(timeframe="14d", project="main")

# Compare what's new
new_this_week = [
    item for item in week1
    if item not in week2
]

# Identify trends
# - What topics are active
# - What areas growing
# - What needs attention
```

### Knowledge Validation

**Ensure knowledge graph integrity**:

```python
# Find all forward references
all_notes = await search_notes(query="", page_size=1000, project="main")

unresolved = []
for note in all_notes["results"]:
    full_note = await read_note(note["permalink"], project="main")

    for relation in full_note["relations"]:
        if not relation.get("target_exists"):
            unresolved.append({
                "source": note["title"],
                "target": relation["target"]
            })

# Report unresolved references
# "Found {len(unresolved)} unresolved references:"
# - Note A -> Missing Target 1
# - Note B -> Missing Target 2
```

### Automated Documentation

**Generate documentation from knowledge graph**:

```python
# Gather all specs
specs = await search_notes(
    query="",
    types=["spec"],
    project="main"
)

# Build comprehensive documentation
doc_content = "# System Documentation\n\n"

for spec in specs["results"]:
    full_spec = await read_note(spec["permalink"], project="main")

    doc_content += f"\n## {full_spec['title']}\n"
    doc_content += f"{full_spec['content']}\n"

    # Add related implementations
    context = await build_context(
        url=f"memory://{spec['permalink']}",
        depth=1,
        project="main"
    )

    implementations = [
        e for e in context["related_entities"]
        if e.get("relation_type") == "implemented_by"
    ]

    if implementations:
        doc_content += "\n### Implementations\n"
        for impl in implementations:
            doc_content += f"- {impl['title']}\n"

# Save generated documentation
await write_note(
    title="Generated System Documentation",
    content=doc_content,
    folder="docs",
    project="main"
)
```

### Knowledge Consolidation

**Merge related notes**:

```python
# Find related notes
related = await search_notes(
    query="authentication",
    project="main"
)

# Read all related
notes_to_merge = []
for note in related["results"]:
    full = await read_note(note["permalink"], project="main")
    notes_to_merge.append(full)

# Consolidate
merged_content = "# Consolidated: Authentication\n\n"

merged_observations = []
merged_relations = []

for note in notes_to_merge:
    merged_observations.extend(note.get("observations", []))
    merged_relations.extend(note.get("relations", []))

# Deduplicate
unique_observations = list({
    obs["content"]: obs for obs in merged_observations
}.values())

unique_relations = list({
    rel["target"]: rel for rel in merged_relations
}.values())

# Build consolidated note
merged_content += "## Observations\n"
for obs in unique_observations:
    merged_content += f"- [{obs['category']}] {obs['content']}"
    if obs.get('tags'):
        merged_content += " " + " ".join(f"#{tag}" for tag in obs['tags'])
    merged_content += "\n"

merged_content += "\n## Relations\n"
for rel in unique_relations:
    merged_content += f"- {rel['type']} [[{rel['target']}]]\n"

# Save consolidated note
await write_note(
    title="Consolidated: Authentication",
    content=merged_content,
    folder="consolidated",
    project="main"
)
```

---

## Tool Reference

**Complete reference for all MCP tools.**

### Content Management

**write_note(title, content, folder, tags, note_type, project)**
- Create or update markdown notes
- Parameters:
  - `title` (required): Note title
  - `content` (required): Markdown content
  - `folder` (required): Destination folder
  - `tags` (optional): List of tags
  - `note_type` (optional): Type of note (stored in frontmatter). Can be "note", "person", "meeting", "guide", etc.
  - `project` (required unless default_project_mode): Target project
- Returns: Created/updated entity with permalink
- Example:
```python
await write_note(
    title="API Design",
    content="# API Design\n...",
    folder="specs",
    tags=["api", "design"],
    note_type="spec",
    project="main"
)
```

**read_note(identifier, page, page_size, project)**
- Read notes with knowledge graph context
- Parameters:
  - `identifier` (required): Title, permalink, or memory:// URL
  - `page` (optional): Page number (default: 1)
  - `page_size` (optional): Results per page (default: 10)
  - `project` (required unless default_project_mode): Target project
- Returns: Entity with content, observations, relations
- Example:
```python
note = await read_note(
    identifier="memory://specs/api-design",
    project="main"
)
```

**edit_note(identifier, operation, content, find_text, section, expected_replacements, project)**
- Edit notes incrementally
- Parameters:
  - `identifier` (required): Note identifier
  - `operation` (required): append, prepend, find_replace, replace_section
  - `content` (required): Content to add/replace
  - `find_text` (optional): Text to find (for find_replace)
  - `section` (optional): Section heading (for replace_section)
  - `expected_replacements` (optional): Expected replacement count
  - `project` (required unless default_project_mode): Target project
- Returns: Updated entity
- Example:
```python
await edit_note(
    identifier="API Design",
    operation="append",
    content="\n- [fact] New requirement",
    project="main"
)
```

**move_note(identifier, destination_path, project)**
- Move notes to new locations
- Parameters:
  - `identifier` (required): Note identifier
  - `destination_path` (required): New path (with or without .md)
  - `project` (required unless default_project_mode): Target project
- Returns: Updated entity with new path
- Example:
```python
await move_note(
    identifier="API Design",
    destination_path="archive/api-design.md",
    project="main"
)
```

**delete_note(identifier, project)**
- Delete notes from knowledge base
- Parameters:
  - `identifier` (required): Note identifier
  - `project` (required unless default_project_mode): Target project
- Returns: Deletion confirmation
- Example:
```python
await delete_note(
    identifier="outdated-note",
    project="main"
)
```

**read_content(path, project)**
- Read raw file content
- Parameters:
  - `path` (required): File path
  - `project` (required unless default_project_mode): Target project
- Returns: Raw file content (text or base64 for binary)
- Example:
```python
content = await read_content(
    path="config/settings.json",
    project="main"
)
```

**view_note(identifier, page, page_size, project)**
- View notes as formatted artifacts
- Parameters: Same as read_note
- Returns: Formatted markdown for display
- Example:
```python
artifact = await view_note(
    identifier="API Design",
    project="main"
)
```

### Knowledge Graph Navigation

**build_context(url, depth, timeframe, max_related, page, page_size, project)**
- Navigate knowledge graph
- Parameters:
  - `url` (required): memory:// URL
  - `depth` (optional): Traversal depth (default: 1)
  - `timeframe` (optional): Time window (e.g., "7d", "1 week")
  - `max_related` (optional): Max related entities (default: 10)
  - `page` (optional): Page number
  - `page_size` (optional): Results per page
  - `project` (required unless default_project_mode): Target project
- Returns: Root entity, related entities, paths
- Example:
```python
context = await build_context(
    url="memory://api-design",
    depth=2,
    timeframe="30d",
    project="main"
)
```

**recent_activity(type, depth, timeframe, project)**
- Get recent changes
- Parameters:
  - `type` (optional): Activity type filter
  - `depth` (optional): Include related entities
  - `timeframe` (optional): Time window (default: "7d")
  - `project` (optional): Target project (omit for all projects)
- Returns: List of recently updated entities
- Example:
```python
activity = await recent_activity(
    timeframe="7d",
    project="main"
)
```

**list_directory(dir_name, depth, file_name_glob, project)**
- Browse directory contents
- Parameters:
  - `dir_name` (optional): Directory path (default: "/")
  - `depth` (optional): Recursion depth (default: 1)
  - `file_name_glob` (optional): File pattern (e.g., "*.md")
  - `project` (required unless default_project_mode): Target project
- Returns: Files and subdirectories
- Example:
```python
contents = await list_directory(
    dir_name="specs",
    depth=2,
    file_name_glob="*.md",
    project="main"
)
```

### Search & Discovery

**search_notes(query, page, page_size, search_type, types, entity_types, after_date, project)**
- Search across knowledge base
- Parameters:
  - `query` (required): Search query
  - `page` (optional): Page number (default: 1)
  - `page_size` (optional): Results per page (default: 10)
  - `search_type` (optional): "text" or "semantic"
  - `types` (optional): Entity type filter
  - `entity_types` (optional): Observation category filter
  - `after_date` (optional): Date filter (ISO format)
  - `project` (required unless default_project_mode): Target project
- Returns: Matching entities with scores
- Example:
```python
results = await search_notes(
    query="authentication",
    types=["spec", "decision"],
    after_date="2025-01-01",
    project="main"
)
```

### Project Management

**list_memory_projects()**
- List all available projects
- Parameters: None
- Returns: List of projects with metadata
- Example:
```python
projects = await list_memory_projects()
```

**create_memory_project(project_name, project_path, set_default)**
- Create new project
- Parameters:
  - `project_name` (required): Project name
  - `project_path` (required): Directory path
  - `set_default` (optional): Set as default (default: False)
- Returns: Created project details
- Example:
```python
await create_memory_project(
    project_name="research",
    project_path="/Users/name/research",
    set_default=False
)
```

**delete_project(project_name)**
- Delete project from configuration
- Parameters:
  - `project_name` (required): Project to delete
- Returns: Deletion confirmation
- Example:
```python
await delete_project(project_name="old-project")
```

**sync_status(project)**
- Check synchronization status
- Parameters:
  - `project` (optional): Target project
- Returns: Sync progress and status
- Example:
```python
status = await sync_status(project="main")
```

### Visualization

**canvas(nodes, edges, title, folder, project)**
- Create Obsidian canvas
- Parameters:
  - `nodes` (required): List of node objects
  - `edges` (required): List of edge objects
  - `title` (required): Canvas title
  - `folder` (required): Destination folder
  - `project` (required unless default_project_mode): Target project
- Returns: Created canvas file
- Example:
```python
await canvas(
    nodes=[{"id": "1", "type": "file", "file": "note.md", "x": 0, "y": 0}],
    edges=[],
    title="Graph View",
    folder="diagrams",
    project="main"
)
```

---

## Best Practices

**Guidelines for effective knowledge management and AI collaboration.**

### 1. Project Setup

**Single-project users**:
- Enable `default_project_mode=true` in config
- Simplifies tool calls
- Less explicit project parameters

**Multi-project users**:
- Keep `default_project_mode=false`
- Always specify project explicitly
- Prevents cross-project errors

**Always start with discovery**:
```python
# First action in conversation
projects = await list_memory_projects()

# Ask user which to use
# Store for session
# Use consistently
```

### 2. Knowledge Structure

**Every note should have**:
- Clear, descriptive title
- 3-5 observations minimum
- 2-3 relations minimum
- Appropriate categories and tags
- Proper frontmatter

**Good structure example**:
```markdown
---
title: Clear Descriptive Title
tags: [relevant, tags, here]
type: note
---

# Title

## Context
Brief background

## Observations
- [category] Specific fact #tag1 #tag2
- [category] Another fact #tag3
- [category] Third fact #tag4

## Relations
- relation_type [[Related Entity 1]]
- relation_type [[Related Entity 2]]
```

### 3. Search Before Creating

**Always search first**:
```python
# Before writing new note
existing = await search_notes(
    query="topic name",
    project="main"
)

if existing["total"] > 0:
    # Update existing instead of creating duplicate
    await edit_note(
        identifier=existing["results"][0]["permalink"],
        operation="append",
        content=new_information,
        project="main"
    )
else:
    # Create new
    await write_note(...)
```

### 4. Use Exact Entity Titles in Relations

**Wrong**:
```markdown
## Relations
- relates_to [[auth system]]  # Won't match "Authentication System"
- implements [[api spec]]      # Won't match "API Specification"
```

**Right**:
```python
# Search for exact title
results = await search_notes(query="Authentication System", project="main")
exact_title = results["results"][0]["title"]

# Use in relation
content = f"## Relations\n- relates_to [[{exact_title}]]"
```

### 5. Meaningful Categories

**Use semantic categories**:
- `[decision]` for choices made
- `[fact]` for objective information
- `[technique]` for methods
- `[requirement]` for needs
- `[insight]` for realizations
- `[problem]` for issues
- `[solution]` for resolutions
- `[action]` for tasks

**Not generic categories**:
- Avoid `[note]`, `[info]`, `[misc]`
- Be specific and intentional

### 6. Descriptive Relation Types

**Use meaningful relation types**:
- `implements` for implementation
- `requires` for dependencies
- `part_of` for hierarchy
- `extends` for enhancement
- `contrasts_with` for alternatives

**Not generic**:
- Avoid overusing `relates_to`
- Be specific about relationship

### 7. Progressive Elaboration

**Build knowledge over time**:
```python
# Session 1: Create foundation
await write_note(
    title="Topic",
    content="Basic structure with initial observations",
    folder="notes",
    project="main"
)

# Session 2: Add details
await edit_note(
    identifier="Topic",
    operation="append",
    content="Additional observations and insights",
    project="main"
)

# Session 3: Add relations
await edit_note(
    identifier="Topic",
    operation="append",
    content="Relations to related topics",
    project="main"
)
```

### 8. Consistent Naming

**Folder structure**:
- specs/ - Specifications
- decisions/ - Decision records
- meetings/ - Meeting notes
- conversations/ - AI conversations
- implementations/ - Code/implementations
- docs/ - Documentation

**File naming**:
- Use descriptive titles
- Consistent format
- Avoid special characters

### 9. Regular Validation

**Check knowledge graph health**:
```python
# Find unresolved references
# Check for orphaned notes
# Verify relation consistency
# Update outdated information
```

### 10. Permission and Transparency

**With users**:
- Always ask before recording
- Confirm after saving
- Explain what was saved
- Describe how it helps

**Recording pattern**:
```
AI: "Would you like me to save our discussion about {topic}?"
User: "Yes"
AI: [Saves to Basic Memory]
    "Saved as '{title}' in {folder}/"
```

### 11. Context Building Strategy

**For new conversations**:
```python
# 1. Search for topic
results = await search_notes(query="topic", project="main")

# 2. Build context from top result
context = await build_context(
    url=f"memory://{results['results'][0]['permalink']}",
    depth=2,
    timeframe="30d",
    project="main"
)

# 3. Use context to inform response
# Reference previous knowledge
# Build on existing understanding
```

### 12. Error Recovery

**Graceful degradation**:
```python
try:
    # Attempt operation
    result = await tool_call(...)
except:
    # Fall back to alternative
    # Inform user of issue
    # Suggest workaround
```

### 13. Incremental Updates

**Prefer editing over rewriting**:
```python
# Good: Incremental update
await edit_note(
    identifier="Note",
    operation="append",
    content="New information",
    project="main"
)

# Avoid: Complete rewrite
# (unless necessary for major restructuring)
```

### 14. Tagging Strategy

**Use tags strategically**:
- Technology: #python #fastapi
- Domain: #auth #security
- Status: #wip #completed
- Priority: #urgent #important
- Category: #bug #feature

**Not too many**:
- 3-5 tags per observation
- Focus on most relevant
- Avoid tag proliferation

### 15. Documentation as Code

**Treat knowledge like code**:
- Version control friendly (markdown)
- Review and refine regularly
- Keep it DRY (Don't Repeat Yourself)
- Link instead of duplicating
- Maintain consistency

---

## Conclusion

This extended guide provides comprehensive coverage of Basic Memory's capabilities for AI assistants. Each section is designed to be self-contained so you can reference or copy specific sections as needed.

For the condensed quick-reference version, see the [AI Assistant Guide](https://github.com/basicmachines-co/basic-memory/blob/main/src/basic_memory/mcp/resources/ai_assistant_guide.md).

For complete documentation including setup, integrations, and advanced features, visit [docs.basicmemory.com](https://docs.basicmemory.com).

**Remember**: Basic Memory is about building persistent, structured knowledge that grows over time. Focus on creating rich observations, meaningful relations, and building a connected knowledge graph that provides lasting value across conversations and sessions.

Built with ♥️ by Basic Machines
