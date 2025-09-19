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
- `core` - Essential tools only (Tier 1: write_note, read_note, search_notes, edit_note, list_memory_projects, recent_activity)
- `features` - Core + important workflows (Tier 1 + Tier 2)
- `all` - Comprehensive testing of all tools and scenarios

## Implementation

You are an expert QA engineer conducting live testing of Basic Memory. 
When the user runs `/project:test-live`, execute comprehensive test plan:

## Tool Testing Priority

### **Tier 1: Critical Core (Always Test)**
1. **write_note** - Foundation of all knowledge creation
2. **read_note** - Primary knowledge retrieval mechanism
3. **search_notes** - Essential for finding information
4. **edit_note** - Core content modification capability
5. **list_memory_projects** - Project discovery and session guidance
6. **recent_activity** - Project discovery mode and activity analysis

### **Tier 2: Important Workflows (Usually Test)**
7. **build_context** - Conversation continuity via memory:// URLs
8. **create_memory_project** - Essential for project setup
9. **move_note** - Knowledge organization
10. **sync_status** - Understanding system state
11. **delete_project** - Project lifecycle management

### **Tier 3: Enhanced Functionality (Sometimes Test)**
12. **view_note** - Claude Desktop artifact display
13. **read_content** - Raw content access
14. **delete_note** - Content removal
15. **list_directory** - File system exploration
16. **edit_note** (advanced modes) - Complex find/replace operations

### **Tier 4: Specialized (Rarely Test)**
17. **canvas** - Obsidian visualization (specialized use case)
18. **MCP Prompts** - Enhanced UX tools (ai_assistant_guide, continue_conversation)

## Stateless Architecture Testing

### **Project Discovery Workflow (CRITICAL)**
Test the new stateless project selection flow:

1. **Initial Discovery**
   - Call `list_memory_projects()` without knowing which project to use
   - Verify clear session guidance appears: "Next: Ask which project to use"
   - Confirm removal of CLI-specific references

2. **Activity-Based Discovery**
   - Call `recent_activity()` without project parameter (discovery mode)
   - Verify intelligent project suggestions based on activity
   - Test guidance: "Should I use [most-active-project] for this task?"

3. **Session Tracking Validation**
   - Verify all tool responses include `[Session: Using project 'name']`
   - Confirm guidance reminds about session-wide project tracking

4. **Single Project Constraint Mode**
   - Test MCP server with `--project` parameter
   - Verify all operations constrained to specified project
   - Test project override behavior in constrained mode

### **Explicit Project Parameters (CRITICAL)**
All tools must require explicit project parameters:

1. **Parameter Validation**
   - Test all Tier 1 tools require `project` parameter
   - Verify clear error messages for missing project
   - Test invalid project name handling

2. **No Session State Dependencies**
   - Confirm no tool relies on "current project" concept
   - Test rapid project switching within conversation
   - Verify each call is truly independent

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

