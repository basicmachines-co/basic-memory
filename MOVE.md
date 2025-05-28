# move_note() Implementation Plan

## Overview
Implement `move_note()` MCP tool to move notes to new locations while maintaining database consistency and search indexing. Follows the established MCP → API → Service architecture pattern.

## Architecture

```
MCP Tool → API Route → Service Logic
move_note() → POST /knowledge/move → entity_service.move_entity()
```

## Implementation Tasks

### Phase 1: Service Layer
- [ ] Add `move_entity()` method to `EntityService`
- [ ] Handle file path resolution and validation
- [ ] Implement physical file move with rollback on failure
- [ ] Update database (file_path, permalink if configured, checksum)
- [ ] Update search index
- [ ] Add comprehensive error handling

### Phase 2: API Layer  
- [ ] Create `MoveEntityRequest` schema in `schemas/`
- [ ] Add `POST /knowledge/move` route to `knowledge_router.py`
- [ ] Handle project parameter and validation
- [ ] Return formatted success/error messages

### Phase 3: MCP Tool
- [ ] Create `move_note.py` in `mcp/tools/`
- [ ] Implement tool with project parameter support
- [ ] Add to tool registry in `mcp/server.py`
- [ ] Follow existing tool patterns for httpx client usage

### Phase 4: Testing
- [ ] Unit tests for `EntityService.move_entity()`
- [ ] API route tests in `test_knowledge_router.py`
- [ ] MCP tool integration tests
- [ ] Error case testing (rollback scenarios)
- [ ] Cross-project move testing

## Detailed Implementation

### Service Method Signature
```python
# src/basic_memory/services/entity_service.py
async def move_entity(
    self, 
    identifier: str,           # title, permalink, or memory:// URL
    destination_path: str,     # new path relative to project root
    project_config: ProjectConfig
) -> str:
    """Move entity to new location with database consistency."""
```

### API Schema
```python
# src/basic_memory/schemas/memory.py
class MoveEntityRequest(BaseModel):
    identifier: str
    destination_path: str
    project: str
```

### MCP Tool Signature
```python
# src/basic_memory/mcp/tools/move_note.py
@tool
async def move_note(
    identifier: str,           
    destination_path: str,     
    project: Optional[str] = None
) -> str:
    """Move a note to a new location, updating database and maintaining links."""
```

## Service Implementation Logic

### 1. Entity Resolution
- Use existing `link_resolver` to find entity by identifier
- Validate entity exists and get current file_path
- Get current project config for file operations

### 2. Path Validation
- Validate destination_path format
- Ensure destination directory can be created
- Check destination doesn't already exist
- Verify source file exists on filesystem

### 3. File Operations
- Create destination directory if needed
- Move physical file with `Path.rename()`
- Implement rollback on subsequent failures

### 4. Database Updates
- Update entity file_path
- Generate new permalink if `update_permalinks_on_move` is True
- Update frontmatter with new permalink if changed
- Recalculate and update checksum
- Use existing repository methods

### 5. Search Re-indexing
- Call `search_service.index_entity()` with updated entity
- Existing search cleanup should be handled automatically

## Error Handling

### Validation Errors
- Entity not found by identifier
- Source file doesn't exist on filesystem
- Destination already exists
- Invalid destination path format

### Operation Errors  
- File system permission errors
- Database update failures
- Search index update failures

### Rollback Strategy
- On database failure: restore original file location
- On search failure: log error but don't rollback (search can be rebuilt)
- Clear error messages for each failure type

## Return Messages

### Success
```
✅ Note moved successfully

📁 **old/path.md** → **new/path.md**
🔗 Permalink updated: old-permalink → new-permalink
📊 Database and search index updated

<!-- Project: project-name -->
```

### Failure
```
❌ Move failed: [specific error message]

<!-- Project: project-name -->
```

## Testing Strategy

### Unit Tests
- `test_entity_service.py` - Add move_entity tests
- Path validation edge cases
- Permalink generation scenarios
- Error handling and rollback

### Integration Tests  
- `test_knowledge_router.py` - API endpoint tests
- `test_tool_move_note.py` - MCP tool tests
- Cross-project move scenarios
- Full workflow from MCP to filesystem

### Edge Cases
- Moving to same location (no-op)
- Moving across project boundaries
- Moving files with complex wikilink references
- Concurrent move operations

## Future Enhancements (Not v0.13.0)
- Update wikilinks in other files that reference moved note
- Batch move operations
- Move with automatic link fixing
- Integration with git for move tracking