# Release Notes v0.13.0

## Overview

Basic Memory v0.13.0 is a **major release** that transforms Basic Memory into a true multi-project knowledge management system. This release introduces fluid project switching, advanced note editing capabilities, robust file management, and production-ready OAuth authentication - all while maintaining full backward compatibility.

**What's New for Users:**
- 🎯 **Switch between projects instantly** during conversations with Claude
- ✏️ **Edit notes incrementally** without rewriting entire documents
- 📁 **Move and organize notes** with full database consistency
- 🔍 **Search frontmatter tags** to discover content more easily
- 🔐 **OAuth authentication** for secure remote access
- ⚡ **Development builds** automatically published for beta testing

**Key v0.13.0 Accomplishments:**
- ✅ **Complete Project Management System** - Fluid project switching and cross-project operations
- ✅ **Advanced Note Editing** - Incremental editing with append, prepend, find/replace, and section operations  
- ✅ **File Management System** - Full move operations with database consistency and rollback protection
- ✅ **Enhanced Search Capabilities** - Frontmatter tags now searchable, improved content discoverability
- ✅ **OAuth 2.1 Authentication** - Production-ready security for cloud deployments
- ✅ **Unified Database Architecture** - Single app-level database for better performance and project management
- ✅ **Comprehensive Integration Testing** - 77 passing integration tests across all MCP tools
- ✅ **Production Ready** - Complete implementation from planning documents to tested release

## Major Features

### 1. Fluid Project Management 🎯

**Switch between projects instantly during conversations:**

```
💬 "What projects do I have?"
🤖 Available projects:
   • main (current, default)
   • work-notes
   • personal-journal
   • code-snippets

💬 "Switch to work-notes"
🤖 ✓ Switched to work-notes project
   
   Project Summary:
   • 47 entities
   • 125 observations  
   • 23 relations

💬 "What did I work on yesterday?"
🤖 [Shows recent activity from work-notes project]
```

**Key Capabilities:**
- **Instant Project Switching**: Change project context mid-conversation without restart
- **Cross-Project Operations**: Optional `project` parameter on all tools for targeted operations
- **Project Discovery**: List all available projects with status indicators
- **Session Context**: Maintains active project throughout conversation
- **Unified Database**: All projects share a single SQLite database with proper isolation
- **Backward Compatibility**: Existing single-project setups continue to work seamlessly

### 2. Advanced Note Editing ✏️

**Edit notes incrementally without rewriting entire documents:**

```python
# Append new sections to existing notes
edit_note("project-planning", "append", "\n## New Requirements\n- Feature X\n- Feature Y")

# Prepend timestamps to meeting notes
edit_note("meeting-notes", "prepend", "## 2025-05-27 Update\n- Progress update...")

# Replace specific sections under headers
edit_note("api-spec", "replace_section", "New implementation details", section="## Implementation")

# Find and replace with validation
edit_note("config", "find_replace", "v0.13.0", find_text="v0.12.0", expected_replacements=2)
```

**Key Capabilities:**
- **Append Operations**: Add content to end of notes (most common use case)
- **Prepend Operations**: Add content to beginning of notes
- **Section Replacement**: Replace content under specific markdown headers
- **Find & Replace**: Simple text replacements with occurrence counting
- **Smart Error Handling**: Helpful guidance when operations fail
- **Project Context**: Works across all projects with session awareness

### 3. Smart File Management 📁

**Move and organize notes with full database consistency:**

```python
# Simple moves with automatic folder creation
move_note("my-note", "work/projects/my-note.md")

# Cross-project moves
move_note("shared-doc", "archive/old-docs/shared-doc.md", project="personal-notes")

# Rename operations
move_note("old-name", "same-folder/new-name.md")
```

