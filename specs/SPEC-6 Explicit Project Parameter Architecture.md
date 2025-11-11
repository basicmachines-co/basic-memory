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

**Current Status**: **ALL PHASES COMPLETE** ✅ **PRODUCTION DEPLOYED**
**Target**: Fix Claude iOS session ID consistency issues ✅ **ACHIEVED**
**Draft PR**: https://github.com/basicmachines-co/basic-memory/pull/298 ✅ **MERGED & DEPLOYED**

### 🎉 **COMPLETE SUCCESS - PRODUCTION READY**

**ALL PHASES OF SPEC-6 IMPLEMENTATION COMPLETE!** The stateless architecture has been successfully implemented across both Basic Memory core and Basic Memory Cloud, representing a **fundamental architectural improvement** that completely solves the Claude iOS compatibility issue while providing superior scalability and reliability.

#### Implementation Summary:
- **16 files modified** with 582 additions and 550 deletions
- **All 17 MCP tools** converted to stateless architecture
- **147 tests updated** across 5 test files (100% passing)
- **Complete session state removal** from core MCP tools
- **Enhanced error handling** and security validations preserved

### Progress Summary

✅ **Complete Stateless Architecture Implementation (All 17 tools)** - **PRODUCTION DEPLOYED**
- Stateless `get_active_project()` function implemented and deployed ✅
- All session state dependencies removed across entire MCP server ✅
- All MCP tools require explicit `project` parameter as first argument ✅
- **Cloud Service**: Redis removed, stateless HTTP enabled ✅
- **Production Validation**: Comprehensive testing completed with 100% success ✅

✅ **Content Management Tools Complete (6/6 tools)**
- `write_note`, `read_note`, `delete_note`, `edit_note` ✅
- `view_note`, `read_content` ✅

✅ **Knowledge Graph Navigation Tools Complete (3/3 tools)**
- `build_context`, `recent_activity`, `list_directory` ✅

✅ **Search & Discovery Tools Complete (1/1 tools)**
- `search_notes` ✅

✅ **Visualization Tools Complete (1/1 tools)**
- `canvas` ✅

✅ **Project Management Cleanup Complete**
- Removed `switch_project` and `get_current_project` tools ✅
- Updated `set_default_project` to remove activate parameter ✅

✅ **Comprehensive Testing Complete (157 tests)**
- All test suites updated to use stateless architecture (147 existing tests)
- Single project constraint mode integration tests (10 new tests)
- 100% test pass rate across all tool test files
- Security validations preserved and working
- Error handling comprehensive and user-friendly

✅ **Documentation & Examples Complete**
- All tool docstrings updated with stateless examples
- Project parameter usage clearly documented
- Error handling and security behavior documented

✅ **Enhanced Discovery Mode Complete**
- `recent_activity` tool supports dual-mode operation (discovery vs project-specific)
- ProjectActivitySummary schema provides cross-project insights
- Recent activity prompt updated to support both modes
- Comprehensive project distribution statistics and most active project tracking

✅ **Single Project Constraint Mode Complete**
- `--project` CLI parameter for MCP server constraint
- Environment variable control (`BASIC_MEMORY_MCP_PROJECT`)
- Automatic project override in `get_active_project()` function
- Project management tools disabled in constrained mode with helpful CLI guidance
- Comprehensive integration test suite (10 tests covering all constraint scenarios)

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
- `list_directory(project, dir_name, depth, file_name_glob)`
- `search_notes(project, query, search_type, types, entity_types)`

**Search & Discovery** (use project parameter for specific project or none for discovery):
- `recent_activity(project, timeframe, depth, max_related)`

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
- [x] edit_note: Add required project parameter
- [x] delete_note: Add required project parameter
- [x] view_note: Add required project parameter
- [x] read_content: Add required project parameter

3. Update Knowledge Graph Navigation Tools (3 tools)

- [x] build_context: Add required project parameter
- [x] recent_activity: Make project parameter required
- [x] list_directory: Add required project parameter

4. Update Search & Visualization Tools (2 tools)

- [x] search_notes: Add required project parameter
- [x] canvas: Add required project parameter

5. Update Project Management Tools

