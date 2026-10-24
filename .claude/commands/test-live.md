# /project:test-live - Live Basic Memory Testing Suite

Execute comprehensive real-world testing of Basic Memory using the installed version.
All test results are recorded as notes in a dedicated test project.

## Usage
```
/project:test-live [phase]
```

**Parameters:**
- `phase` (optional): Specific test phase to run (`recent`, `core`, `features`, `edge`, `workflows`, `stress`, or `all`)
- `recent` - Focus on recent changes and new features (recommended for regular testing)
- `core` - Essential tools only (Tier 1: bm_write, bm_read, bm_search, bm_edit, bm_project(command="list"), bm_project(command="recent"))
- `features` - Core + important workflows (Tier 1 + Tier 2)
- `all` - Comprehensive testing of all tools and scenarios

## Implementation

You are an expert QA engineer conducting live testing of Basic Memory.
When the user runs `/project:test-live`, execute comprehensive test plan:

## Tool Testing Priority

### **Tier 1: Critical Core (Always Test)**
1. **bm_write** - Foundation of all knowledge creation
2. **bm_read** - Primary knowledge retrieval mechanism
3. **bm_search** - Essential for finding information
4. **bm_edit** - Core content modification capability
5. **bm_project(command="list")** - Project discovery and session guidance
6. **bm_project(command="recent")** - Project discovery mode and activity analysis

### **Tier 2: Important Workflows (Usually Test)**
7. **bm_context** - Conversation continuity via memory:// URLs
8. **bm_project(command="create")** - Essential for project setup
9. **bm_move** - Knowledge organization
10. **bm_project_delete** - Project lifecycle management

### **Tier 3: Enhanced Functionality (Sometimes Test)**
11. **bm_read** — bounded section/line/token reads and non-Markdown files
12. **bm_delete** — content removal
13. **bm_list / bm_find** — one-level listing, recursive names, frontmatter inventories
14. **bm_edit** — advanced section commands and metadata updates
15. **bm_schema** — `command="validate"`, `"infer"`, and `"diff"`
16. **bm_man** — manual index, page lookup, and text search

### **Tier 4: Specialized (Rarely Test)**
17. **MCP prompts and resources** — assistant guide, conversation continuity, manual pages

## Project Routing Tests

1. Call `bm_project(command="list")`; inspect project names, external IDs, and
   workspace grouping. Reuse a returned UUID through `project`.
2. Call `bm_project(command="recent", project=<test-project>, since="7d")`.
   Check paging and indexing guidance. Without `project`, it uses the default
   project; with no default, it aggregates projects. Empty activity is not proof
   of an empty knowledge base.
3. Test explicit names, workspace-qualified names, UUIDs, and project-qualified
   paths. A path prefix and explicit project must agree. Verify ambiguous and
   conflicting routes fail clearly.
4. Test a server constrained with `--project`. Verify operations cannot bypass
   that constraint.
5. Switch between two disposable projects with the same note title. Confirm reads
   and writes stay in the requested project and do not depend on an earlier call.
6. Test omission according to each tool's schema, including default-project use
   and ambiguous unqualified note refusals. Do not expect every tool to require
   `project` or to emit a session-tracking footer.

### Pre-Test Setup

1. **Environment Verification**
   - Verify basic-memory is installed and accessible via MCP
   - Check version and confirm it's the expected release
   - Test MCP connection and tool availability

2. **Recent Changes Analysis** (if phase includes 'recent' or 'all')
   - Run `git log --oneline -20` to examine recent commits
   - Identify new features, bug fixes, and enhancements
   - Generate targeted test scenarios for recent changes
   - Prioritize regression testing for recently fixed issues

3. **Test Project Creation**

Run the bash `date` command to get the current date/time.

   ```
   Create project: "basic-memory-testing-[timestamp]"
   Location: ~/basic-memory-testing-[timestamp]
   Purpose: Record all test observations and results
   ```

Make sure to use the newly created project for all subsequent test operations by specifying it in the `project` parameter of each tool call.

4. **Baseline Documentation**
   Create initial test session note with:
   - Test environment details
   - Version being tested
   - Recent changes identified (if applicable)
   - Test objectives and scope
   - Start timestamp

### Phase 0: Recent Changes Validation (if 'recent' or 'all' phase)

Based on recent commit analysis, create targeted test scenarios:

**Recent Changes Test Protocol:**
1. **Feature Addition Tests** - For each new feature identified:
   - Test basic functionality
   - Test integration with existing tools
   - Verify documentation accuracy
   - Test edge cases and error handling

2. **Bug Fix Regression Tests** - For each recent fix:
   - Recreate the original problem scenario
   - Verify the fix works as expected
   - Test related functionality isn't broken
   - Document the verification in test notes

