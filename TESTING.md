# Manual Testing Suite for Basic Memory

This document outlines a comprehensive manual testing approach where an AI assistant (Claude) executes real-world usage scenarios using Basic Memory's MCP tools. The unique aspect: **Basic Memory tests itself** - all test observations and results are recorded as notes in a dedicated test project.

## Philosophy

- **Integration over Isolation**: Test the full MCP→API→DB→File stack
- **Real Usage Patterns**: Creative exploration, not just checklist validation
- **Self-Documenting**: Use Basic Memory to record all test observations
- **Living Documentation**: Test results become part of the knowledge base

## Setup Instructions

### 1. Environment Preparation

```bash
# Ensure latest basic-memory is installed
pip install --upgrade basic-memory

# Verify MCP server is available
basic-memory --version
```

### 2. MCP Integration Setup

**Option A: Claude Desktop Integration**
```json
// Add to ~/.config/claude-desktop/claude_desktop_config.json
// or 
// .mcp.json
{
  "mcpServers": {
    "basic-memory": {
      "command": "uv",
      "args": [
        "--directory",
        "/Users/phernandez/dev/basicmachines/basic-memory",
        "run",
        "src/basic_memory/cli/main.py",
        "mcp"
      ]
    }
  }
}
```

**Option B: Claude Code MCP**
```bash
claude mcp add basic-memory basic-memory mcp
```

### 3. Test Project Creation

During testing, create a dedicated test project:
```
- Project name: "basic-memory-testing"
- Location: ~/basic-memory-testing
- Purpose: Contains all test observations and results
```

## Testing Categories

### Phase 1: Core Functionality Validation

**Objective**: Verify all basic operations work correctly

**Test Areas:**
- [ ] **Note Creation**: Various content types, structures, frontmatter
- [ ] **Note Reading**: By title, path, memory:// URLs, non-existent notes
- [ ] **Search Operations**: Simple queries, boolean operators, tag searches
- [ ] **Context Building**: Different depths, timeframes, relation traversal
- [ ] **Recent Activity**: Various timeframes, filtering options

**Success Criteria:**
- All operations complete without errors
- Files appear correctly in filesystem
- Search returns expected results
- Context includes appropriate related content

**Observations to Record:**
```markdown
# Core Functionality Test Results

## Test Execution
- [timestamp] Test started at 2025-01-06 15:30:00
- [setup] Created test project successfully
- [environment] MCP connection established

## write_note Tests
- [success] Basic note creation works
- [success] Frontmatter tags are preserved
- [issue] Special characters in titles need investigation

## Relations
- validates [[Search Operations Test]]
- part_of [[Manual Testing Suite]]
```

### Phase 2: v0.13.0 Feature Deep Dive

**Objective**: Thoroughly test new project management and editing capabilities

**Project Management Tests:**
- [ ] Create multiple projects dynamically
- [ ] Switch between projects mid-conversation
- [ ] Cross-project operations (create notes in different projects)
- [ ] Project discovery and status checking
- [ ] Default project behavior

**Note Editing Tests:**
- [ ] Append operations (add content to end)
- [ ] Prepend operations (add content to beginning)
- [ ] Find/replace operations with validation
- [ ] Section replacement under headers
- [ ] Edit operations across different projects

**File Management Tests:**
- [ ] Move notes within same project
- [ ] Move notes between projects
- [ ] Automatic folder creation during moves
- [ ] Move operations with special characters
- [ ] Database consistency after moves

**Success Criteria:**
- Project switching preserves context correctly
- Edit operations modify files as expected
- Move operations maintain database consistency
- Search indexes update after moves and edits

### Phase 3: Edge Case Exploration

**Objective**: Discover limits and handle unusual scenarios gracefully

**Boundary Testing:**
- [ ] Very long note titles and content
- [ ] Empty notes and projects
- [ ] Special characters: unicode, emojis, symbols
- [ ] Deeply nested folder structures
- [ ] Circular relations and self-references

**Error Scenario Testing:**
- [ ] Invalid memory:// URLs
- [ ] Missing files referenced in database
- [ ] Concurrent operations (if possible)
- [ ] Invalid project names
- [ ] Disk space constraints (if applicable)

**Performance Testing:**
- [ ] Large numbers of notes (100+)
- [ ] Complex search queries
- [ ] Deep relation chains (5+ levels)
- [ ] Rapid successive operations

### Phase 4: Real-World Workflow Scenarios

**Objective**: Test realistic usage patterns that users might follow

**Scenario 1: Meeting Notes Pipeline**
1. Create meeting notes with action items
2. Extract action items into separate notes
3. Link to project planning documents
4. Update progress over time using edit operations
5. Archive completed items

**Scenario 2: Research Knowledge Building**
1. Create research topic notes
2. Build complex relation networks
3. Add incremental findings over time
4. Search and discover connections
5. Reorganize as knowledge grows

