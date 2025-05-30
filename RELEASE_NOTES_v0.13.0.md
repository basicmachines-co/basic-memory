# Release Notes v0.13.0

## Overview

This is a major release that introduces multi-project support, OAuth authentication, server-side templating, and numerous improvements to the MCP server implementation. The codebase has been significantly refactored to support a unified database architecture while maintaining backward compatibility.

**Key v0.13.0 Accomplishments:**
- ✅ **Complete Project Management System** - Fluid project switching and cross-project operations
- ✅ **Advanced Note Editing** - Incremental editing with append, prepend, find/replace, and section operations  
- ✅ **File Management System** - Full move operations with database consistency and rollback protection
- ✅ **Enhanced MCP Tool Suite** - 5+ new tools with comprehensive error handling and session management
- ✅ **Full Test Coverage** - 38+ new tests across service, API, and MCP layers
- ✅ **Production Ready** - Complete implementation from planning documents to tested release

## Major Features

### 1. Multi-Project Support 🎯
- **Unified Database Architecture**: All projects now share a single SQLite database with proper isolation
- **Project Management API**: New endpoints for creating, updating, and managing projects
- **Project Configuration**: Projects can be defined in `config.json` and synced with the database
- **Default Project**: Backward compatibility maintained with automatic default project creation
- **Project Switching**: CLI commands and API endpoints now support project context

### 2. OAuth 2.1 Authentication 🔐
- **Multiple Provider Support**:
  - Basic (in-memory) provider for development
  - Supabase provider for production deployments
  - External providers (GitHub, Google) framework
- **JWT-based Access Tokens**: Secure token generation and validation
- **PKCE Support**: Enhanced security for authorization code flow
- **MCP Inspector Integration**: Full support for authenticated testing
- **CLI Commands**: `basic-memory auth register-client` and `basic-memory auth test-auth`

### 3. Server-Side Template Engine 📝
- **Handlebars Templates**: Server-side rendering of prompts and responses
- **Custom Helpers**: Rich set of template helpers for formatting
- **Structured Output**: XML-formatted responses for better LLM consumption
- **Template Caching**: Improved performance with template compilation caching

### 4. Enhanced Import System 📥
- **Unified Importer Framework**: Base class for all importers with consistent interface
- **API Support**: New `/import` endpoints for triggering imports via API
- **Progress Tracking**: Real-time progress updates during import operations
- **Multiple Formats**:
  - ChatGPT conversations
  - Claude conversations  
  - Claude projects
  - Memory JSON format

### 5. Directory Navigation 📁
- **Directory Service**: Browse and navigate project file structure
- **API Endpoints**: `/directory/tree` and `/directory/list` endpoints
- **Hierarchical View**: Tree structure representation of knowledge base

## API Changes

### New Endpoints

#### Project Management
- `GET /projects` - List all projects
- `POST /projects` - Create new project
- `GET /projects/{project_id}` - Get project details
- `PUT /projects/{project_id}` - Update project
- `DELETE /projects/{project_id}` - Delete project
- `POST /projects/{project_id}/set-default` - Set default project

#### Import API
- `GET /{project}/import/types` - List available importers
- `POST /{project}/import/{importer_type}/analyze` - Analyze import source
- `POST /{project}/import/{importer_type}/preview` - Preview import
- `POST /{project}/import/{importer_type}/execute` - Execute import

#### Directory API
- `GET /{project}/directory/tree` - Get directory tree
- `GET /{project}/directory/list` - List directory contents

#### Prompt Templates
- `POST /{project}/prompts/search` - Search with formatted output
- `POST /{project}/prompts/continue-conversation` - Continue conversation with context

#### Management API
- `GET /management/sync/status` - Get sync status
- `POST /management/sync/start` - Start background sync
- `POST /management/sync/stop` - Stop background sync

#### Note Operations API  
- `PATCH /{project}/knowledge/entities/{identifier}` - Edit existing entities incrementally
- `POST /{project}/knowledge/move` - Move entities to new file locations

### Updated Endpoints