**Example Recent Changes (Update based on actual git log):**
- Watch Service Restart (#156): Test project creation → file modification → automatic restart
- Cross-Project Moves (#161): Test move_note with cross-project detection
- Docker Environment Support (#174): Test BASIC_MEMORY_HOME behavior
- MCP Server Logging (#164): Verify log level configurations

### Phase 1: Core Functionality Validation (Tier 1 Tools)

Test essential MCP tools that form the foundation of Basic Memory:

**1. write_note Tests (Critical):**
- ✅ Basic note creation with frontmatter
- ✅ Special characters and Unicode in titles
- ✅ Various content types (lists, headings, code blocks)
- ✅ Empty notes and minimal content edge cases
- ⚠️ Error handling for invalid parameters

**2. read_note Tests (Critical):**
- ✅ Read by title, permalink, memory:// URLs
- ✅ Non-existent notes (error handling)
- ✅ Notes with complex markdown formatting
- ⚠️ Performance with large notes (>10MB)

**3. search_notes Tests (Critical):**
- ✅ Simple text queries across content
- ✅ Tag-based searches with multiple tags
- ✅ Boolean operators (AND, OR, NOT)
- ✅ Empty/no results scenarios
- ⚠️ Performance with 100+ notes

**4. edit_note Tests (Critical):**
- ✅ Append operations preserving frontmatter
- ✅ Prepend operations
- ✅ Find/replace with validation
- ✅ Section replacement under headers
- ⚠️ Error scenarios (invalid operations)

**5. list_memory_projects Tests (Critical):**
- ✅ Display all projects with clear session guidance
- ✅ Project discovery workflow prompts
- ✅ Removal of CLI-specific references
- ✅ Empty project list handling
- ✅ Single project constraint mode display

**6. recent_activity Tests (Critical - Discovery Mode):**
- ✅ Discovery mode without project parameter
- ✅ Intelligent project suggestions based on activity
- ✅ Guidance prompts for project selection
- ✅ Session tracking reminders in responses
- ⚠️ Performance with multiple projects

### Phase 2: Important Workflows (Tier 2 Tools)

**7. build_context Tests (Important):**
- ✅ Different depth levels (1, 2, 3+)
- ✅ Various timeframes for context
- ✅ memory:// URL navigation
- ⚠️ Performance with complex relation graphs

**8. create_memory_project Tests (Important):**
- ✅ Create projects dynamically
- ✅ Set default during creation
- ✅ Path validation and creation
- ⚠️ Invalid paths and names
- ✅ Integration with existing projects

**9. move_note Tests (Important):**
- ✅ Move within same project
- ✅ Cross-project moves with detection (#161)
- ✅ Automatic folder creation
- ✅ Database consistency validation
- ⚠️ Special characters in paths

**10. sync_status Tests (Important):**
- ✅ Background operation monitoring
- ✅ File synchronization status
- ✅ Project sync state reporting
- ⚠️ Error state handling

### Phase 3: Enhanced Functionality (Tier 3 Tools)

**11. view_note Tests (Enhanced):**
- ✅ Claude Desktop artifact display
- ✅ Title extraction from frontmatter
- ✅ Unicode and emoji content rendering
- ⚠️ Error handling for non-existent notes

**12. read_content Tests (Enhanced):**
- ✅ Raw file content access
- ✅ Binary file handling
- ✅ Image file reading
- ⚠️ Large file performance

**13. delete_note Tests (Enhanced):**
- ✅ Single note deletion
- ✅ Database consistency after deletion
- ⚠️ Non-existent note handling
- ✅ Confirmation of successful deletion

**14. list_directory Tests (Enhanced):**
- ✅ Directory content listing
- ✅ Depth control and filtering
- ✅ File name globbing
- ⚠️ Empty directory handling

**15. delete_project Tests (Enhanced):**
- ✅ Project removal from config
- ✅ Database cleanup
- ⚠️ Default project protection
- ⚠️ Non-existent project handling

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
2. Extract action items using edit_note
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

**16. canvas Tests (Specialized):**
- ✅ JSON Canvas generation
- ✅ Node and edge creation
- ✅ Obsidian compatibility
- ⚠️ Complex graph handling

**17. MCP Prompts Tests (Specialized):**
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
tags: [testing, v0.13.0, live-testing, [phase]]
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
- [timestamp] ✅ write_note: Created note with emoji title 📝 #tier1 #functionality
- [timestamp] ✅ search_notes: Boolean query returned 23 results in 0.4s #tier1 #performance  
- [timestamp] ✅ edit_note: Append operation preserved frontmatter #tier1 #reliability

### ⚠️ Issues Discovered
- [timestamp] ⚠️ move_note: Slow with deep folder paths (2.1s) #tier2 #performance
- [timestamp] 🚨 search_notes: Unicode query returned unexpected results #tier1 #bug #critical
- [timestamp] ⚠️ build_context: Context lost for memory:// URLs #tier2 #issue

### 🚀 Enhancements Identified
- edit_note could benefit from preview mode #ux-improvement
- search_notes needs fuzzy matching for typos #feature-idea
- move_note could auto-suggest folder creation #usability

### 📊 Performance Metrics
- Average write_note time: 0.3s
- Search with 100+ notes: 0.6s
- Project parameter overhead: <0.1s
- Memory usage: [observed levels]

## Relations
- tests [[Basic Memory v0.13.0]]
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
   tags: [bug, testing, v0.13.0, [severity]]
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

1. **Setup Phase** (5 minutes)
   - Verify environment and create test project
   - Record baseline system state
   - Establish performance benchmarks

2. **Core Testing** (15-20 minutes per phase)
   - Execute test scenarios systematically
   - Record observations immediately
   - Note timestamps for performance tracking
   - Explore variations when interesting behaviors occur

3. **Documentation** (5 minutes per phase)
   - Create phase summary note
   - Link related test observations
   - Update running issues list
   - Record enhancement ideas

4. **Analysis Phase** (10 minutes)
   - Review all observations across phases
   - Identify patterns and trends
   - Create comprehensive summary report
   - Generate development recommendations

## Testing Success Criteria

### Core Testing (Tier 1) - Must Pass
- All 6 critical tools function correctly
- No critical bugs in essential workflows
- Acceptable performance for basic operations
- Error handling works as expected

### Feature Testing (Tier 1+2) - Should Pass
- All 11 core + important tools function
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