# v0.13.0 Release Issues

This document tracks the issues identified for the v0.13.0 release, organized by priority.

## High Priority Bug Fixes

These issues address core functionality problems and should be resolved first:

### ~~#118: [BUG] Non-standard tag markup in YAML frontmatter~~ ✅ COMPLETED
- **Impact**: Data quality issue affecting tag formatting
- **Description**: Tags are improperly formatted with `#` prefix and incorrect YAML indentation
- **Expected**: `tags:\n  - basicmemory`
- **Actual**: `tags:\n- '#basicmemory'`
- **Complexity**: Low - straightforward formatting fix
- **User Impact**: High - affects all tag usage
- **Resolution**: Fixed in write_note.py by removing `#` prefix from tag formatting

### ~~#110: [BUG] `--project` flag ignored in some commands~~ ✅ COMPLETED
- **Impact**: Breaks multi-project functionality added in v0.12.3
- **Description**: Commands like `project info` and `sync` don't respect `--project` flag
- **Root Cause**: Inconsistent project parameter handling across CLI commands
- **Complexity**: Medium - requires CLI argument parsing review
- **User Impact**: High - breaks core multi-project workflow
- **Resolution**: Fixed CLI app callback to update global config when --project specified

### ~~#107: [BUG] Fails to update note ("already exists")~~ ✅ ALREADY RESOLVED
- **Impact**: Prevents updating existing notes via write_note tool
- **Description**: `write_note` errors when target file exists, breaking daily note workflows
- **Root Cause**: EntityParser couldn't handle absolute paths correctly
- **Complexity**: Medium - requires write_note behavior enhancement
- **User Impact**: High - breaks core knowledge management workflow
- **Resolution**: Fixed in commit 9bff1f7 - EntityParser now handles absolute paths correctly

## Medium Priority Enhancements

These features would improve user experience and can be added if time permits:

### ~~#52: Search frontmatter tags~~ ✅ COMPLETED
- **Impact**: Enhances search capabilities
- **Description**: Include YAML frontmatter tags in search index
- **Implementation**: Index tags in search metadata, possibly add "tag:" search prefix
- **Complexity**: Medium - requires search index modification
- **User Impact**: Medium - improves discoverability
- **Resolution**: Implemented Phase 1 - frontmatter tags now included in FTS5 search index

### ~~#93: Reliable write_note Behavior for Populating Link Placeholders~~ ✅ COMPLETED
- **Impact**: Improves WikiLink workflow
- **Description**: Handle system-generated placeholder files gracefully in write_note
- **Features Needed**:
  - Detect and populate placeholder files
  - Respect user-specified permalinks in frontmatter
  - Consistent file conflict handling
- **Complexity**: High - requires significant write_note refactoring
- **User Impact**: Medium-High - smooths linking workflow
- **Resolution**: Fixed entity_service.py to parse frontmatter before permalink resolution. Both new and existing notes now respect custom permalinks specified in frontmatter.

## Lower Priority Issues

These issues are tracked but not planned for v0.13.0:

### External/Third-party
- **#116**: MseeP.ai badge PR (external contribution)

### Diagnostic/Investigation Needed
- **#99**: Timeout logs on Windows
- **#108**: Claude connection interruptions
- **#111**: Highlight app MCP errors
- **#97**: Notes become inaccessible on Windows 11
- **#96**: LLM not generating proper knowledge graph format

## Implementation Strategy

1. **Start with High Priority bugs** - these fix broken functionality
2. **Add Medium Priority enhancements** if time allows
3. **Investigate Lower Priority issues** for future releases

## Success Criteria for v0.13.0

- [x] YAML tag formatting follows standard specification
- [x] `--project` flag works consistently across all commands
- [x] `write_note` can update existing notes reliably
- [x] Custom permalinks in frontmatter are respected by write_note
- [x] Frontmatter tags are included in search index
- [x] Comprehensive test coverage for all fixes
- [ ] Documentation updates for any behavior changes

## Notes

This release focuses on stability and core functionality fixes rather than major new features. The goal is to ensure the multi-project system introduced in v0.12.3 works reliably and that basic knowledge management workflows are robust.