**Key Capabilities:**
- **Database Consistency**: Updates file paths, permalinks, and checksums automatically
- **Search Reindexing**: Maintains search functionality after moves
- **Folder Creation**: Automatically creates destination directories
- **Cross-Project Moves**: Support for moving notes between projects
- **Rollback Protection**: Ensures data integrity during failed operations
- **Link Preservation**: Maintains internal links and references

### 4. Enhanced Search & Discovery 🔍

**Find content more easily with improved search capabilities:**

- **Frontmatter Tag Search**: Tags from YAML frontmatter are now indexed and searchable
- **Improved Content Discovery**: Search across titles, content, tags, and metadata
- **Project-Scoped Search**: Search within specific projects or across all projects
- **Better Search Quality**: Enhanced FTS5 indexing with tag content inclusion

**Example:**
```yaml
---
title: Coffee Brewing Methods
tags: [coffee, brewing, equipment]
---
```
Now searchable by: "coffee", "brewing", "equipment", or "Coffee Brewing Methods"

### 5. OAuth 2.1 Authentication 🔐

**Production-ready security for cloud deployments:**

```bash
# Quick test setup
export FASTMCP_AUTH_SECRET_KEY="your-secret-key"
FASTMCP_AUTH_ENABLED=true basic-memory mcp --transport streamable-http

# Get test token
basic-memory auth test-auth
```

**Key Features:**
- **Multiple Provider Support**: Basic (development), Supabase (production), External providers
- **JWT-based Access Tokens**: Secure token generation and validation
- **PKCE Support**: Enhanced security for authorization code flow
- **MCP Inspector Integration**: Full support for authenticated testing
- **Cloud-Ready**: Enables secure remote access and cloud native deployments

### 6. Unified Database Architecture 🗄️

**Single app-level database for better performance and project management:**

- **Migration from Per-Project DBs**: Moved from multiple SQLite files to single app database
- **Project Isolation**: Proper data separation with project_id foreign keys
- **Better Performance**: Optimized queries and reduced file I/O
- **Easier Backup**: Single database file contains all project data
- **Cloud Preparation**: Architecture ready for cloud deployments

## Complete MCP Tool Suite 🛠️

### New Project Management Tools
- **`list_projects()`** - Discover and list all available projects with status
- **`switch_project(project_name)`** - Change active project context during conversations
- **`get_current_project()`** - Show currently active project with statistics
- **`set_default_project(project_name)`** - Update default project configuration

### New Note Operations Tools
- **`edit_note()`** - Incremental note editing (append, prepend, find/replace, section replace)
- **`move_note()`** - Move notes with database consistency and search reindexing

### Enhanced Existing Tools
All existing tools now support:
- **Optional `project` parameter** for cross-project operations
- **Session context awareness** (uses active project when project not specified)
- **Enhanced error messages** with project context metadata
- **Improved response formatting** with project information footers

### Comprehensive Integration Testing 🧪

**v0.13.0 includes the most comprehensive test suite in Basic Memory's history:**

- **77 Integration Tests**: Complete MCP tool testing across 9 test files
- **End-to-End Coverage**: Tests full workflow from MCP client → server → API → database → file system
- **Real Environment Testing**: Uses actual SQLite databases and file operations (no mocking)
- **Error Scenario Testing**: Comprehensive coverage of edge cases and failure modes
- **Cross-Project Testing**: Validates multi-project operations work correctly

**Test Coverage by Tool:**
- `write_note`: 18 integration tests
- `read_note`: 8 integration tests  
- `search_notes`: 10 integration tests
- `edit_note`: 10 integration tests
- `move_note`: 10 integration tests
- `list_directory`: 10 integration tests
- `project_management`: 8 integration tests (2 skipped)
- `delete_note`: 3 integration tests
- `read_content`: Coverage validated

This ensures every feature works reliably in real-world scenarios.

## User Experience Improvements

### Installation Options

**Multiple ways to install and test Basic Memory:**

```bash
# Stable release
pip install basic-memory

# Beta/pre-releases
pip install basic-memory --pre

# Development builds (automatically published)
pip install basic-memory --pre --force-reinstall
```