3. **Performance/Enhancement Validation** - For optimizations:
   - Establish baseline timing
   - Compare with expected improvements
   - Test under various load conditions
   - Document performance observations

Use the actual recent commits to choose regression cases; do not reuse a fixed
list of old issue numbers as the current release scope.

### Phase 1: Core Functionality Validation (Tier 1 Tools)

Test essential MCP tools that form the foundation of Basic Memory:

**1. bm_write Tests (Critical):**
- ✅ Basic note creation with frontmatter
- ✅ Special characters and Unicode in titles
- ✅ Various content types (lists, headings, code blocks)
- ✅ Empty notes and minimal content edge cases
- ⚠️ Error handling for invalid parameters

**2. bm_read Tests (Critical):**
- ✅ Read by title, permalink, memory:// URLs
- ✅ Non-existent notes (error handling)
- ✅ Notes with complex markdown formatting
- ⚠️ Performance with large notes (>10MB)

**3. bm_search Tests (Critical):**
- ✅ Simple text queries across content
- ✅ Tag filters via `filters={"tags": [...]}` and `tag:` query shorthand
- ✅ Boolean operators (AND, OR, NOT)
- ✅ Empty/no results scenarios
- ⚠️ Performance with 100+ notes

**4. bm_edit Tests (Critical):**
- ✅ Append operations preserving frontmatter
- ✅ Prepend operations
- ✅ Find/replace with validation
- ✅ Section replacement under headers
- ✅ `command="set_metadata"` preserves other frontmatter fields
- ✅ Missing-note edits fail instead of creating notes
- ✅ Stale `expected_checksum` refuses changes
- ⚠️ Error scenarios (invalid commands)

**5. bm_project(command="list") Tests (Critical):**
- Project identities and workspace grouping
- Empty project list and constrained server behavior
- UUIDs reusable through `project`

**6. bm_project(command="recent") Tests (Critical):**
- Explicit project, default project, and no-default aggregation
- `since`, `items`, and pagination
- Empty activity and never-indexed guidance

### Phase 2: Important Workflows (Tier 2 Tools)

**7. bm_context Tests (Important):**
- ✅ Different depth levels (1, 2, 3+)
- ✅ `since` windows (default `7d`), depth 1–5, and context page size at most 50
- ✅ memory:// URL navigation
- ⚠️ Performance with complex relation graphs

**8. bm_project(command="create") Tests (Important):**
- ✅ Create projects dynamically
- ✅ Set default during creation
- ✅ Path validation and creation
- ⚠️ Invalid paths and names
- ✅ Integration with existing projects

**9. bm_move Tests (Important):**
- ✅ Move within same project
- ✅ Refuse cross-project moves; preserve source and destination
- ✅ Automatic folder creation
- ✅ Database consistency validation
- ⚠️ Special characters in paths

### Phase 3: Enhanced Functionality (Tier 3 Tools)

**10. bm_read (Markdown display) Tests (Enhanced):**
- ✅ Markdown returned for host display; no artifact-opening guarantee
- ✅ Title extraction from frontmatter
- ✅ Unicode and emoji content rendering
- ⚠️ Error handling for non-existent notes

**11. bm_read (file content) Tests (Enhanced):**
- ✅ Raw file content access
- ✅ Binary file handling
- ✅ Image file reading
- ⚠️ Large file performance

**12. bm_delete Tests (Enhanced):**
- ✅ Single note deletion
- ✅ Database consistency after deletion
- ⚠️ Non-existent note handling
- ✅ Confirmation of successful deletion

**13. bm_list / bm_find Tests (Enhanced):**
- ✅ Directory content listing
- ✅ `bm_list` lists one level; `bm_find` controls recursive depth
- ✅ File name globbing
- ⚠️ Empty directory handling

**14. bm_project_delete Tests (Enhanced):**
- ✅ Project removal from config
- ✅ Database cleanup
- ⚠️ Default project protection
- ⚠️ Non-existent project handling

**Schema and manual discovery:**
- Validate one fixture by identifier and a note type with `bm_schema(command="validate")`.
- Infer and diff a fixture type using the corresponding `command` values.
- Read `bm_man(page="bm-edit(3)")`; confirm documented signatures match the server.

### Phase 4: Edge Case Exploration

**Boundary Testing:**
- Very long titles and content (stress limits)
- Empty projects and notes
- Unicode, emojis, special symbols
- Deeply nested folder structures
- Circular relations and self-references
- Maximum relation depths

**Error Scenarios:**
- Invalid memory:// URLs
- Missing files referenced in database
- Invalid project names and paths
- Malformed note structures
- Concurrent operation conflicts

