# App-Level Database Refactoring

This document outlines the plan for migrating Basic Memory from per-project SQLite databases to a single app-level database that manages all knowledge data across projects.

## Goals

- Move to a single app-level SQLite database for all knowledge data
- Deprecate per-project databases completely
- Add project information to entities, observations, and relations
- Simplify project switching and management
- Enable better multi-project support for the Pro app
- Prepare for cloud/GoHighLevel integration

## Architecture Changes

We're moving from:
```
~/.basic-memory/config.json (project list)
~/basic-memory/[project-name]/.basic-memory/memory.db (one DB per project)
```

To:
```
~/.basic-memory/config.json (project list)  <- same
~/.basic-memory/memory.db (app-level DB with project/entity/observation/search_index tables)
~/basic-memory/[project-name]/.basic-memory/memory.db (project DBs deprecated) <- we are removing these
```

## Implementation Tasks

### 1. Configuration Changes

- [x] Update config.py to use a single app database for all projects
- [x] Add functions to get app database path for all operations
- [x] Keep JSON-based config.json for project listing/paths
- [x] Update project configuration loading to use app DB for all operations


### 3. Project Model Implementation

- [x] Create Project SQLAlchemy model in models/project.py
- [x] Define attributes: id, name, path, config, etc.
- [x] Add proper indexes and constraints
- [x] Add project_id foreign key to Entity, Observation, and Relation models
- [x] Create migration script for updating schema with project relations
- [x] Implement app DB initialization with project table

### 4. Repository Layer Updates

- [x] Create ProjectRepository for CRUD operations on Project model
- [x] Update base Repository class to filter queries by project_id
- [x] Update existing repositories to use project context automatically
- [x] Implement query scoping to specific projects
- [x] Add functions for project context management

### 5. Search Functionality Updates

- [x] Update search_index table to include project_id
- [x] Modify search queries to filter by project_id
- [x] Update FTS (Full Text Search) to be project-aware
- [x] Add appropriate indices for efficient project-scoped searches
- [x] Update search repository for project context

### 6. Service Layer Updates

- [x] Update ProjectService to manage projects in the database
- [x] Add methods for project creation, deletion, updating
- [x] Modify existing services to use project context
- [x] Update initialization service for app DB setup
- [x] ~~Implement project switching logic~~

### 7. Sync Service Updates

- [x] Modify background sync service to handle project context
- [x] Update file watching to support multiple project directories
- [x] Add project context to file sync events
- [x] Update file path resolution to respect project boundaries
- [x] Handle file change detection with project awareness

### 8. API Layer Updates

- [x] Update API endpoints to include project context
- [x] Create new endpoints for project management
- [x] Modify dependency injection to include project context
- [x] Add request/response models for project operations
- [x] ~~Implement middleware for project context handling~~
- [x] Update error handling to include project information

### 9. MCP Tools Updates

- [x] Update MCP tools to include project context
- [x] Add project selection capabilities to MCP server
- [x] Update context building to respect project boundaries
- [x] Update file operations to handle project paths correctly
- [x] Add project-aware helper functions for MCP tools

### 10. CLI Updates

- [x] Update CLI commands to work with app DB
- [x] Add or update project management commands
- [x] Implement project switching via app DB
- [x] Ensure CLI help text reflects new project structure
- [x] ~~Add migration commands for existing projects~~
- [x] Update project CLI commands to use the API with direct config fallback
- [x] Added tests for CLI project commands

### 11. Performance Optimizations

- [x] Add proper indices for efficient project filtering
- [x] Optimize queries for multi-project scenarios
- [x] ~~Add query caching if needed~~
- [x] Monitor and optimize performance bottlenecks

### 12. Testing Updates

- [x] Update test fixtures to support project context
- [x] Add multi-project testing scenarios
- [x] Create tests for migration processes
- [ ] Test performance with larger multi-project datasets

### 13 Migrations

- [x] project table
- [x] search project_id index
- [x] project import/sync - during initialization

## Database Schema Changes

### New Project Table
```sql
CREATE TABLE project (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    path TEXT NOT NULL,
    config JSON,
    is_active BOOLEAN DEFAULT TRUE,
    is_default BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### Modified Entity Table
```sql
ALTER TABLE entity ADD COLUMN project_id INTEGER REFERENCES project(id);
CREATE INDEX ix_entity_project_id ON entity(project_id);
```

### Modified Observation Table
```sql
-- No direct changes needed as observations are linked to entities which have project_id
CREATE INDEX ix_observation_entity_project_id ON observation(entity_id, project_id);
```

### Modified Relation Table
```sql
-- No direct changes needed as relations are linked to entities which have project_id
CREATE INDEX ix_relation_from_project_id ON relation(from_id, project_id);
CREATE INDEX ix_relation_to_project_id ON relation(to_id, project_id);
```

## Migration Path

For existing projects, we'll:
1. Create the project table in the app database
2. For each project in config.json:
   a. Register the project in the project table
   b. Import all entities, observations, and relations from the project's DB
   c. Set the project_id on all imported records
3. Validate that all data has been migrated correctly
4. Keep config.json but use the database as the source of truth

## Testing

- [x] Test project creation, switching, deletion
- [x] Test knowledge operations (entity, observation, relation) with project context 
- [x] Verify existing projects can be migrated successfully
- [x] Test multi-project operations
- [x] Test error cases (missing project, etc.)
- [x] Test CLI commands with multiple projects
- [x] Test CLI error handling for API failures
- [x] Test CLI commands use only API, no config fallback

## Current Status

The app-level database refactoring is now complete! We have successfully:

1. Migrated from per-project SQLite databases to a single app-level database
2. Added project context to all layers of the application (models, repositories, services, API)
3. Implemented bidirectional synchronization between config.json and the database
4. Updated all API endpoints to include project context
5. Enhanced project management capabilities in both the API and CLI
6. Added comprehensive test coverage for project operations
7. Modified the directory router and all other routers to respect project boundaries

The only remaining task is to thoroughly test performance with larger multi-project datasets, which can be done as part of regular usage monitoring.

## CLI API Integration

The CLI commands have been updated to use the API endpoints for project management operations. This includes:

1. The `project list` command now fetches projects from the API
2. The `project add` command creates projects through the API
3. The `project remove` command removes projects through the API
4. The `project default` command sets the default project through the API
5. Added a new `project sync` command to synchronize projects between config and database
6. The `project current` command now shows detailed project information from the API

This approach ensures that project operations performed through the CLI are synchronized with the database, maintaining consistency between the configuration file and the app-level database. Failed API requests result in a proper error message instructing the user to ensure the Basic Memory server is running, rather than falling back to direct config updates. This ensures that the database remains the single source of truth for project information.