- [x] Remove switch_project tool completely
- [x] Remove get_current_project tool completely
- [x] Update set_default_project to remove activate parameter
- [x] Keep list_memory_projects, create_memory_project, delete_project unchanged
    
6. Enhance recent_activity Response

- [x] Add project distribution info showing activity across all projects
- [x] Include project usage stats in response
- [x] Implement ProjectActivitySummary for discovery mode
- [x] Add dual-mode functionality (discovery vs project-specific)

7. Update Tool Documentation

- [x] Update write_note docstring with stateless architecture examples
- [x] Update read_note docstring with project parameter examples
- [x] Update delete_note docstring with comprehensive usage guidance
- [x] Update all remaining tool docstrings with project parameter examples

8. Update Tool Responses

- [x] Add clear project indicator to all tool responses across all tools
- [x] Format: "project: {project_name}" in response metadata
- [x] Add project metadata footer for LLM awareness
- [x] Update all tool responses to include project indicators

9. Comprehensive Testing

- [x] Update all write_note tests to use stateless architecture (34 tests passing)
- [x] Update all edit_note tests to use stateless architecture (17 tests passing)
- [x] Update all view_note tests to use stateless architecture (12 tests passing)
- [x] Update all search_notes tests to use stateless architecture (16 tests passing)
- [x] Update all move_note tests to use stateless architecture (31 tests passing)
- [x] Update all delete_note tests to use stateless architecture
- [x] Verify direct function call compatibility (bypassing MCP layer)
- [x] Test security validation with project parameters
- [x] Validate error handling for non-existent projects
- [x] **Total: 157 tests updated and passing (100% success rate)**
  - [x] **147 existing tests** updated for stateless architecture
  - [x] **10 new tests** for single project constraint mode                                                                   
                                                                                                                             
### Phase 1.5: Default Project Mode Enhancement

#### Problem
While the stateless architecture solves reliability issues, it introduces UX friction for single-project users (estimated 80% of usage) who must specify the project parameter in every tool call.

#### Solution: Default Project Mode
Add optional `default_project_mode` configuration that allows single-project users to have the simplicity of implicit project selection while maintaining the reliability of stateless architecture.

#### Configuration
```json
{
  "default_project": "main",
  "default_project_mode": true  // NEW: Auto-use default_project when not specified
}
```

#### Implementation Details
1. **Config Enhancement** (`src/basic_memory/config.py`)
   - Add `default_project_mode: bool = Field(default=False)`
   - Preserves backward compatibility (defaults to false)

2. **Project Resolution Logic** (`src/basic_memory/mcp/project_context.py`)
   Three-tier resolution hierarchy:
   - Priority 1: CLI `--project` constraint (BASIC_MEMORY_MCP_PROJECT env var)
   - Priority 2: Explicit project parameter in tool call
   - Priority 3: `default_project` if `default_project_mode=true` and no project specified

3. **Assistant Guide Updates** (`src/basic_memory/mcp/resources/ai_assistant_guide.md`)
   - Detect `default_project_mode` at runtime
   - Provide mode-specific instructions to LLMs
   - In default mode: "All operations use project 'main' automatically"
   - In regular mode: Current project discovery guidance

4. **Tool Parameter Handling** (all MCP tools)
   - Make project parameter Optional[str] = None
   - Add resolution logic: `project = project or get_default_project()`
   - Maintain explicit project override capability

#### Usage Modes Summary
- **Regular Mode**: Multi-project users, assistant tracks project per conversation
- **Default Project Mode**: Single-project users, automatic default project
- **Constrained Mode**: CLI --project flag, locked to specific project

#### Testing Requirements
- Integration test for default_project_mode=true with missing parameters
- Test explicit project override in default_project_mode
- Test mode=false requires explicit parameters
- Test CLI constraint overrides default_project_mode

Phase 2: Testing & Validation

8. Update Tests

- [x] Modify all MCP tool tests to pass required project parameter
- [x] Remove tests for deleted tools (switch_project, get_current_project)
- [x] Add tests for project parameter validation
- [x] **Complete: All 147 tests across 5 test files updated and passing**

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

### Phase 2: Cloud Service Simplification (basic-memory-cloud repository) ✅ **COMPLETE**

