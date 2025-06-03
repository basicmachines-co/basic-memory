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
- ✅ **Complete Project Management System** - Project switching and project-specific operations
- ✅ **Advanced Note Editing** - Incremental editing with append, prepend, find/replace, and section operations  
- ✅ **File Management System** - Full move operations with database consistency and rollback protection
- ✅ **Enhanced Search Capabilities** - Frontmatter tags now searchable, improved content discoverability
- ✅ **Unified Database Architecture** - Single app-level database for better performance and project management

## Major Features

### 1. Multiple Project Management 🎯

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
- **Project-Specific Operations**: Operations work within the currently active project context
- **Project Discovery**: List all available projects with status indicators
- **Session Context**: Maintains active project throughout conversation
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
- **Project Context**: Works within the active project with session awareness

### 3. Smart File Management 📁

**Move and organize notes:**

```python
# Simple moves with automatic folder creation
move_note("my-note", "work/projects/my-note.md")

# Organize within the active project
move_note("shared-doc", "archive/old-docs/shared-doc.md")

# Rename operations
move_note("old-name", "same-folder/new-name.md")
```

**Key Capabilities:**
- **Database Consistency**: Updates file paths, permalinks, and checksums automatically
- **Search Reindexing**: Maintains search functionality after moves
- **Folder Creation**: Automatically creates destination directories
- **Project Isolation**: Operates within the currently active project
- **Link Preservation**: Maintains internal links and references

### 4. Enhanced Search & Discovery 🔍

**Find content more easily with improved search capabilities:**

- **Frontmatter Tag Search**: Tags from YAML frontmatter are now indexed and searchable
- **Improved Content Discovery**: Search across titles, content, tags, and metadata
- **Project-Scoped Search**: Search within the currently active project
- **Better Search Quality**: Enhanced FTS5 indexing with tag content inclusion

**Example:**
```yaml
---
title: Coffee Brewing Methods
tags: [coffee, brewing, equipment]
---
```
Now searchable by: "coffee", "brewing", "equipment", or "Coffee Brewing Methods"

### 5. Unified Database Architecture 🗄️

**Single app-level database for better performance and project management:**

- **Migration from Per-Project DBs**: Moved from multiple SQLite files to single app database
- **Project Isolation**: Proper data separation with project_id foreign keys
- **Better Performance**: Optimized queries and reduced file I/O

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
- **Session context awareness** (operates within the currently active project)
- **Enhanced error messages** with project context metadata
- **Improved response formatting** with project information footers
- **Project isolation** ensures operations stay within the correct project boundaries


## User Experience Improvements

### Installation Options

**Multiple ways to install and test Basic Memory:**

```bash
# Stable release
uv tool install basic-memory

# Beta/pre-releases
uv tool install basic-memory --pre
```


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
- Configuration: Projects defined in `~/.basic-memory/config.json` are synced with database

**What Stays the Same:**
- All existing notes and data remain unchanged
- Default project behavior maintained for single-project users
- All existing MCP tools continue to work without modification




## Documentation & Resources

### New Documentation
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


### Getting Updates
```bash
# Stable releases
uv tool upgrade basic-memory

# Beta releases  
uv tool install basic-memory --pre --force-reinstall

# Latest development
uv tool install basic-memory --pre --force-reinstall
```