**Automatic Versioning**: Uses `uv-dynamic-versioning` for git tag-based releases
- Development builds: `0.12.4.dev26+468a22f` (commit-based)
- Beta releases: `0.13.0b1` (manual tag)
- Stable releases: `0.13.0` (manual tag)

### Bug Fixes & Quality Improvements

**Major issues resolved in v0.13.0:**

- **#118**: Fixed YAML tag formatting to follow standard specification
- **#110**: Fixed `--project` flag consistency across all CLI commands
- **#107**: Fixed write_note update failures with existing notes
- **#93**: Fixed custom permalink handling in frontmatter
- **#52**: Enhanced search capabilities with frontmatter tag indexing
- **FTS5 Search**: Fixed special character handling in search queries
- **Error Handling**: Improved error messages and validation across all tools

## Breaking Changes & Migration

### For Existing Users

**Automatic Migration**: First run will automatically migrate existing data to the new unified database structure. No manual action required.

**What Changes:**
- Database location: Moved to `~/.basic-memory/memory.db` (unified across projects)
- API endpoints: Now require project context (e.g., `/main/entities` instead of `/entities`)
- Configuration: Projects defined in `config.json` are synced with database

**What Stays the Same:**
- All existing notes and data remain unchanged
- Default project behavior maintained for single-project users
- All existing MCP tools continue to work without modification

### For API Consumers

```python
# Old (v0.12.x)
response = client.get("/entities")

# New (v0.13.0)
response = client.get("/main/entities")  # 'main' is default project
```

### For Multi-Project Setup

```json
// config.json example
{
  "projects": {
    "main": "~/basic-memory",
    "work-notes": "~/work/notes",
    "personal": "~/personal/journal"
  },
  "default_project": "main",
  "sync_changes": true
}
```

## API & CLI Changes

### New API Endpoints

#### Project Management
- `GET /projects/projects` - List all projects
- `POST /projects` - Create new project
- `PUT /projects/{name}/default` - Set default project
- `DELETE /{name}` - Delete project

#### Note Operations
- `PATCH /{project}/knowledge/entities/{identifier}` - Edit existing entities incrementally
- `POST /{project}/knowledge/move` - Move entities to new file locations

#### Enhanced Features
- `POST /{project}/prompts/search` - Search with formatted output
- `POST /{project}/prompts/continue-conversation` - Continue with context
- `GET /{project}/directory/tree` - Directory structure navigation
- `GET /{project}/directory/list` - Directory contents listing

### New CLI Commands
- `basic-memory auth` - OAuth client management
- `basic-memory project create` - Create new project
- `basic-memory project list` - List all projects with status
- `basic-memory project set-default` - Set default project
- `basic-memory project delete` - Delete project
- `basic-memory project info` - Show project statistics

### Updated CLI Behavior
- All commands now support `--project` flag consistently
- Project operations use unified database
- Import commands support project targeting
- Sync operates across all active projects by default

## Technical Improvements

### Performance Enhancements
- **Unified Database**: Single SQLite file reduces I/O overhead
- **Optimized Queries**: Better use of indexes and project-scoped filtering
- **Concurrent Initialization**: Projects initialize in parallel
- **Search Improvements**: Enhanced FTS5 indexing with tag content

### Database Schema
- **New Project Table**: Centralized project management
- **Project Foreign Keys**: All entities linked to projects
- **Enhanced Search Index**: Includes frontmatter tags and improved structure
- **Migration Support**: Automatic schema updates with backward compatibility

### Environment Variables (OAuth)
```bash
# Enable OAuth authentication
export FASTMCP_AUTH_ENABLED=true
export FASTMCP_AUTH_SECRET_KEY="your-secret-key"
export FASTMCP_AUTH_PROVIDER="basic"  # or "supabase"

# Start authenticated server
basic-memory mcp --transport streamable-http
```