All knowledge-related endpoints now require project context:
- `/{project}/entities`
- `/{project}/observations`
- `/{project}/search`
- `/{project}/memory`

## CLI Changes

### New Commands
- `basic-memory auth` - OAuth client management
- `basic-memory project create` - Create new project
- `basic-memory project list` - List all projects
- `basic-memory project set-default` - Set default project
- `basic-memory project delete` - Delete project
- `basic-memory project info` - Show project statistics

### Updated Commands
- Import commands now support `--project` flag
- Sync commands operate on all active projects by default
- MCP server defaults to stdio transport (use `--transport streamable-http` for HTTP)

## Configuration Changes

### config.json Structure
```json
{
  "projects": {
    "main": "~/basic-memory",
    "my-project": "~/my-notes",
    "work": "~/work/notes"
  },
  "default_project": "main",
  "sync_changes": true
}
```

### Environment Variables
- `FASTMCP_AUTH_ENABLED` - Enable OAuth authentication
- `FASTMCP_AUTH_SECRET_KEY` - JWT signing key
- `FASTMCP_AUTH_PROVIDER` - OAuth provider type
- `FASTMCP_AUTH_REQUIRED_SCOPES` - Required OAuth scopes

## Database Changes

### New Tables
- `project` - Project definitions and metadata
- Migration: `5fe1ab1ccebe_add_projects_table.py`

### Schema Updates
- All knowledge tables now include `project_id` foreign key
- Search index updated to support project filtering
- Backward compatibility maintained via default project

## Performance Improvements

- **Concurrent Initialization**: Projects initialize in parallel
- **Optimized Queries**: Better use of indexes and joins
- **Template Caching**: Compiled templates cached in memory
- **Batch Operations**: Reduced database round trips

## Bug Fixes

### Core Functionality Fixes ✅
- **#118: Fixed YAML tag formatting** - Tags now follow standard YAML specification (`tags: [basicmemory]` instead of `tags: ['#basicmemory']`)
- **#110: Fixed --project flag consistency** - CLI commands now properly respect `--project` flag across all operations
- **#107: Fixed write_note update failures** - EntityParser now handles absolute paths correctly (resolved in commit 9bff1f7)
- **#93: Fixed custom permalink handling** - write_note now respects user-specified permalinks in frontmatter for both new and existing notes

### Infrastructure Fixes
- Fixed duplicate initialization in MCP server startup
- Fixed JWT audience validation for OAuth tokens
- Fixed trailing slash requirement for MCP endpoints
- Corrected OAuth endpoint paths
- Fixed stdio transport initialization
- Improved error handling in file sync operations
- Fixed search result ranking and filtering

## Enhancements

### Knowledge Management Improvements ✅
- **#52: Enhanced search capabilities** - Frontmatter tags are now included in the FTS5 search index, improving content discoverability
- **Improved search quality** - Tags from YAML frontmatter (both list and string formats) are indexed and searchable

## Breaking Changes

- **Project Context Required**: API endpoints now require project context
- **Database Location**: Unified database at `~/.basic-memory/memory.db`
- **Import Module Restructure**: Import functionality moved to dedicated module

## Migration Guide

### For Existing Users

1. **Automatic Migration**: First run will migrate existing data to default project
2. **Project Configuration**: Add projects to `config.json` if using multiple projects
3. **API Updates**: Update API calls to include project context

### For API Consumers

```python
# Old
response = client.get("/entities")

# New  
response = client.get("/main/entities")  # 'main' is default project
```

### For OAuth Setup

```bash
# Enable OAuth
export FASTMCP_AUTH_ENABLED=true
export FASTMCP_AUTH_SECRET_KEY="your-secret-key"

# Start server
basic-memory mcp --transport streamable-http

# Get token
basic-memory auth test-auth
```

## Dependencies

### Added
- `python-dotenv` - Environment variable management
- `pydantic` >= 2.0 - Enhanced validation

### Updated
- `fastmcp` to latest version
- `mcp` to latest version
- All development dependencies updated

## Documentation

