Current vs. Desired State

Current: Project context is fixed at startup → Restart required to switch
Desired: Fluid project switching during conversation → "Switch to my work-notes project"

## UX Scenarios to Consider

### Scenario 1: Project Discovery & Switching

User: "What projects do I have?"
Assistant: [calls list_projects()]
• personal-notes (active) 
• work-project
• code-snippets

User: "Switch to work-project"
Assistant: [calls switch_project("work-project")]
✓ Switched to work-project

User: "What did I work on yesterday?"
Assistant: [calls recent_activity() in work-project context]

### Scenario 2: Cross-Project Operations

User: "Create a note about this meeting in my personal-notes project"
Assistant: [calls write_note(..., project="personal-notes")]

User: "Now search for 'API design' across all my projects"
Assistant: [calls search_across_projects("API design")]

### Scenario 3: Context Awareness

User: "Edit my todo list"
Assistant: [calls read_note("todo-list")]
📍 Note from work-project: "Todo List"
• Finish API documentation
• Review pull requests

## Design Options

### Option A: Session-Based Context

# New MCP tools for project management
switch_project("work-project")     # Sets session context
list_projects()                    # Shows available projects  
get_current_project()             # Shows active project

# Existing tools use session context
edit_note("my-note", "append", "content")  # Uses work-project

### Option B: Explicit Project Parameters

# Add optional project param to all tools
edit_note("my-note", "append", "content", project="personal-notes")
search_notes("query", project="work-project")

# If no project specified, use session default
edit_note("my-note", "append", "content")  # Uses current context

### Option C: Hybrid (Most Flexible)

# Set default context
switch_project("work-project")

# Use context by default
edit_note("my-note", "append", "content")

# Override when needed
search_notes("query", project="personal-notes")

Technical Implementation Ideas

Session State Management

# Simple in-memory session store
SESSION_STORE = {
  "session_123": {
      "current_project": "work-project",
      "default_project": "personal-notes"
  }
}

## New MCP Tools

@tool
async def list_projects() -> str:
  """List all available projects."""

@tool  
async def switch_project(project_name: str) -> str:
  """Switch to a different project context."""

@tool
async def get_current_project() -> str:
  """Show the currently active project."""

@tool
async def search_across_projects(query: str) -> str:
  """Search across all projects."""

@tool
async def set_default_project(project-name: str) -> str:
  """Set default project. Requires restart"""

## Enhanced Existing Tools

@tool
async def edit_note(
  identifier: str,
  operation: str, 
  content: str,
  project: Optional[str] = None  # New optional parameter
) -> str:
  # If project not specified, use session context
  project_id = project or get_session_project()

## UX Questions to Consider

1. Context Visibility: Should every tool response show which project it's operating on?

- we could add a footer or something to the tool result that the LLM could understand is just metadata, not to display to the user 

2. Error Handling: What happens when you reference a non-existent project?

- we would need to validate the project as an input and show an error

3. Default Behavior: Should there be a "global search" that works across all projects?

- i'm thinking this is a "not now" thing

4. State Persistence: Should project context persist across MCP reconnections?

- I think we always startup with the "default" project. If the user wants to change it, they can update the config, or call the new tool. 

5. Conversation Flow: How do we make project switching feel natural in conversation?

  What's your vision for the ideal user experience? Should it feel more like:
- A file system: "cd into work-project, then edit my notes"
- A workspace switcher: "Switch to work mode" vs "Switch to personal mode"
- Context tags: "In work-project, show me recent activity"

Something like "lets switch to project X", LLM responds "ok we are working in project X, and shows project summary"

# Implementation Plan - Client-Side Project Management

## Overview
Implement ad-hoc project switching as an MCP-only feature. No API changes needed - just session state management on the MCP side with enhanced tools.

## Core Components

