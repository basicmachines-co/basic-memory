# CHANGELOG

## v0.13.6 (2025-06-18)

### Bug Fixes

- **Custom Entity Types** - Support for custom entity types in write_note
  ([`7789864`](https://github.com/basicmachines-co/basic-memory/commit/77898644933589c2da9bdd60571d54137a5309ed))
  - Fixed `entity_type` parameter for `write_note` MCP tool to respect value passed in
  - Frontmatter `type` field automatically respected when no explicit parameter provided
  - Maintains backward compatibility with default "note" type

- **#139**: Fix "UNIQUE constraint failed: entity.permalink" database error
  ([`c6215fd`](https://github.com/basicmachines-co/basic-memory/commit/c6215fd819f9564ead91cf3a950f855241446096))
  - Implement SQLAlchemy UPSERT strategy to handle permalink conflicts gracefully
  - Eliminates crashes when creating notes with existing titles in same folders
  - Seamlessly updates existing entities instead of failing with constraint errors

- **Database Migration Performance** - Eliminate redundant migration initialization
  ([`84d2aaf`](https://github.com/basicmachines-co/basic-memory/commit/84d2aaf6414dd083af4b0df73f6c8139b63468f6))
  - Fix duplicate migration calls that slowed system startup
  - Improve performance with multiple projects (tested with 28+ projects)
  - Add migration deduplication safeguards with comprehensive test coverage

- **User Experience** - Correct spelling error in continue_conversation prompt
  ([`b4c26a6`](https://github.com/basicmachines-co/basic-memory/commit/b4c26a613379e6f2ba655efe3d7d8d40c27999e5))
  - Fix "Chose a folder" → "Choose a folder" in MCP prompt instructions
  - Improve grammar and clarity in user-facing prompt text

### Documentation

- **Website Updates** - Add new website and community links to README
  ([`3fdce68`](https://github.com/basicmachines-co/basic-memory/commit/3fdce683d7ad8b6f4855d7138d5ff2136d4c07bc))

- **Project Documentation** - Update README.md and CLAUDE.md with latest project information
  ([`782cb2d`](https://github.com/basicmachines-co/basic-memory/commit/782cb2df28803482d209135a054e67cc32d7363e))

### Technical Improvements

- **Comprehensive Test Coverage** - Add extensive test suites for new features
  - Custom entity type validation with 8 new test scenarios
  - UPSERT behavior testing with edge case coverage
  - Migration deduplication testing with 6 test scenarios
  - Database constraint handling validation

- **Code Quality** - Enhanced error handling and validation
  - Improved SQLAlchemy patterns with modern UPSERT operations
  - Better conflict resolution strategies for entity management
  - Strengthened database consistency guarantees

### Performance

- **Database Operations** - Faster startup and improved scalability
  - Reduced migration overhead for multi-project setups
  - Optimized conflict resolution for entity creation
  - Enhanced performance with growing knowledge bases

### Migration Guide

This release includes automatic database improvements. No manual migration required:

- Existing notes and entity types continue working unchanged
- New `entity_type` parameter is optional and backward compatible
- Database performance improvements apply automatically
- All existing MCP tool behavior preserved

### Installation

```bash
# Latest stable release
uv tool install basic-memory

# Update existing installation
uv tool upgrade basic-memory
```

## v0.13.5 (2025-06-11)

### Bug Fixes

- **MCP Tools**: Renamed `create_project` tool to `create_memory_project` for namespace isolation
- **Namespace**: Continued namespace isolation effort to prevent conflicts with other MCP servers

### Changes

- Tool functionality remains identical - only the name changed from `create_project` to `create_memory_project`
- All integration tests updated to use the new tool name
- Completes namespace isolation for project management tools alongside `list_memory_projects`

## v0.13.4 (2025-06-11)

### Bug Fixes

- **MCP Tools**: Renamed `list_projects` tool to `list_memory_projects` to avoid naming conflicts with other MCP servers
- **Namespace**: Improved tool naming specificity for better MCP server integration and isolation

### Changes

- Tool functionality remains identical - only the name changed from `list_projects` to `list_memory_projects`
- All integration tests updated to use the new tool name
- Better namespace isolation for Basic Memory MCP tools

## v0.13.3 (2025-06-11)

### Bug Fixes

- **Projects**: Fixed case-insensitive project switching where switching succeeded but subsequent operations failed due to session state inconsistency
- **Config**: Enhanced config manager with case-insensitive project lookup using permalink-based matching
- **MCP Tools**: Updated project management tools to store canonical project names from database instead of user input
- **API**: Improved project service to handle both name and permalink lookups consistently

### Technical Improvements

- Added comprehensive case-insensitive project switching test coverage with 5 new integration test scenarios
- Fixed permalink generation inconsistencies where different case inputs could generate different permalinks
- Enhanced project URL construction to use permalinks consistently across all API calls
- Improved error handling and session state management for project operations

### Changes

- Project switching now preserves canonical project names from database in session state
- All project operations use permalink-based lookups for case-insensitive matching
- Enhanced test coverage ensures reliable case-insensitive project operations

## v0.13.2 (2025-06-11)

### Features

- **Release Management**: Added automated release management system with version control in `__init__.py`
- **Automation**: Implemented justfile targets for `release` and `beta` commands with comprehensive quality gates
- **CI/CD**: Enhanced release process with automatic version updates, git tagging, and GitHub release creation

### Development Experience

- Added `.claude/commands/release/` directory with automation documentation
- Implemented release validation including lint, type-check, and test execution
- Streamlined release workflow from manual process to single-command automation

### Technical Improvements

- Updated package version management to use actual version numbers instead of dynamic versioning
- Added release process documentation and command references
- Enhanced justfile with comprehensive release automation targets

## v0.13.1 (2025-06-11)

### Bug Fixes

- **CLI**: Fixed  `basic-memory project` project management commands that were  not working in v0.13.0 (#129)
- **Projects**: Resolved case sensitivity issues when switching between projects that caused "Project not found" errors (#127)
- **API**: Standardized CLI project command endpoints and improved error handling
- **Core**: Implemented consistent project name handling using permalinks to avoid case-related conflicts

### Changes

- Renamed `basic-memory project sync` command to `basic-memory project sync-config` for clarity
- Improved project switching reliability across different case variations
- Removed redundant server status messages from CLI error outputs

## v0.13.0 (2025-06-11)

### Overview

Basic Memory v0.13.0 is a **major release** that transforms Basic Memory into a true multi-project knowledge management system. This release introduces fluid project switching, advanced note editing capabilities, robust file management, and production-ready OAuth authentication - all while maintaining full backward compatibility.

**What's New for Users:**
- 🎯 **Switch between projects instantly** during conversations with Claude
- ✏️ **Edit notes incrementally** without rewriting entire documents
- 📁 **Move and organize notes** with full database consistency
- 📖 **View notes as formatted artifacts** for better readability in Claude Desktop
- 🔍 **Search frontmatter tags** to discover content more easily
- 🔐 **OAuth authentication** for secure remote access
- ⚡ **Development builds** automatically published for beta testing

**Key v0.13.0 Accomplishments:**
- ✅ **Complete Project Management System** - Project switching and project-specific operations
- ✅ **Advanced Note Editing** - Incremental editing with append, prepend, find/replace, and section operations  
- ✅ **View Notes as Artifacts in Claude Desktop/Web** - Use the view_note tool to view a note as an artifact
- ✅ **File Management System** - Full move operations with database consistency and rollback protection
- ✅ **Enhanced Search Capabilities** - Frontmatter tags now searchable, improved content discoverability
- ✅ **Unified Database Architecture** - Single app-level database for better performance and project management

### Major Features

#### 1. Multiple Project Management 

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

#### 2. Advanced Note Editing 

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

#### 3. Smart File Management

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

#### 4. Enhanced Search & Discovery 

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

#### 5. Unified Database Architecture 

**Single app-level database for better performance and project management:**

- **Migration from Per-Project DBs**: Moved from multiple SQLite files to single app database
- **Project Isolation**: Proper data separation with project_id foreign keys
- **Better Performance**: Optimized queries and reduced file I/O

### Complete MCP Tool Suite

#### New Project Management Tools
- **`list_projects()`** - Discover and list all available projects with status
- **`switch_project(project_name)`** - Change active project context during conversations
- **`get_current_project()`** - Show currently active project with statistics
- **`set_default_project(project_name)`** - Update default project configuration
- **`sync_status()`** - Check file synchronization status and background operations

#### New Note Operations Tools
- **`edit_note()`** - Incremental note editing (append, prepend, find/replace, section replace)
- **`move_note()`** - Move notes with database consistency and search reindexing
- **`view_note()`** - Display notes as formatted artifacts for better readability in Claude Desktop

#### Enhanced Existing Tools
All existing tools now support:
- **Session context awareness** (operates within the currently active project)
- **Enhanced error messages** with project context metadata
- **Improved response formatting** with project information footers
- **Project isolation** ensures operations stay within the correct project boundaries


### User Experience Improvements

#### Installation Options

**Multiple ways to install and test Basic Memory:**

```bash
# Stable release
uv tool install basic-memory

# Beta/pre-releases
uv tool install basic-memory --pre
```


#### Bug Fixes & Quality Improvements

**Major issues resolved in v0.13.0:**

- **#118**: Fixed YAML tag formatting to follow standard specification
- **#110**: Fixed `--project` flag consistency across all CLI commands
- **#107**: Fixed write_note update failures with existing notes
- **#93**: Fixed custom permalink handling in frontmatter
- **#52**: Enhanced search capabilities with frontmatter tag indexing
- **FTS5 Search**: Fixed special character handling in search queries
- **Error Handling**: Improved error messages and validation across all tools

### Breaking Changes & Migration

#### For Existing Users

**Automatic Migration**: First run will automatically migrate existing data to the new unified database structure. No manual action required.

**What Changes:**
- Database location: Moved to `~/.basic-memory/memory.db` (unified across projects)
- Configuration: Projects defined in `~/.basic-memory/config.json` are synced with database

**What Stays the Same:**
- All existing notes and data remain unchanged
- Default project behavior maintained for single-project users
- All existing MCP tools continue to work without modification

### Documentation & Resources

#### New Documentation
- [Project Management Guide](docs/Project%20Management.md) - Multi-project workflows
- [Note Editing Guide](docs/Note%20Editing.md) - Advanced editing techniques

#### Updated Documentation
- [README.md](README.md) - Installation options and beta build instructions
- [CONTRIBUTING.md](CONTRIBUTING.md) - Release process and version management
- [CLAUDE.md](CLAUDE.md) - Development workflow and CI/CD documentation
- [Claude.ai Integration](docs/Claude.ai%20Integration.md) - Updated MCP tool examples

#### Quick Start Examples

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



## v0.12.3 (2025-04-17)

### Bug Fixes

- Add extra logic for permalink generation with mixed Latin unicode and Chinese characters
  ([`73ea91f`](https://github.com/basicmachines-co/basic-memory/commit/73ea91fe0d1f7ab89b99a1b691d59fe608b7fcbb))

Signed-off-by: phernandez <paul@basicmachines.co>

- Modify recent_activity args to be strings instead of enums
  ([`3c1cc34`](https://github.com/basicmachines-co/basic-memory/commit/3c1cc346df519e703fae6412d43a92c7232c6226))

Signed-off-by: phernandez <paul@basicmachines.co>


## v0.12.2 (2025-04-08)

### Bug Fixes

- Utf8 for all file reads/write/open instead of default platform encoding
  ([#91](https://github.com/basicmachines-co/basic-memory/pull/91),
  [`2934176`](https://github.com/basicmachines-co/basic-memory/commit/29341763318408ea8f1e954a41046c4185f836c6))

Signed-off-by: phernandez <paul@basicmachines.co>


## v0.12.1 (2025-04-07)

### Bug Fixes

- Run migrations and sync when starting mcp
  ([#88](https://github.com/basicmachines-co/basic-memory/pull/88),
  [`78a3412`](https://github.com/basicmachines-co/basic-memory/commit/78a3412bcff83b46e78e26f8b9fce42ed9e05991))


## v0.12.0 (2025-04-06)

### Bug Fixes

- [bug] `#` character accumulation in markdown frontmatter tags prop
  ([#79](https://github.com/basicmachines-co/basic-memory/pull/79),
  [`6c19c9e`](https://github.com/basicmachines-co/basic-memory/commit/6c19c9edf5131054ba201a109b37f15c83ef150c))

- [bug] Cursor has errors calling search tool
  ([#78](https://github.com/basicmachines-co/basic-memory/pull/78),
  [`9d581ce`](https://github.com/basicmachines-co/basic-memory/commit/9d581cee133f9dde4a0a85118868227390c84161))

- [bug] Some notes never exit "modified" status
  ([#77](https://github.com/basicmachines-co/basic-memory/pull/77),
  [`7930ddb`](https://github.com/basicmachines-co/basic-memory/commit/7930ddb2919057be30ceac8c4c19da6aaa1d3e92))

- [bug] write_note Tool Fails to Update Existing Files in Some Situations.
  ([#80](https://github.com/basicmachines-co/basic-memory/pull/80),
  [`9bff1f7`](https://github.com/basicmachines-co/basic-memory/commit/9bff1f732e71bc60f88b5c2ce3db5a2aa60b8e28))

- Set default mcp log level to ERROR
  ([#81](https://github.com/basicmachines-co/basic-memory/pull/81),
  [`248214c`](https://github.com/basicmachines-co/basic-memory/commit/248214cb114a269ca60ff6398e382f9e2495ad8e))

- Write_note preserves frontmatter fields in content
  ([#84](https://github.com/basicmachines-co/basic-memory/pull/84),
  [`3f4d9e4`](https://github.com/basicmachines-co/basic-memory/commit/3f4d9e4d872ebc0ed719c61b24d803c14a9db5e6))

### Documentation

- Add VS Code instructions to README
  ([#76](https://github.com/basicmachines-co/basic-memory/pull/76),
  [`43cbb7b`](https://github.com/basicmachines-co/basic-memory/commit/43cbb7b38cc0482ac0a41b6759320e3588186e43))

- Updated basicmachines.co links to be https
  ([#69](https://github.com/basicmachines-co/basic-memory/pull/69),
  [`40ea28b`](https://github.com/basicmachines-co/basic-memory/commit/40ea28b0bfc60012924a69ecb76511daa4c7d133))

### Features

- Add watch to mcp process ([#83](https://github.com/basicmachines-co/basic-memory/pull/83),
  [`00c8633`](https://github.com/basicmachines-co/basic-memory/commit/00c8633cfcee75ff640ff8fe81dafeb956281a94))

- Permalink enhancements ([#82](https://github.com/basicmachines-co/basic-memory/pull/82),
  [`617e60b`](https://github.com/basicmachines-co/basic-memory/commit/617e60bda4a590678a5f551f10a73e7b47e3b13e))

- Avoiding "useless permalink values" for files without metadata - Enable permalinks to be updated
  on move via config setting


## v0.11.0 (2025-03-29)

### Bug Fixes

- Just delete db for reset db instead of using migrations.
  ([#65](https://github.com/basicmachines-co/basic-memory/pull/65),
  [`0743ade`](https://github.com/basicmachines-co/basic-memory/commit/0743ade5fc07440f95ecfd816ba7e4cfd74bca12))

Signed-off-by: phernandez <paul@basicmachines.co>

- Make logs for each process - mcp, sync, cli
  ([#64](https://github.com/basicmachines-co/basic-memory/pull/64),
  [`f1c9570`](https://github.com/basicmachines-co/basic-memory/commit/f1c95709cbffb1b88292547b0b8f29fcca22d186))

Signed-off-by: phernandez <paul@basicmachines.co>

### Documentation

- Update broken "Multiple Projects" link in README.md
  ([#55](https://github.com/basicmachines-co/basic-memory/pull/55),
  [`3c68b7d`](https://github.com/basicmachines-co/basic-memory/commit/3c68b7d5dd689322205c67637dca7d188111ee6b))

### Features

- Add bm command alias for basic-memory
  ([#67](https://github.com/basicmachines-co/basic-memory/pull/67),
  [`069c0a2`](https://github.com/basicmachines-co/basic-memory/commit/069c0a21c630784e1bf47d2b7de5d6d1f6fadd7a))

Signed-off-by: phernandez <paul@basicmachines.co>

- Rename search tool to search_notes
  ([#66](https://github.com/basicmachines-co/basic-memory/pull/66),
  [`b278276`](https://github.com/basicmachines-co/basic-memory/commit/b27827671dc010be3e261b8b221aca6b7f836661))

Signed-off-by: phernandez <paul@basicmachines.co>


## v0.10.1 (2025-03-25)

### Bug Fixes

- Make set_default_project also activate project for current session to fix #37
  ([`cbe72be`](https://github.com/basicmachines-co/basic-memory/commit/cbe72be10a646c0b03931bb39aff9285feae47f9))

This change makes the 'basic-memory project default <name>' command both: 1. Set the default project
  for future invocations (persistent change) 2. Activate the project for the current session
  (immediate change)

Added tests to verify this behavior, which resolves issue #37 where the project name and path
  weren't changing properly when the default project was changed.

🤖 Generated with [Claude Code](https://claude.ai/code)

Co-Authored-By: Claude <noreply@anthropic.com>

- Make set_default_project also activate project for current session to fix #37
  ([`46c4fd2`](https://github.com/basicmachines-co/basic-memory/commit/46c4fd21645b109af59eb2a0201c7bd849b34a49))

This change makes the 'basic-memory project default <name>' command both: 1. Set the default project
  for future invocations (persistent change) 2. Activate the project for the current session
  (immediate change)

Added tests to verify this behavior, which resolves issue #37 where the project name and path
  weren't changing properly when the default project was changed.

🤖 Generated with [Claude Code](https://claude.ai/code)

Co-Authored-By: Claude <noreply@anthropic.com>

Signed-off-by: phernandez <paul@basicmachines.co>

- Move ai_assistant_guide.md into package resources to fix #39
  ([`390ff9d`](https://github.com/basicmachines-co/basic-memory/commit/390ff9d31ccee85bef732e8140b5eeecd7ee176f))

This change relocates the AI assistant guide from the static directory into the package resources
  directory, ensuring it gets properly included in the distribution package and is accessible when
  installed via pip/uv.

🤖 Generated with [Claude Code](https://claude.ai/code)

Co-Authored-By: Claude <noreply@anthropic.com>

- Move ai_assistant_guide.md into package resources to fix #39
  ([`cc2cae7`](https://github.com/basicmachines-co/basic-memory/commit/cc2cae72c14b380f78ffeb67c2261e4dbee45faf))

This change relocates the AI assistant guide from the static directory into the package resources
  directory, ensuring it gets properly included in the distribution package and is accessible when
  installed via pip/uv.

🤖 Generated with [Claude Code](https://claude.ai/code)

Co-Authored-By: Claude <noreply@anthropic.com>

Signed-off-by: phernandez <paul@basicmachines.co>

- Preserve custom frontmatter fields when updating notes
  ([`78f234b`](https://github.com/basicmachines-co/basic-memory/commit/78f234b1806b578a0a833e8ee4184015b7369a97))

Fixes #36 by modifying entity_service.update_entity() to read existing frontmatter from files before
  updating them. Custom metadata fields such as Status, Priority, and Version are now preserved when
  notes are updated through the write_note MCP tool.

Added test case that verifies this behavior by creating a note with custom frontmatter and then
  updating it.

🤖 Generated with [Claude Code](https://claude.ai/code)

Co-Authored-By: Claude <noreply@anthropic.com>

- Preserve custom frontmatter fields when updating notes
  ([`e716946`](https://github.com/basicmachines-co/basic-memory/commit/e716946b4408d017eca4be720956d5a210b4e6b1))

Fixes #36 by modifying entity_service.update_entity() to read existing frontmatter from files before
  updating them. Custom metadata fields such as Status, Priority, and Version are now preserved when
  notes are updated through the write_note MCP tool.

Added test case that verifies this behavior by creating a note with custom frontmatter and then
  updating it.

🤖 Generated with [Claude Code](https://claude.ai/code)

Co-Authored-By: Claude <noreply@anthropic.com>

Signed-off-by: phernandez <paul@basicmachines.co>

### Chores

- Remove duplicate code in entity_service.py from bad merge
  ([`681af5d`](https://github.com/basicmachines-co/basic-memory/commit/681af5d4505dadc40b4086630f739d76bac9201d))

Signed-off-by: phernandez <paul@basicmachines.co>

### Documentation

- Add help docs to mcp cli tools
  ([`731b502`](https://github.com/basicmachines-co/basic-memory/commit/731b502d36cec253d114403d73b48fab3c47786e))

Signed-off-by: phernandez <paul@basicmachines.co>

- Add mcp badge, update cli reference, llms-install.md
  ([`b26afa9`](https://github.com/basicmachines-co/basic-memory/commit/b26afa927f98021246cd8b64858e57333595ea90))

Signed-off-by: phernandez <paul@basicmachines.co>

- Update CLAUDE.md ([#33](https://github.com/basicmachines-co/basic-memory/pull/33),
  [`dfaf0fe`](https://github.com/basicmachines-co/basic-memory/commit/dfaf0fea9cf5b97d169d51a6276ec70162c21a7e))

fix spelling in CLAUDE.md: enviroment -> environment Signed-off-by: Ikko Eltociear Ashimine
  <eltociear@gmail.com>

### Refactoring

- Move project stats into projct subcommand
  ([`2a881b1`](https://github.com/basicmachines-co/basic-memory/commit/2a881b1425c73947f037fbe7ac5539c015b62526))

Signed-off-by: phernandez <paul@basicmachines.co>


## v0.10.0 (2025-03-15)

### Bug Fixes

- Ai_resource_guide.md path
  ([`da97353`](https://github.com/basicmachines-co/basic-memory/commit/da97353cfc3acc1ceb0eca22ac6af326f77dc199))

Signed-off-by: phernandez <paul@basicmachines.co>

- Ai_resource_guide.md path
  ([`c4732a4`](https://github.com/basicmachines-co/basic-memory/commit/c4732a47b37dd2e404139fb283b65556c81ce7c9))

- Ai_resource_guide.md path
  ([`2e9d673`](https://github.com/basicmachines-co/basic-memory/commit/2e9d673e54ad6a63a971db64f01fc2f4e59c2e69))

Signed-off-by: phernandez <paul@basicmachines.co>

- Don't sync *.tmp files on watch ([#31](https://github.com/basicmachines-co/basic-memory/pull/31),
  [`6b110b2`](https://github.com/basicmachines-co/basic-memory/commit/6b110b28dd8ba705ebfc0bcb41faf2cb993da2c3))

Fixes #30

Signed-off-by: phernandez <paul@basicmachines.co>

- Drop search_index table on db reindex
  ([`31cca6f`](https://github.com/basicmachines-co/basic-memory/commit/31cca6f913849a0ab8fc944803533e3072e9ef88))

Signed-off-by: phernandez <paul@basicmachines.co>

- Improve utf-8 support for file reading/writing
  ([#32](https://github.com/basicmachines-co/basic-memory/pull/32),
  [`eb5e4ec`](https://github.com/basicmachines-co/basic-memory/commit/eb5e4ec6bd4d2fe757087be030d867f4ca1d38ba))

fixes #29

Signed-off-by: phernandez <paul@basicmachines.co>

### Chores

- Remove logfire
  ([`9bb8a02`](https://github.com/basicmachines-co/basic-memory/commit/9bb8a020c3425a02cb3a88f6f02adcd281bccee2))

Signed-off-by: phernandez <paul@basicmachines.co>

### Documentation

- Add glama badge. Fix typos in README.md
  ([#28](https://github.com/basicmachines-co/basic-memory/pull/28),
  [`9af913d`](https://github.com/basicmachines-co/basic-memory/commit/9af913da4fba7bb4908caa3f15f2db2aa03777ec))

Signed-off-by: phernandez <paul@basicmachines.co>

- Update CLAUDE.md with GitHub integration capabilities
  ([#25](https://github.com/basicmachines-co/basic-memory/pull/25),
  [`fea2f40`](https://github.com/basicmachines-co/basic-memory/commit/fea2f40d1b54d0c533e6d7ee7ce1aa7b83ad9a47))

This PR updates the CLAUDE.md file to document the GitHub integration capabilities that enable
  Claude to participate directly in the development workflow.

### Features

- Add Smithery integration for easier installation
  ([#24](https://github.com/basicmachines-co/basic-memory/pull/24),
  [`eb1e7b6`](https://github.com/basicmachines-co/basic-memory/commit/eb1e7b6088b0b3dead9c104ee44174b2baebf417))

This PR adds support for deploying Basic Memory on the Smithery platform.

Signed-off-by: bm-claudeai <claude@basicmachines.co>


## v0.9.0 (2025-03-07)

### Chores

- Pre beta prep ([#20](https://github.com/basicmachines-co/basic-memory/pull/20),
  [`6a4bd54`](https://github.com/basicmachines-co/basic-memory/commit/6a4bd546466a45107007b5000276b6c9bb62ef27))

fix: drop search_index table on db reindex

fix: ai_resource_guide.md path

chore: remove logfire

Signed-off-by: phernandez <paul@basicmachines.co>

### Documentation

- Update README.md and CLAUDE.md
  ([`182ec78`](https://github.com/basicmachines-co/basic-memory/commit/182ec7835567fc246798d9b4ad121b2f85bc6ade))

### Features

- Add project_info tool ([#19](https://github.com/basicmachines-co/basic-memory/pull/19),
  [`d2bd75a`](https://github.com/basicmachines-co/basic-memory/commit/d2bd75a949cc4323cb376ac2f6cb39f47c78c428))

Signed-off-by: phernandez <paul@basicmachines.co>

- Beta work ([#17](https://github.com/basicmachines-co/basic-memory/pull/17),
  [`e6496df`](https://github.com/basicmachines-co/basic-memory/commit/e6496df595f3cafde6cc836384ee8c60886057a5))

feat: Add multiple projects support

feat: enhanced read_note for when initial result is not found

fix: merge frontmatter when updating note

fix: handle directory removed on sync watch

- Implement boolean search ([#18](https://github.com/basicmachines-co/basic-memory/pull/18),
  [`90d5754`](https://github.com/basicmachines-co/basic-memory/commit/90d5754180beaf4acd4be38f2438712555640b49))


## v0.8.0 (2025-02-28)

### Chores

- Formatting
  ([`93cc637`](https://github.com/basicmachines-co/basic-memory/commit/93cc6379ebb9ecc6a1652feeeecbf47fc992d478))

- Refactor logging setup
  ([`f4b703e`](https://github.com/basicmachines-co/basic-memory/commit/f4b703e57f0ddf686de6840ff346b8be2be499ad))

### Features

- Add enhanced prompts and resources
  ([#15](https://github.com/basicmachines-co/basic-memory/pull/15),
  [`093dab5`](https://github.com/basicmachines-co/basic-memory/commit/093dab5f03cf7b090a9f4003c55507859bf355b0))

## Summary - Add comprehensive documentation to all MCP prompt modules - Enhance search prompt with
  detailed contextual output formatting - Implement consistent logging and docstring patterns across
  prompt utilities - Fix type checking in prompt modules

## Prompts Added/Enhanced - `search.py`: New formatted output with relevance scores, excerpts, and
  next steps - `recent_activity.py`: Enhanced with better metadata handling and documentation -
  `continue_conversation.py`: Improved context management

## Resources Added/Enhanced - `ai_assistant_guide`: Resource with description to give to LLM to
  understand how to use the tools

## Technical improvements - Added detailed docstrings to all prompt modules explaining their purpose
  and usage - Enhanced the search prompt with rich contextual output that helps LLMs understand
  results - Created a consistent pattern for formatting output across prompts - Improved error
  handling in metadata extraction - Standardized import organization and naming conventions - Fixed
  various type checking issues across the codebase

This PR is part of our ongoing effort to improve the MCP's interaction quality with LLMs, making the
  system more helpful and intuitive for AI assistants to navigate knowledge bases.

🤖 Generated with [Claude Code](https://claude.ai/code)

---------

Co-authored-by: phernandez <phernandez@basicmachines.co>

- Add new `canvas` tool to create json canvas files in obsidian.
  ([#14](https://github.com/basicmachines-co/basic-memory/pull/14),
  [`0d7b0b3`](https://github.com/basicmachines-co/basic-memory/commit/0d7b0b3d7ede7555450ddc9728951d4b1edbbb80))

Add new `canvas` tool to create json canvas files in obsidian.

---------

Co-authored-by: phernandez <phernandez@basicmachines.co>

- Incremental sync on watch ([#13](https://github.com/basicmachines-co/basic-memory/pull/13),
  [`37a01b8`](https://github.com/basicmachines-co/basic-memory/commit/37a01b806d0758029d34a862e76d44c7e5d538a5))

- incremental sync on watch - sync non-markdown files in knowledge base - experimental
  `read_resource` tool for reading non-markdown files in raw form (pdf, image)


## v0.7.0 (2025-02-19)

### Bug Fixes

- Add logfire instrumentation to tools
  ([`3e8e3e8`](https://github.com/basicmachines-co/basic-memory/commit/3e8e3e8961eae2e82839746e28963191b0aef0a0))

- Add logfire spans to cli
  ([`00d23a5`](https://github.com/basicmachines-co/basic-memory/commit/00d23a5ee15ddac4ea45e702dcd02ab9f0509276))

- Add logfire spans to cli
  ([`812136c`](https://github.com/basicmachines-co/basic-memory/commit/812136c8c22ad191d14ff32dcad91aae076d4120))

- Search query pagination params
  ([`bc9ca07`](https://github.com/basicmachines-co/basic-memory/commit/bc9ca0744ffe4296d7d597b4dd9b7c73c2d63f3f))

### Chores

- Fix tests
  ([`57984aa`](https://github.com/basicmachines-co/basic-memory/commit/57984aa912625dcde7877afb96d874c164af2896))

- Remove unused tests
  ([`2c8ed17`](https://github.com/basicmachines-co/basic-memory/commit/2c8ed1737d6769fe1ef5c96f8a2bd75b9899316a))

### Features

- Add cli commands for mcp tools
  ([`f5a7541`](https://github.com/basicmachines-co/basic-memory/commit/f5a7541da17e97403b7a702720a05710f68b223a))

- Add pagination to build_context and recent_activity
  ([`0123544`](https://github.com/basicmachines-co/basic-memory/commit/0123544556513af943d399d70b849b142b834b15))

- Add pagination to read_notes
  ([`02f8e86`](https://github.com/basicmachines-co/basic-memory/commit/02f8e866923d5793d2620076c709c920d99f2c4f))


## v0.6.0 (2025-02-18)

### Chores

- Re-add sync status console on watch
  ([`66b57e6`](https://github.com/basicmachines-co/basic-memory/commit/66b57e682f2e9c432bffd4af293b0d1db1d3469b))

### Features

- Configure logfire telemetry ([#12](https://github.com/basicmachines-co/basic-memory/pull/12),
  [`6da1438`](https://github.com/basicmachines-co/basic-memory/commit/6da143898bd45cdab8db95b5f2b75810fbb741ba))

Co-authored-by: phernandez <phernandez@basicmachines.co>


## v0.5.0 (2025-02-18)

### Features

- Return semantic info in markdown after write_note
  ([#11](https://github.com/basicmachines-co/basic-memory/pull/11),
  [`0689e7a`](https://github.com/basicmachines-co/basic-memory/commit/0689e7a730497827bf4e16156ae402ddc5949077))

Co-authored-by: phernandez <phernandez@basicmachines.co>


## v0.4.3 (2025-02-18)

### Bug Fixes

- Re do enhanced read note format ([#10](https://github.com/basicmachines-co/basic-memory/pull/10),
  [`39bd5ca`](https://github.com/basicmachines-co/basic-memory/commit/39bd5ca08fd057220b95a8b5d82c5e73a1f5722b))

Co-authored-by: phernandez <phernandez@basicmachines.co>


## v0.4.2 (2025-02-17)


## v0.4.1 (2025-02-17)

### Bug Fixes

- Fix alemic config
  ([`71de8ac`](https://github.com/basicmachines-co/basic-memory/commit/71de8acfd0902fc60f27deb3638236a3875787ab))

- More alembic fixes
  ([`30cd74e`](https://github.com/basicmachines-co/basic-memory/commit/30cd74ec95c04eaa92b41b9815431f5fbdb46ef8))


## v0.4.0 (2025-02-16)

### Features

- Import chatgpt conversation data ([#9](https://github.com/basicmachines-co/basic-memory/pull/9),
  [`56f47d6`](https://github.com/basicmachines-co/basic-memory/commit/56f47d6812982437f207629e6ac9a82e0e56514e))

Co-authored-by: phernandez <phernandez@basicmachines.co>

- Import claude.ai data ([#8](https://github.com/basicmachines-co/basic-memory/pull/8),
  [`a15c346`](https://github.com/basicmachines-co/basic-memory/commit/a15c346d5ebd44344b76bad877bb4d1073fcbc3b))

Import Claude.ai conversation and project data to basic-memory Markdown format.

---------

Co-authored-by: phernandez <phernandez@basicmachines.co>


## v0.3.0 (2025-02-15)

### Bug Fixes

- Refactor db schema migrate handling
  ([`ca632be`](https://github.com/basicmachines-co/basic-memory/commit/ca632beb6fed5881f4d8ba5ce698bb5bc681e6aa))


## v0.2.21 (2025-02-15)

### Bug Fixes

- Fix osx installer github action
  ([`65ebe5d`](https://github.com/basicmachines-co/basic-memory/commit/65ebe5d19491e5ff047c459d799498ad5dd9cd1a))

- Handle memory:// url format in read_note tool
  ([`e080373`](https://github.com/basicmachines-co/basic-memory/commit/e0803734e69eeb6c6d7432eea323c7a264cb8347))

- Remove create schema from init_db
  ([`674dd1f`](https://github.com/basicmachines-co/basic-memory/commit/674dd1fd47be9e60ac17508476c62254991df288))

### Features

- Set version in var, output version at startup
  ([`a91da13`](https://github.com/basicmachines-co/basic-memory/commit/a91da1396710e62587df1284da00137d156fc05e))


## v0.2.20 (2025-02-14)

### Bug Fixes

- Fix installer artifact
  ([`8de84c0`](https://github.com/basicmachines-co/basic-memory/commit/8de84c0221a1ee32780aa84dac4d3ea60895e05c))


## v0.2.19 (2025-02-14)

### Bug Fixes

- Get app artifact for installer
  ([`fe8c3d8`](https://github.com/basicmachines-co/basic-memory/commit/fe8c3d87b003166252290a87cbe958301cccf797))


## v0.2.18 (2025-02-14)

### Bug Fixes

- Don't zip app on release
  ([`8664c57`](https://github.com/basicmachines-co/basic-memory/commit/8664c57bb331d7f3f7e0239acb5386c7a3c6144e))


## v0.2.17 (2025-02-14)

### Bug Fixes

- Fix app zip in installer release
  ([`8fa197e`](https://github.com/basicmachines-co/basic-memory/commit/8fa197e2ec8a1b6caaf6dbb39c3c6626bba23e2e))


## v0.2.16 (2025-02-14)

### Bug Fixes

- Debug inspect build on ci
  ([`1d6054d`](https://github.com/basicmachines-co/basic-memory/commit/1d6054d30a477a4e6a5d6ac885632e50c01945d3))


## v0.2.15 (2025-02-14)

### Bug Fixes

- Debug installer ci
  ([`dab9573`](https://github.com/basicmachines-co/basic-memory/commit/dab957314aec9ed0e12abca2265552494ae733a2))


## v0.2.14 (2025-02-14)


## v0.2.13 (2025-02-14)

### Bug Fixes

- Refactor release.yml installer
  ([`a152657`](https://github.com/basicmachines-co/basic-memory/commit/a15265783e47c22d8c7931396281d023b3694e27))

- Try using symlinks in installer build
  ([`8dd923d`](https://github.com/basicmachines-co/basic-memory/commit/8dd923d5bc0587276f92b5f1db022ad9c8687e45))


## v0.2.12 (2025-02-14)

### Bug Fixes

- Fix cx_freeze options for installer
  ([`854cf83`](https://github.com/basicmachines-co/basic-memory/commit/854cf8302e2f83578030db05e29b8bdc4348795a))


## v0.2.11 (2025-02-14)

### Bug Fixes

- Ci installer app fix #37
  ([`2e215fe`](https://github.com/basicmachines-co/basic-memory/commit/2e215fe83ca421b921186c7f1989dc2cb5cca278))


## v0.2.10 (2025-02-14)

### Bug Fixes

- Fix build on github ci for app installer
  ([`29a2594`](https://github.com/basicmachines-co/basic-memory/commit/29a259421a0ccb10cfa68e3707eaa506ad5e55c0))


## v0.2.9 (2025-02-14)


## v0.2.8 (2025-02-14)

### Bug Fixes

- Fix installer on ci, maybe
  ([`edbc04b`](https://github.com/basicmachines-co/basic-memory/commit/edbc04be601d234bb1f5eb3ba24d6ad55244b031))


## v0.2.7 (2025-02-14)

### Bug Fixes

- Try to fix installer ci
  ([`230738e`](https://github.com/basicmachines-co/basic-memory/commit/230738ee9c110c0509e0a09cb0e101a92cfcb729))


## v0.2.6 (2025-02-14)

### Bug Fixes

- Bump project patch version
  ([`01d4672`](https://github.com/basicmachines-co/basic-memory/commit/01d46727b40c24b017ea9db4b741daef565ac73e))

- Fix installer setup.py change ci to use make
  ([`3e78fcc`](https://github.com/basicmachines-co/basic-memory/commit/3e78fcc2c208d83467fe7199be17174d7ffcad1a))


## v0.2.5 (2025-02-14)

### Bug Fixes

- Refix vitual env in installer build
  ([`052f491`](https://github.com/basicmachines-co/basic-memory/commit/052f491fff629e8ead629c9259f8cb46c608d584))


## v0.2.4 (2025-02-14)


## v0.2.3 (2025-02-14)

### Bug Fixes

- Workaround unsigned app
  ([`41d4d81`](https://github.com/basicmachines-co/basic-memory/commit/41d4d81c1ad1dc2923ba0e903a57454a0c8b6b5c))


## v0.2.2 (2025-02-14)

### Bug Fixes

- Fix path to intaller app artifact
  ([`53d220d`](https://github.com/basicmachines-co/basic-memory/commit/53d220df585561f9edd0d49a9e88f1d4055059cf))


## v0.2.1 (2025-02-14)

### Bug Fixes

- Activate vitualenv in installer build
  ([`d4c8293`](https://github.com/basicmachines-co/basic-memory/commit/d4c8293687a52eaf3337fe02e2f7b80e4cc9a1bb))

- Trigger installer build on release
  ([`f11bf78`](https://github.com/basicmachines-co/basic-memory/commit/f11bf78f3f600d0e1b01996cf8e1f9c39e3dd218))


## v0.2.0 (2025-02-14)

### Features

- Build installer via github action ([#7](https://github.com/basicmachines-co/basic-memory/pull/7),
  [`7c381a5`](https://github.com/basicmachines-co/basic-memory/commit/7c381a59c962053c78da096172e484f28ab47e96))

* feat(ci): build installer via github action

* enforce conventional commits in PR titles

* feat: add icon to installer

---------

Co-authored-by: phernandez <phernandez@basicmachines.co>


## v0.1.2 (2025-02-14)

### Bug Fixes

- Fix installer for mac
  ([`dde9ff2`](https://github.com/basicmachines-co/basic-memory/commit/dde9ff228b72852b5abc58faa1b5e7c6f8d2c477))

- Remove unused FileChange dataclass
  ([`eb3360c`](https://github.com/basicmachines-co/basic-memory/commit/eb3360cc221f892b12a17137ae740819d48248e8))

- Update uv installer url
  ([`2f9178b`](https://github.com/basicmachines-co/basic-memory/commit/2f9178b0507b3b69207d5c80799f2d2f573c9a04))


## v0.1.1 (2025-02-07)


## v0.1.0 (2025-02-07)

### Bug Fixes

- Create virtual env in test workflow
  ([`8092e6d`](https://github.com/basicmachines-co/basic-memory/commit/8092e6d38d536bfb6f93c3d21ea9baf1814f9b0a))

- Fix permalink uniqueness violations on create/update/sync
  ([`135bec1`](https://github.com/basicmachines-co/basic-memory/commit/135bec181d9b3d53725c8af3a0959ebc1aa6afda))

- Fix recent activity bug
  ([`3d2c0c8`](https://github.com/basicmachines-co/basic-memory/commit/3d2c0c8c32fcfdaf70a1f96a59d8f168f38a1aa9))

- Install fastapi deps after removing basic-foundation
  ([`51a741e`](https://github.com/basicmachines-co/basic-memory/commit/51a741e7593a1ea0e5eb24e14c70ff61670f9663))

- Recreate search index on db reset
  ([`1fee436`](https://github.com/basicmachines-co/basic-memory/commit/1fee436bf903a35c9ebb7d87607fc9cc9f5ff6e7))

- Remove basic-foundation from deps
  ([`b8d0c71`](https://github.com/basicmachines-co/basic-memory/commit/b8d0c7160f29c97cdafe398a7e6a5240473e0c89))

- Run tests via uv
  ([`4eec820`](https://github.com/basicmachines-co/basic-memory/commit/4eec820a32bc059a405e2f4dac4c73b245ca4722))

### Chores

- Rename import tool
  ([`af6b7dc`](https://github.com/basicmachines-co/basic-memory/commit/af6b7dc40a55eaa2aa78d6ea831e613851081d52))

### Features

- Add memory-json importer, tweak observation content
  ([`3484e26`](https://github.com/basicmachines-co/basic-memory/commit/3484e26631187f165ee6eb85517e94717b7cf2cf))


## v0.0.1 (2025-02-04)

### Bug Fixes

- Fix versioning for 0.0.1 release
  ([`ba1e494`](https://github.com/basicmachines-co/basic-memory/commit/ba1e494ed1afbb7af3f97c643126bced425da7e0))


## v0.0.0 (2025-02-04)

### Chores

- Remove basic-foundation src ref in pyproject.toml
  ([`29fce8b`](https://github.com/basicmachines-co/basic-memory/commit/29fce8b0b922d54d7799bf2534107ee6cfb961b8))