- New: [OAuth Authentication Guide](docs/OAuth%20Authentication%20Guide.md)
- New: [Supabase OAuth Setup](docs/Supabase%20OAuth%20Setup.md)
- Updated: [Claude.ai Integration](docs/Claude.ai%20Integration.md)
- Updated: Main README with project examples

## Testing

- Added comprehensive test coverage for new features
- OAuth provider tests with full flow validation
- Template engine tests with various scenarios
- Project service integration tests
- Import system unit tests
- **Enhanced MCP Tool Testing**: 16 new tests for `move_note()`, 20+ tests for `edit_note()`
- **API Integration Testing**: 11 new tests for move entity endpoints
- **Service Layer Testing**: 11 comprehensive tests for entity move operations
- **Full Stack Testing**: Complete coverage from MCP tools through API to database

## New MCP Tools & Features

### 6. Enhanced MCP Tool Set 🛠️

#### Project Management Tools ✅
- **`list_projects()`** - Discover and list all available projects
- **`switch_project(project_name)`** - Change active project context during conversations
- **`get_current_project()`** - Show currently active project with statistics
- **`set_default_project(project_name)`** - Update default project configuration
- **Cross-Project Operations** - Optional `project` parameter on all tools for targeted operations

#### Note Editing Tools ✅
- **`edit_note()`** - Incremental note editing without full content replacement
  - **Append Operation**: Add content to end of notes
  - **Prepend Operation**: Add content to beginning of notes  
  - **Find & Replace**: Simple text replacements with occurrence counting
  - **Section Replace**: Replace content under specific markdown headers
  - **Smart Error Handling**: Helpful guidance when operations fail

#### File Management Tools ✅
- **`move_note()`** - Move notes to new locations with full consistency
  - **Database Consistency**: Updates file paths, permalinks, and checksums
  - **Search Reindexing**: Maintains search functionality after moves
  - **Folder Creation**: Automatically creates destination directories
  - **Cross-Project Moves**: Support for moving between projects
  - **Rollback on Failure**: Ensures data integrity during failed operations

#### Enhanced Session Management
- **Fluid Project Switching**: Change project context mid-conversation
- **Session State Persistence**: Maintains active project throughout MCP session
- **Project Context Metadata**: All tool responses include project information
- **Backward Compatibility**: Defaults to main project for existing workflows

### 7. Advanced Note Operations 📝

#### Incremental Editing Capabilities
```python
# Append new sections
edit_note("project-planning", "append", "\n## New Requirements\n- Feature X\n- Feature Y")

# Prepend timestamps to meeting notes
edit_note("meeting-notes", "prepend", "## 2025-05-27 Update\n- Progress update...")

# Replace specific sections
edit_note("api-spec", "replace_section", "New implementation details", section="## Implementation")

# Find and replace with validation
edit_note("config", "find_replace", "v0.13.0", find_text="v0.12.0", expected_replacements=2)
```

#### File Movement Operations
```python
# Simple moves with automatic folder creation
move_note("my-note", "work/projects/my-note.md")

# Cross-project moves
move_note("shared-doc", "archive/old-docs/shared-doc.md", project="personal-notes")

# Rename operations
move_note("old-name", "same-folder/new-name.md")
```

### 8. Comprehensive Testing Coverage 🧪
- **Service Layer Tests**: 11 comprehensive tests for `move_entity()` service method
- **API Integration Tests**: 11 tests for move entity API endpoints  
- **MCP Tool Tests**: 16 tests for `move_note()` tool covering all scenarios
- **Error Handling Tests**: Complete coverage of validation, rollback, and edge cases
- **Cross-Layer Integration**: Full workflow testing from MCP → API → Service → Database

## Updated Endpoints

### Knowledge Management API
- `PATCH /{project}/knowledge/entities/{identifier}` - Edit existing entities incrementally
- `POST /{project}/knowledge/move` - Move entities to new file locations

### Enhanced Tool Capabilities
All existing MCP tools now support:
- Optional `project` parameter for cross-project operations
- Session context awareness (uses active project when project not specified)
- Enhanced error messages with project context metadata