**Scenario 3: Multi-Project Workflow**
1. Work project: Technical documentation
2. Personal project: Recipe collection
3. Learning project: Course notes
4. Switch between projects during conversation
5. Cross-reference related concepts

**Scenario 4: Content Evolution**
1. Start with basic notes
2. Gradually enhance with relations
3. Reorganize file structure
4. Update existing content incrementally
5. Build comprehensive knowledge graph

### Phase 5: Creative Stress Testing

**Objective**: Push the system to discover unexpected behaviors

**Creative Exploration Areas:**
- [ ] Rapid project creation and switching
- [ ] Unusual but valid markdown structures
- [ ] Creative use of observation categories
- [ ] Novel relation types and patterns
- [ ] Combining tools in unexpected ways

**Stress Scenarios:**
- [ ] Bulk operations (create many notes quickly)
- [ ] Complex nested moves and edits
- [ ] Deep context building with large graphs
- [ ] Search with complex boolean expressions

## Test Execution Process

### Pre-Test Checklist
- [ ] MCP connection verified
- [ ] Test project created
- [ ] Baseline notes recorded

### During Testing
1. **Execute test scenarios** using actual MCP tool calls
2. **Record observations** immediately in test project
3. **Note timestamps** for performance tracking
4. **Document any errors** with reproduction steps
5. **Explore variations** when something interesting happens

### Test Observation Format

Record all observations as Basic Memory notes using this structure:

```markdown
---
title: Test Session YYYY-MM-DD HH:MM
tags: [testing, session, v0.13.0]
---

# Test Session YYYY-MM-DD HH:MM

## Test Focus
- Primary objective
- Features being tested

## Observations
- [success] Feature X worked as expected #functionality
- [performance] Operation Y took 2.3 seconds #timing
- [issue] Error with special characters #bug
- [enhancement] Could improve UX for scenario Z #improvement

## Discovered Issues
- [bug] Description of problem with reproduction steps
- [limitation] Current system boundary encountered

## Relations
- tests [[Feature X]]
- part_of [[Manual Testing Suite]]
- found_issue [[Bug Report: Special Characters]]
```

### Post-Test Analysis
- [ ] Review all test observations
- [ ] Create summary report with findings
- [ ] Identify patterns in successes/failures
- [ ] Generate improvement recommendations

## Success Metrics

**Quantitative Measures:**
- % of test scenarios completed successfully
- Number of bugs discovered and documented
- Performance benchmarks established
- Coverage of all MCP tools and operations

**Qualitative Measures:**
- Natural conversation flow maintained
- Knowledge graph quality and connections
- User experience insights captured
- System reliability under various conditions

## Expected Outcomes

**For the System:**
- Validation of v0.13.0 features in real usage
- Discovery of edge cases not covered by unit tests
- Performance baseline establishment
- Bug identification with reproduction cases

**For the Knowledge Base:**
- Comprehensive testing documentation
- Real usage examples for documentation
- Edge case scenarios for future reference
- Performance insights and optimization opportunities

**For Development:**
- Priority list for bug fixes
- Enhancement ideas from real usage
- Validation of architectural decisions
- User experience insights

## Test Reporting

All test results will be captured in the Basic Memory test project, creating a living knowledge base of:

- Test execution logs with detailed observations
- Bug reports with reproduction steps
- Performance benchmarks and timing data
- Feature enhancement ideas discovered during testing
- Knowledge graphs showing test coverage relationships
- Summary reports for development team review

This approach ensures that the testing process itself validates Basic Memory's core value proposition: effectively capturing, organizing, and connecting knowledge through natural interaction patterns.

## Things to note

### User Experience & Usability:
- are tool instructions clear with working examples?
- Do error messages provide actionable guidance for resolution?
- Are response times acceptable for interactive use?
- Do tools feel consistent in their parameter patterns and behavior?
- Can users easily discover what tools are available and their capabilities?

### System Behavior:
- Does context preservation work as expected across tool calls?
- Do memory:// URLs behave intuitively for knowledge navigation?
- How well do tools work together in multi-step workflows?
- Does the system gracefully handle edge cases and invalid inputs?

### Documentation Alignment:
- does tool output provide clear results and helpful information?
- Do actual tool behaviors match their documented descriptions?
- Are the examples in tool help accurate and useful?
- Do real-world usage patterns align with documented workflows?

### Mental Model Validation:
- Does the system work the way users would naturally expect?
- Are there surprising behaviors that break user assumptions?
- Can users easily recover from mistakes or wrong turns?
- Do the knowledge graph concepts (entities, relations, observations) feel natural?

### Performance & Reliability:
- Do operations complete in reasonable time for the data size?
- Is system behavior consistent across multiple test sessions?
- How does performance change as the knowledge base grows?
- Are there any operations that feel unexpectedly slow?

---

**Ready to begin testing?** Start by creating the test project and recording your first observation about the testing setup process itself.