# Release Notes v0.13.0

## Overview

This is a major release that introduces multi-project support, OAuth authentication, server-side templating, and numerous improvements to the MCP server implementation. The codebase has been significantly refactored to support a unified database architecture while maintaining backward compatibility.

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

- Fixed duplicate initialization in MCP server startup
- Fixed JWT audience validation for OAuth tokens
- Fixed trailing slash requirement for MCP endpoints
- Corrected OAuth endpoint paths
- Fixed stdio transport initialization
- Improved error handling in file sync operations
- Fixed search result ranking and filtering

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

## Contributors

This release includes contributions from the Basic Machines team and the AI assistant Claude, demonstrating effective human-AI collaboration in software development.

## Next Steps

- Production deployment guide updates
- Additional OAuth provider implementations
- Performance profiling and optimization
- Enhanced project analytics features