**Performance Testing:**
- Create 100+ notes rapidly
- Complex search queries
- Deep relation chains (5+ levels)
- Rapid successive operations
- Memory usage monitoring

### Phase 5: Real-World Workflow Scenarios

**Meeting Notes Pipeline:**
1. Create meeting notes with action items
2. Extract action items using bm_edit
3. Build relations to project documents
4. Update progress incrementally
5. Search and track completion

**Research Knowledge Building:**
1. Create research topic hierarchy
2. Build complex relation networks
3. Add incremental findings over time
4. Search for connections and patterns
5. Reorganize as knowledge evolves

**Multi-Project Workflow:**
1. Technical documentation project
2. Personal recipe collection project
3. Learning/course notes project
4. Specify different projects for different operations
5. Cross-reference related concepts

**Content Evolution:**
1. Start with basic notes
2. Enhance with relations and observations
3. Reorganize file structure using moves
4. Update content with edit operations
5. Validate knowledge graph integrity

### Phase 6: Specialized Tools Testing (Tier 4)

**15. MCP Prompts Tests (Specialized):**
- ✅ ai_assistant_guide output
- ✅ continue_conversation functionality
- ✅ Formatted search results
- ✅ Enhanced activity reports

### Phase 7: Integration & File Watching Tests

**File System Integration:**
- ✅ Watch service behavior with file changes
- ✅ Project creation → watch restart (#156)
- ✅ Multi-project synchronization
- ⚠️ MCP→API→DB→File stack validation

**Real Integration Testing:**
- ✅ End-to-end file watching vs manual operations
- ✅ Cross-session persistence
- ✅ Database consistency across operations
- ⚠️ Performance under real file system changes

### Phase 8: Creative Stress Testing

**Creative Exploration:**
- Rapid project creation/switching patterns
- Unusual but valid markdown structures
- Creative observation categories
- Novel relation types and patterns
- Unexpected tool combinations

**Stress Scenarios:**
- Bulk operations (many notes quickly)
- Complex nested moves and edits
- Deep context building
- Complex boolean search expressions
- Resource constraint testing

## Test Execution Guidelines

### Quick Testing (core/features phases)
- Focus on Tier 1 tools (core) or Tier 1+2 (features)
- Test essential functionality and common edge cases
- Record critical issues immediately
- Complete in 15-20 minutes

### Comprehensive Testing (all phase)
- Cover all tiers systematically
- Include specialized tools and stress testing
- Document performance baselines
- Complete in 45-60 minutes

### Recent Changes Focus (recent phase)
- Analyze git log for recent commits
- Generate targeted test scenarios
- Focus on regression testing for fixes
- Validate new features thoroughly

## Test Observation Format

Record ALL observations immediately as Basic Memory notes:

```markdown
---
title: Test Session [Phase] YYYY-MM-DD HH:MM
tags: [testing, [installed-version], live-testing, [phase]]
permalink: test-session-[phase]-[timestamp]
---

# Test Session [Phase] - [Date/Time]

## Environment
- Basic Memory version: [version]
- MCP connection: [status]
- Test project: [name]
- Phase focus: [description]

## Test Results

### ✅ Successful Operations
- [timestamp] ✅ bm_write: Created note with emoji title 📝 #tier1 #functionality
- [timestamp] ✅ bm_search: Boolean query returned 23 results in 0.4s #tier1 #performance
- [timestamp] ✅ bm_edit: Append operation preserved frontmatter #tier1 #reliability

### ⚠️ Issues Discovered
- [timestamp] ⚠️ bm_move: Slow with deep folder paths (2.1s) #tier2 #performance
- [timestamp] 🚨 bm_search: Unicode query returned unexpected results #tier1 #bug #critical
- [timestamp] ⚠️ bm_context: Context lost for memory:// URLs #tier2 #issue

### 🚀 Enhancements Identified
- bm_edit could benefit from preview mode #ux-improvement
- bm_search needs fuzzy matching for typos #feature-idea
- bm_move could auto-suggest folder creation #usability

### 📊 Performance Metrics
- Average bm_write time: 0.3s
- Search with 100+ notes: 0.6s
- Project parameter overhead: <0.1s
- Memory usage: [observed levels]

## Relations
- tests [[Basic Memory [installed-version]]]
- part_of [[Live Testing Suite]]
- found_issues [[Bug Report: Unicode Search]]
- discovered [[Performance Optimization Opportunities]]
```

## Quality Assessment Areas

**User Experience & Usability:**
- Tool instruction clarity and examples
- Error message actionability
- Response time acceptability
- Tool consistency and discoverability
- Learning curve and intuitiveness

**System Behavior:**
- Stateless operation independence
- memory:// URL navigation reliability
- Multi-step workflow cohesion
- Edge case graceful handling
- Recovery from user errors

**Documentation Alignment:**
- Tool output clarity and helpfulness
- Behavior vs. documentation accuracy
- Example validity and usefulness
- Real-world vs. documented workflows

**Mental Model Validation:**
- Natural user expectation alignment
- Surprising behavior identification
- Mistake recovery ease
- Knowledge graph concept naturalness

**Performance & Reliability:**
- Operation completion times
- Consistency across sessions
- Scaling behavior with growth
- Unexpected slowness identification

## Error Documentation Protocol

For each error discovered:

1. **Immediate Recording**
   - Create dedicated error note
   - Include exact reproduction steps
   - Capture error messages verbatim
   - Note system state when error occurred

2. **Error Note Format**
   ```markdown
   ---
   title: Bug Report - [Short Description]
   tags: [bug, testing, [installed-version], [severity]]
   ---

   # Bug Report: [Description]

   ## Reproduction Steps
   1. [Exact steps to reproduce]
   2. [Include all parameters used]
   3. [Note any special conditions]

   ## Expected Behavior
   [What should have happened]

   ## Actual Behavior
   [What actually happened]

   ## Error Messages
   ```
   [Exact error text]
   ```

   ## Environment
   - Version: [version]
   - Project: [name]
   - Timestamp: [when]

   ## Severity
   - [ ] Critical (blocks major functionality)
   - [ ] High (impacts user experience)
   - [ ] Medium (workaround available)
   - [ ] Low (minor inconvenience)

   ## Relations
   - discovered_during [[Test Session [Phase]]]
   - affects [[Feature Name]]
   ```

## Success Metrics Tracking

**Quantitative Measures:**
- Test scenario completion rate
- Bug discovery count with severity
- Performance benchmark establishment
- Tool coverage completeness

**Qualitative Measures:**
- Conversation flow naturalness
- Knowledge graph quality
- User experience insights
- System reliability assessment

## Test Execution Flow

1. **Setup Phase**
   - Verify environment and create test project
   - Record baseline system state
   - Establish performance benchmarks

2. **Core Testing**
   - Execute test scenarios systematically
   - Record observations immediately
   - Note timestamps for performance tracking
   - Explore variations when interesting behaviors occur

3. **Documentation**
   - Create phase summary note
   - Link related test observations
   - Update running issues list
   - Record enhancement ideas

4. **Analysis Phase**
   - Review all observations across phases
   - Identify patterns and trends
   - Create comprehensive summary report
   - Generate development recommendations

## Testing Success Criteria

### Core Testing (Tier 1) - Must Pass
- All critical core cases function correctly
- No critical bugs in essential workflows
- Acceptable performance for basic operations
- Error handling works as expected

### Feature Testing (Tier 1+2) - Should Pass
- All core and important workflow cases function
- Workflow scenarios complete successfully
- Performance meets baseline expectations
- Integration points work correctly

### Comprehensive Testing (All Tiers) - Complete Coverage
- All tools tested across all scenarios
- Edge cases and stress testing completed
- Performance baselines established
- Full documentation of issues and enhancements

## Expected Outcomes

**System Validation:**
- Feature verification prioritized by tier importance
- Recent changes validated for regression
- Performance baseline establishment
- Bug identification with severity assessment

**Knowledge Base Creation:**
- Prioritized testing documentation
- Real usage examples for user guides
- Recent changes validation records
- Performance insights for optimization

**Development Insights:**
- Tier-based bug priority list
- Recent changes impact assessment
- Enhancement ideas from real usage
- User experience improvement areas

## Post-Test Deliverables

1. **Test Summary Note**
   - Overall results and findings
   - Critical issues requiring immediate attention
   - Enhancement opportunities discovered
   - System readiness assessment

2. **Bug Report Collection**
   - All discovered issues with reproduction steps
   - Severity and impact assessments
   - Suggested fixes where applicable

3. **Performance Baseline**
   - Timing data for all operations
   - Scaling behavior observations
   - Resource usage patterns

4. **UX Improvement Recommendations**
   - Usability enhancement suggestions
   - Documentation improvement areas
   - Tool design optimization ideas

5. **Updated TESTING.md**
   - Incorporate new test scenarios discovered
   - Update based on real execution experience
   - Add performance benchmarks and targets

## Context
- Uses real installed basic-memory version
- Tests complete MCP→API→DB→File stack
- Creates living documentation in Basic Memory itself
- Follows integration over isolation philosophy
- Prioritizes testing by tool importance and usage frequency
- Adapts to recent development changes dynamically
- Focuses on real usage patterns over checklist validation
- Generates actionable insights prioritized by impact
