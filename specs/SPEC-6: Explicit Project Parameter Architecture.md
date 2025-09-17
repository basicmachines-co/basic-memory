---
title: 'SPEC-6: Explicit Project Parameter Architecture'
type: spec
permalink: specs/spec-6-explicit-project-parameter-architecture
tags:
- architecture
- mcp
- project-management
- stateless
---

# SPEC-6: Explicit Project Parameter Architecture

## Why

The current session-based project management system has critical reliability issues:

1. **Session State Fragility**: Claude iOS mobile client fails to maintain consistent session IDs across MCP tool calls, causing project switching to silently fail (Issue #74)
2. **Scaling Limitations**: Redis-backed session state creates single-point-of-failure and prevents horizontal scaling
3. **Client Compatibility**: Session tracking works inconsistently across different MCP clients (web, mobile, API)
4. **Hidden Complexity**: Users cannot see or understand "current project" state, leading to confusion when operations execute in wrong projects
5. **Silent Failures**: Operations appear successful but execute in unintended projects, risking data integrity

Evidence from production logs shows each MCP tool call from mobile client receives different session IDs:
```
create_memory_project: session_id=12cdfc24913b48f8b680ed4b2bfdb7ba
switch_project:       session_id=050a69275d98498cbdd227cdb74d9740
list_directory:       session_id=85f3483014af4136a5d435c76ded212f
```

Related Github issue: https://github.com/basicmachines-co/basic-memory-cloud/issues/75

## Status

**Current Status**: Phase 1 Core Implementation Complete ✅
**Target**: Fix Claude iOS session ID consistency issues

### Progress Summary

✅ **Core Architecture Complete (3/3 tools)**
- Stateless `get_active_project()` function implemented
- Session state dependencies removed
- `write_note`, `read_note`, `delete_note` fully updated with project parameters

✅ **Testing & Validation Complete**
- All 34 write_note tests passing (100% success rate)
- Direct function call compatibility verified
- Security validation working with project parameters
- Error handling for non-existent projects validated

✅ **Documentation Complete for Core Tools**
- Comprehensive docstrings with stateless architecture examples
- Project parameter usage clearly documented
- Error handling and security behavior documented

🔄 **Remaining Work**
- 4 content management tools: `edit_note`, `view_note`, `read_content`, `move_note`
- 3 knowledge graph tools: `build_context`, `recent_activity`, `list_directory`
- 2 search tools: `search_notes`, `canvas`
- Project management cleanup: Remove `switch_project`/`get_current_project`

## What

Transform Basic Memory from stateful session-based to stateless explicit project parameter architecture:

### Core Changes
1. **Mandatory Project Parameter**: All MCP tools require explicit `project` parameter
2. **Remove Session State**: Eliminate Redis, session middleware, and `switch_project` tool
3. **Stateless HTTP**: Enable `stateless_http=True` for horizontal scaling
4. **Enhanced Context Discovery**: Improve `recent_activity` to show project distribution
5. **Clear Response Format**: All tool responses display target project information

Implementation Approach                                                                    

- Each tool will directly accept the project parameter                             
- Remove all calls to context-based project retrieval                              
- Validate project exists before operations                                        
- Clear error messages when project not found                                      
- Backward compatibility: Initially keep optional parameter, then make required  

### Affected MCP Tools
**Content Management** (require project parameter):
- `write_note(project, title, content, folder)`
- `read_note(project, identifier)`
- `edit_note(project, identifier, operation, content)`
- `delete_note(project, identifier)`
- `view_note(project, identifier)`
- `read_content(project, path)`

**Knowledge Graph Navigation** (require project parameter):
- `build_context(project, url, timeframe, depth, max_related)`
- `recent_activity(project, timeframe, depth, max_related)`
- `list_directory(project, dir_name, depth, file_name_glob)`

**Search & Discovery** (require project parameter):
- `search_notes(project, query, search_type, types, entity_types)`

**Visualization** (require project parameter):
- `canvas(project, nodes, edges, title, folder)`

**Project Management** (unchanged - already stateless):
- `list_memory_projects()`
- `create_memory_project(project_name, project_path, set_default)`
- `delete_project(project_name)`
- `get_current_project()` - Remove this tool
- `switch_project(project_name)` - Remove this tool
- `set_default_project(project_name, activate)` - Remove activate parameter

## How (High Level)

### Phase 1: Basic Memory Core (basic-memory repository)

#### MCP Tool Updates

Phase 1: Core Changes 

1. Update project_context.py

  - [x] Make project parameter mandatory for get_active_project()
  - [x] Remove session state handling

2. Update Content Management Tools (6 tools)

  - [x] write_note: Make project parameter required, not optional
  - [x] read_note: Make project parameter required
  - [ ] edit_note: Add required project parameter
  - [x] delete_note: Add required project parameter
  - [ ] view_note: Add required project parameter
  - [ ] read_content: Add required project parameter                                                                             

4. Update Knowledge Graph Navigation Tools (3 tools)                                                                         

  - [ ] build_context: Add required project parameter                                                                            
  - [ ] recent_activity: Make project parameter required                                                                         
  - [ ] list_directory: Add required project parameter                                                                           

5. Update Search & Visualization Tools (2 tools)                                                                             

  - [ ] search_notes: Add required project parameter                                                                             
  - [ ] canvas: Add required project parameter

5. Update Project Management Tools    

- [ ] Remove switch_project tool completely                                                                                    
- [ ] Remove get_current_project tool completely                                                                               
- [ ] Update set_default_project to remove activate parameter                                                                  
- [ ] Keep list_memory_projects, create_memory_project, delete_project unchanged

6. Enhance recent_activity Response                                                                                          

- [ ] Add project distribution info showing activity across all projects                                                       
- [ ] Include project usage stats in response

7. Update Tool Documentation

  - [x] Update write_note docstring with stateless architecture examples
  - [x] Update read_note docstring with project parameter examples
  - [x] Update delete_note docstring with comprehensive usage guidance
  - [ ] Update remaining tool docstrings with project parameter examples

8. Update Tool Responses

  - [x] Add clear project indicator to all tool responses (write_note, read_note, delete_note)
  - [x] Format: "project: {project_name}" in response metadata
  - [x] Add project metadata footer for LLM awareness
  - [ ] Update remaining tool responses to include project indicators

9. Comprehensive Testing

  - [x] Update all 34 write_note tests to use stateless architecture (100% passing)
  - [x] Verify direct function call compatibility (bypassing MCP layer)
  - [x] Test security validation with project parameters
  - [x] Validate error handling for non-existent projects
  - [ ] Update tests for remaining tools to use project parameters                                                                   
                                                                                                                             
Phase 2: Testing & Validation   

8. Update Tests                                                 

- [ ] Modify all MCP tool tests to pass required project parameter  
- [ ] Remove tests for deleted tools (switch_project, get_current_project)
- [ ] Add tests for project parameter validation

#### Enhanced recent_activity Response
```json
{
  "recent_notes": [...],
  "project_activity": {
    "research-project": {
      "operations": 5,
      "last_used": "30 minutes ago",
      "recent_folders": ["experiments", "findings"]
    },
    "work-notes": {
      "operations": 2,
      "last_used": "2 hours ago",
      "recent_folders": ["meetings", "planning"]
    }
  },
  "total_projects": 3
}
```

#### Response Format Updates
```
✓ Note created successfully

Project: research-project
File: experiments/Neural Network Results.md
Permalink: research-project/neural-network-results
```

### Phase 2: Cloud Service Simplification (basic-memory-cloud repository)

#### Remove Session Infrastructure
1. Delete `apps/mcp/src/basic_memory_cloud_mcp/middleware/session_state.py`
2. Delete `apps/mcp/src/basic_memory_cloud_mcp/middleware/session_logging.py`
3. Update `apps/mcp/src/basic_memory_cloud_mcp/main.py`:
   ```python
   # Remove session middleware
   # server.add_middleware(SessionStateMiddleware)

   # Enable stateless HTTP
   mcp = FastMCP(name="basic-memory-mcp", stateless_http=True)
   ```

#### Deployment Simplification
1. Remove Redis from `fly.toml`
2. Remove Redis environment variables
3. Update health checks to not depend on Redis

### Phase 3: Conversational Project Management

#### Claude Behavior Pattern
1. **Project Discovery**:
   ```
   Claude: Let me check your recent activity...
   [calls recent_activity() - no project needed for discovery]

   I see you've been working in:
   - research-project (5 operations, 30 min ago)
   - work-notes (2 operations, 2 hours ago)

   Which project should I use for this operation?
   ```

2. **Context Maintenance**:
   ```
   User: Use research-project
   Claude: Working in research-project.
   [All subsequent operations use project="research-project"]
   ```

3. **Explicit Project Switching**:
   ```
   User: Check work-notes for that meeting summary
   Claude: Let me search work-notes for the meeting summary.
   [Uses project="work-notes" for specific operation]
   ```

## How to Evaluate

### Success Criteria

#### 1. Functional Completeness
- [ ] All MCP tools accept required `project` parameter
- [ ] All MCP tools validate project exists before execution
- [ ] `switch_project` and `get_current_project` tools removed
- [ ] No Redis dependencies in deployment
- [ ] `recent_activity` shows project distribution
- [ ] All responses display target project clearly

#### 2. Cross-Client Compatibility Testing
Test identical operations across all clients:
- [ ] **Claude Desktop**: All operations work with explicit projects
- [ ] **Claude Code**: All operations work with explicit projects
- [ ] **Claude Mobile iOS**: All operations work with explicit projects
- [ ] **API clients**: All operations work with explicit projects
- [ ] **CLI tools**: All operations work with explicit projects

#### 3. Session Independence Verification
- [ ] Operations work identically with/without session tracking
- [ ] No behavioral differences between clients
- [ ] Mobile client session ID changes do not affect operations
- [ ] Redis can be completely removed without functional impact

#### 4. Performance & Scaling
- [ ] `stateless_http=True` enabled successfully
- [ ] No Redis memory usage
- [ ] Horizontal scaling possible (multiple MCP instances)
- [ ] Response times unchanged or improved

#### 5. User Experience Testing
**Project Discovery Flow**:
- [ ] `recent_activity()` provides useful project context
- [ ] Claude can intelligently suggest projects based on activity
- [ ] Project switching is explicit and clear in conversation

**Error Handling**:
- [ ] Clear error messages for non-existent projects
- [ ] Helpful suggestions when project parameter missing
- [ ] No silent failures or wrong-project operations

**Response Clarity**:
- [ ] Every operation clearly shows target project
- [ ] Users always know which project is being operated on
- [ ] No confusion about "current project" state

#### 6. Migration Safety
- [ ] Backward compatibility period with optional project parameter
- [ ] Clear migration documentation for existing users
- [ ] Data integrity maintained during transition
- [ ] No data loss during migration

### Test Scenarios

#### Core Functionality Test
```bash
# Test all tools work with explicit project
write_note(project="test-proj", title="Test", content="Content", folder="docs")
read_note(project="test-proj", identifier="Test")
edit_note(project="test-proj", identifier="Test", operation="append", content="More")
search_notes(project="test-proj", query="Content")
list_directory(project="test-proj", dir_name="docs")
delete_note(project="test-proj", identifier="Test")
```

#### Cross-Client Consistency Test
Run identical test sequence on:
1. Claude Desktop
2. Claude Code
3. Claude Mobile iOS
4. API client
5. CLI tools

Verify all clients:
- Accept explicit project parameters
- Return identical responses
- Show same project information
- Have no session dependencies

#### Session Independence Test
1. Monitor session IDs during operations
2. Verify operations work with changing session IDs
3. Confirm Redis removal doesn't affect functionality
4. Test with multiple concurrent clients

### Acceptance Criteria

**Must Have**:
- All MCP tools require and use explicit project parameter
- No session state dependencies remain
- Universal client compatibility achieved
- Clear project information in all responses

**Should Have**:
- Enhanced `recent_activity` with project distribution
- Smooth migration path for existing users
- Improved performance with stateless architecture

**Could Have**:
- Smart project suggestions based on content/context
- Project shortcuts for common operations
- Advanced project analytics in responses

## Notes

### Breaking Changes
This is a **breaking change** that requires:
- All MCP clients to pass project parameter
- Migration of existing workflows
- Update of all documentation and examples

### Implementation Order
1. **basic-memory core** - Update MCP tools to accept project parameter (optional initially)
2. **Testing** - Verify all clients work with explicit projects
3. **Cloud service** - Remove session infrastructure
4. **Migration** - Make project parameter mandatory
5. **Cleanup** - Remove deprecated tools and middleware

### Related Issues
- Fixes #74 (Claude iOS session state bug)
- Implements #75 (Mandatory project parameter architecture)
- Enables future horizontal scaling
- Simplifies multi-tenant architecture

### Dependencies
- Requires coordination between basic-memory and basic-memory-cloud repositories
- Needs client-side updates for smooth transition
- Documentation updates across all materials