## Documentation & Resources

### New Documentation
- [OAuth Authentication Guide](docs/OAuth%20Authentication%20Guide.md) - Complete OAuth setup
- [Supabase OAuth Setup](docs/Supabase%20OAuth%20Setup.md) - Production deployment guide
- [Project Management Guide](docs/Project%20Management.md) - Multi-project workflows
- [Note Editing Guide](docs/Note%20Editing.md) - Advanced editing techniques

### Updated Documentation
- [README.md](README.md) - Installation options and beta build instructions
- [CONTRIBUTING.md](CONTRIBUTING.md) - Release process and version management
- [CLAUDE.md](CLAUDE.md) - Development workflow and CI/CD documentation
- [Claude.ai Integration](docs/Claude.ai%20Integration.md) - Updated MCP tool examples

### Quick Start Examples

**Project Switching:**
```
💬 "Switch to my work project and show recent activity"
🤖 [Calls switch_project("work") then recent_activity()]
```

**Note Editing:**
```
💬 "Add a section about deployment to my API docs"
🤖 [Calls edit_note("api-docs", "append", "## Deployment\n...")]
```

**File Organization:**
```
💬 "Move my old meeting notes to the archive folder"
🤖 [Calls move_note("meeting-notes", "archive/old-meetings.md")]
```

## Dependencies & Compatibility

### Added Dependencies
- `python-dotenv` - Environment variable management for OAuth
- `uv-dynamic-versioning>=0.7.0` - Automatic version management from git tags

### Updated Dependencies
- `fastmcp` - Latest version with OAuth and streaming support
- `mcp` - Latest Model Context Protocol implementation
- `pydantic` >= 2.0 - Enhanced validation and schema support
- All development dependencies updated to latest versions

### Python Compatibility
- **Python 3.12+** required (unchanged)
- Full type annotation support
- Async/await patterns throughout
- SQLAlchemy 2.0 modern async patterns

## Release & Version Management

### New Versioning System
- **Automatic versioning** from git tags using `uv-dynamic-versioning`
- **Development builds**: Auto-published on every commit to main
- **Beta releases**: Manual git tags like `v0.13.0b1`
- **Stable releases**: Manual git tags like `v0.13.0`

### CI/CD Pipeline
- **Continuous integration**: Tests run on every PR
- **Development releases**: Auto-publish dev builds to PyPI
- **Production releases**: Triggered by git tags
- **GitHub releases**: Automatic release notes generation

### Getting Updates
```bash
# Stable releases
pip install --upgrade basic-memory

# Beta releases  
pip install --upgrade basic-memory --pre

# Latest development
pip install --upgrade basic-memory --pre --force-reinstall
```

## Looking Forward

### Cloud Native Foundation
v0.13.0 establishes the foundation for cloud deployments:
- **OAuth Authentication**: Production-ready security
- **Streaming HTTP/SSE**: Remote access capabilities  
- **Unified Database**: Cloud-compatible architecture
- **Project Isolation**: Multi-tenant ready structure

### Future Roadmap
- **Cloud deployments** with the unified database and OAuth foundation
- **Real-time collaboration** using the streaming infrastructure
- **Advanced search syntax** (e.g., `tag:coffee brewing:methods`)
- **Batch operations** for large-scale note management
- **Enhanced visualizations** with canvas improvements

### Community & Contributions
- **Integration testing framework** enables confident contributions
- **Comprehensive documentation** supports developer onboarding
- **AI-human collaboration** continues to drive development
- **GitHub integration** facilitates seamless contribution workflow

---

**Basic Memory v0.13.0** represents the largest advancement in the project's history, transforming it from a single-project tool into a sophisticated, multi-project knowledge management system while maintaining the simplicity and local-first principles that make it unique.

The extensive integration testing, production-ready authentication, and cloud preparation ensure this release provides a solid foundation for both current users and future growth. 🚀