#### ✅ Remove Session Infrastructure **COMPLETE**
1. ✅ Delete `apps/mcp/src/basic_memory_cloud_mcp/middleware/session_state.py`
2. ✅ Delete `apps/mcp/src/basic_memory_cloud_mcp/middleware/session_logging.py`
3. ✅ Update `apps/mcp/src/basic_memory_cloud_mcp/main.py`:
   ```python
   # Remove session middleware
   # server.add_middleware(SessionStateMiddleware)

   # Enable stateless HTTP
   mcp = FastMCP(name="basic-memory-mcp", stateless_http=True)
   ```

#### ✅ Deployment Simplification **COMPLETE**
1. ✅ Remove Redis from `fly.toml`
2. ✅ Remove Redis environment variables
3. ✅ Update health checks to not depend on Redis
4. ✅ Production deployment verified working with stateless architecture

### Phase 3: Conversational Project Management ✅ **COMPLETE**

#### ✅ Claude Behavior Pattern **VERIFIED WORKING**
1. ✅ **Project Discovery**:
   ```
   Claude: Let me check your recent activity...
   [calls recent_activity() - no project needed for discovery]

   I see you've been working in:
   - research-project (5 operations, 30 min ago)
   - work-notes (2 operations, 2 hours ago)

   Which project should I use for this operation?
   ```

2. ✅ **Context Maintenance**:
   ```
   User: Use research-project
   Claude: Working in research-project.
   [All subsequent operations use project="research-project"]
   ```

3. ✅ **Explicit Project Switching**:
   ```
   User: Check work-notes for that meeting summary
   Claude: Let me search work-notes for the meeting summary.
   [Uses project="work-notes" for specific operation]
   ```

**Validation**: Comprehensive testing confirmed all conversational patterns work naturally with the stateless architecture.

## How to Evaluate

### Success Criteria

#### 1. Functional Completeness
- [x] All MCP tools accept required `project` parameter
- [x] All MCP tools validate project exists before execution
- [x] `switch_project` and `get_current_project` tools removed
- [x] All responses display target project clearly
- [x] No Redis dependencies in deployment (Phase 2: Cloud Service) ✅ **COMPLETE**
- [x] `recent_activity` shows project distribution with ProjectActivitySummary

#### 2. Cross-Client Compatibility Testing ✅ **COMPLETE**
Test identical operations across all clients:
- [x] **Claude Desktop**: All operations work with explicit projects ✅
- [x] **Claude Code**: All operations work with explicit projects ✅
- [x] **Claude Mobile iOS**: All operations work with explicit projects ✅ **CRITICAL SUCCESS**
- [x] **API clients**: All operations work with explicit projects ✅
- [x] **CLI tools**: All operations work with explicit projects ✅

**Critical Achievement**: Claude iOS mobile client session tracking issues completely eliminated through stateless architecture.

#### 3. Session Independence Verification ✅ **COMPLETE**
- [x] Operations work identically with/without session tracking ✅
- [x] No behavioral differences between clients ✅
- [x] Mobile client session ID changes do not affect operations ✅
- [x] Redis can be completely removed without functional impact ✅

**Production Validation**: Redis removed from production deployment with zero functional impact.

#### 4. Performance & Scaling ✅ **COMPLETE**
- [x] `stateless_http=True` enabled successfully ✅
- [x] No Redis memory usage ✅
- [x] Horizontal scaling possible (multiple MCP instances) ✅
- [x] Response times unchanged or improved ✅

#### 5. User Experience Testing
**Project Discovery Flow**:
- [x] `recent_activity()` provides useful project context
- [x] Claude can intelligently suggest projects based on activity
- [x] Project switching is explicit and clear in conversation

**Error Handling**:
- [x] Clear error messages for non-existent projects
- [x] Helpful suggestions when project parameter missing
- [x] No silent failures or wrong-project operations

**Response Clarity**:
- [x] Every operation clearly shows target project
- [x] Users always know which project is being operated on
- [x] No confusion about "current project" state

#### 6. Migration Safety ✅ **COMPLETE**
- [x] Backward compatibility period with optional project parameter ✅
- [x] Clear migration documentation for existing users ✅
- [x] Data integrity maintained during transition ✅
- [x] No data loss during migration ✅

**Production Migration**: Successfully deployed to production with zero data loss and maintained system integrity.

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