### 1. Session State Management
```python
# src/basic_memory/mcp/project_session.py
class ProjectSession:
    """Simple in-memory project context for MCP session."""
    _current_project: Optional[str] = None
    _default_project: Optional[str] = None
    
    @classmethod
    def initialize(cls, default_project: str):
        """Set the default project from config on startup."""
        cls._default_project = default_project
        cls._current_project = default_project
    
    @classmethod 
    def get_current_project(cls) -> str:
        return cls._current_project or cls._default_project or "main"
    
    @classmethod
    def set_current_project(cls, project_name: str):
        cls._current_project = project_name
    
    @classmethod
    def get_default_project(cls) -> str:
        return cls._default_project or "main"
```

### 2. New MCP Tools
File: `src/basic_memory/mcp/tools/project_management.py`

```python
@tool
async def list_projects() -> str:
    """List all available projects with their status."""

@tool  
async def switch_project(project_name: str) -> str:
    """Switch to a different project context. Shows project summary after switching."""

@tool
async def get_current_project() -> str:
    """Show the currently active project and basic stats."""

@tool
async def set_default_project(project_name: str) -> str:
    """Set default project in config. Requires restart to take effect."""
```

### 3. Enhanced Existing Tools
Add optional `project` parameter to all existing tools:
- `edit_note(..., project: Optional[str] = None)`
- `write_note(..., project: Optional[str] = None)`
- `read_note(..., project: Optional[str] = None)`
- `search_notes(..., project: Optional[str] = None)`
- `recent_activity(..., project: Optional[str] = None)`

### 4. Tool Response Metadata
Add project context footer to all tool responses:
```python
def add_project_metadata(result: str, project_name: str) -> str:
    """Add project context as metadata footer."""
    return f"{result}\n\n<!-- Project: {project_name} -->"
```

## Implementation Tasks

### Phase 1: Core Infrastructure ✅
- [x] Create `ProjectSession` class
- [x] Create `project_management.py` tools file
- [x] Initialize session state in MCP server startup
- [x] Add project validation utilities

### Phase 2: New Tools Implementation ✅
- [x] Implement `list_projects()` 
- [x] Implement `switch_project()`
- [x] Implement `get_current_project()`
- [x] Implement `set_default_project()`

### Phase 3: Enhance Existing Tools ✅
- [x] Add `project` parameter to all existing tools
- [x] Update tools to use session context when project not specified
- [x] Add project metadata to tool responses
- [x] Update tool documentation

### Phase 4: Testing & Polish ✅
- [x] Add comprehensive tests for project management tools
- [x] Test cross-project operations
- [x] Test error handling for invalid projects
- [x] Update documentation and examples
- [x] All tests passing (146/146 MCP, 16/16 CLI)
- [x] 100% test coverage achieved

### Phase 5: v0.13.0 Additional Features
- [x] Implement `edit_note()` MCP tool (append/prepend operations)
- [ ] Add `move_note()` functionality
- [ ] Implement agent mode capabilities
- [ ] Update release notes

### Later
- [ ] Add prompt agent functionality

## Expected UX Flow

```
User: "What projects do I have?"
Assistant: [calls list_projects()]

Available projects:
• main (current, default)
• work-notes  
• personal-journal
• code-snippets

---

User: "Switch to work-notes"
Assistant: [calls switch_project("work-notes")]

✓ Switched to work-notes project

Project Summary:
• 47 notes
• Last updated: 2 hours ago  
• Recent activity: 3 notes modified today

---

User: "What did I work on yesterday?"
Assistant: [calls recent_activity() - uses work-notes context]

Recent activity in work-notes:
• Updated "API Design Notes" 
• Created "Meeting with Team Lead"
• Modified "Project Timeline"

---

User: "Edit my todo list" 
Assistant: [calls edit_note("todo-list", ...) - uses work-notes context]

Edited note (append) in work-notes:
• file_path: Todo List.md
• Added 2 lines to end of note
```

## Technical Details

### Error Handling
- Validate project names against available projects
- Show helpful error messages for non-existent projects
- Graceful fallback to default project on errors

### Context Visibility  
- Add `<!-- Project: project-name -->` footer to all tool responses
- LLM can use this metadata but doesn't need to show to user
- Clear indication in tool responses which project is active

### State Management
- Session state resets to default project on MCP restart
- No persistence across reconnections (keeps it simple)
- Config changes require restart (matches current behavior)