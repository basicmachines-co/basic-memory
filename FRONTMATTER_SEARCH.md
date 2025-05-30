# Frontmatter Tag Search Implementation

## Overview

This document outlines the implementation of frontmatter tag search functionality for Basic Memory. The goal is to enable users to search for entities based on their frontmatter tags, improving discoverability of content.

## Current State

### What Works
- ✅ Tags are parsed from YAML frontmatter and stored in `entity.entity_metadata`
- ✅ FTS5 search infrastructure is in place
- ✅ Observation tags are already indexed and searchable
- ✅ Search metadata structure supports additional fields

### What's Missing
- ✅ Entity frontmatter tags are now included in search indexing (COMPLETED)
- ❌ No special tag search syntax (e.g., `tag:foo`) - Future Phase 2

### Example Data
Current entity metadata includes tags:
```json
{
  "title": "Business Strategy Index",
  "type": "note", 
  "permalink": "business/business-strategy-index",
  "tags": ["business", "strategy", "planning", "organization"]
}
```

## Implementation Plan

### Phase 1: Basic Tag Search (v0.13.0) - LOW RISK ⭐

**Goal:** Make frontmatter tags searchable via regular text search

**Approach:** Add entity tags to `content_stems` during search indexing

**Benefits:**
- Users can search for tags as regular text
- Zero risk to existing search functionality
- Immediate value with minimal code changes

**Implementation Tasks:**

1. **Update Search Indexing** (`search_service.py`)
   - Extract tags from `entity.entity_metadata` 
   - Add tags to `content_stems` for entity indexing
   - Handle both string and list tag formats

2. **Add Tests**
   - Test tag extraction from entity metadata
   - Test searching for entities by tag content
   - Test both list and string tag formats

3. **Verify Existing Tag Data**
   - Ensure consistent tag format in metadata
   - Test with real data from existing entities

### Phase 2: Enhanced Tag Search (Future) - MEDIUM RISK ⭐⭐⭐

**Goal:** Add dedicated tag search syntax (`tag:foo`)

**Approach:** Extend search query parsing and repository

**Benefits:**
- More precise tag-only searches
- Better search result categorization
- Foundation for advanced tag operations

**Implementation Tasks:**
- Update search query parsing to handle `tag:` prefix
- Add tag-specific search repository methods
- Update search result metadata to highlight tag matches
- Comprehensive testing of new search syntax

## File Changes Required (Phase 1)

### Primary Changes

1. **`src/basic_memory/services/search_service.py`**
   - Update `index_entity_markdown()` method
   - Add entity tag extraction logic
   - Include tags in content_stems

2. **`tests/services/test_search_service.py`**
   - Add test for entity tag indexing
   - Add test for searching entities by tags
   - Test tag format handling

### Supporting Changes

3. **`tests/mcp/test_tool_search.py`** (if exists)
   - Add integration tests for tag search via MCP tools

## Success Criteria

### Phase 1 ✅ COMPLETED
- [x] Entity frontmatter tags are included in search index
- [x] Users can find entities by searching tag text
- [x] All existing search functionality continues to work
- [x] Test coverage for new functionality
- [x] Works with both list and string tag formats

### Phase 2 (Future)
- [ ] `tag:foo` syntax returns only entities with that tag
- [ ] Multiple tag search (`tag:foo tag:bar`)
- [ ] Tag autocomplete/suggestions
- [ ] Search result metadata shows matched tags

## Risk Assessment

### Phase 1 Risks: ⭐ VERY LOW
- **Code Impact:** ~20 lines in search service
- **Search Logic:** No changes to core search functionality
- **Backward Compatibility:** 100% - only adds to existing search content
- **Testing:** Straightforward unit tests required

### Phase 2 Risks: ⭐⭐⭐ MEDIUM
- **Code Impact:** Query parsing, repository methods, API changes
- **Search Logic:** New search syntax parsing required
- **Backward Compatibility:** Must maintain existing search behavior
- **Testing:** Complex query parsing and edge case testing

## Implementation Notes

### Tag Format Handling
Entity metadata contains tags in different formats:
```python
# List format (preferred)
"tags": ["business", "strategy", "planning"]

# String format (legacy)
"tags": "['documentation', 'tools', 'best-practices']"

# Empty
"tags": "[]"
```

The implementation must handle all formats gracefully.

### Search Content Inclusion
Tags will be added to `content_stems` which already includes:
- Entity title variants
- Entity content
- Permalink variants  
- File path variants

Adding tags to this stream maintains consistency with existing search behavior.

## Implementation Details (Phase 1 COMPLETED)

### Changes Made

1. **`src/basic_memory/services/search_service.py`** ✅
   - Added `_extract_entity_tags()` helper method to handle multiple tag formats
   - Modified `index_entity_markdown()` to include entity tags in `content_stems`
   - Added proper error handling for malformed tag data

2. **`tests/services/test_search_service.py`** ✅  
   - Added 8 comprehensive tests covering all tag formats and edge cases
   - Tests verify tag extraction, search indexing, and search functionality
   - Includes tests for both list and string tag formats

### Key Implementation Features

- **Robust Tag Parsing:** Handles list format, string format, and edge cases
- **Safe Evaluation:** Uses `ast.literal_eval()` for parsing string representations
- **Backward Compatible:** Zero impact on existing search functionality  
- **Comprehensive Testing:** Full test coverage for all scenarios

### Tag Format Support
```python
# All these formats are now properly handled:
"tags": ["business", "strategy"]                    # List format
"tags": "['documentation', 'tools']"                # String format  
"tags": "[]"                                        # Empty string
"tags": []                                          # Empty list
# Missing tags key or metadata - gracefully handled
```

## Next Steps (Future)

1. **Consider Phase 2:** Enhanced tag search syntax for future release
2. **Monitor Usage:** Track how users search for tags
3. **Gather Feedback:** Understand if `tag:foo` syntax would be valuable
4. **Performance Monitoring:** Ensure tag indexing doesn't impact performance