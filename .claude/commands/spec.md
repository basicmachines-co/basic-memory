---
allowed-tools: mcp__basic-memory__write_note, mcp__basic-memory__read_note, mcp__basic-memory__search_notes, mcp__basic-memory__edit_note
argument-hint: [create|status|show|review] [spec-name]
description: Manage specifications in our development process
---

## Context

Specifications are managed in the Basic Memory "specs" project. All specs live in a centralized location accessible across all repositories via MCP tools.

See SPEC-1 and SPEC-2 in the "specs" project for the full specification-driven development process.

Available commands:
- `create [name]` - Create new specification
- `status` - Show all spec statuses
- `show [spec-name]` - Read a specific spec
- `review [spec-name]` - Review implementation against spec

## Your task

Execute the spec command: `/spec $ARGUMENTS`

### If command is "create":
1. Get next SPEC number by searching existing specs in "specs" project
2. Create new spec using template from SPEC-2
3. Use mcp__basic-memory__write_note with project="specs"
4. Include standard sections: Why, What, How, How to Evaluate

### If command is "status":
1. Use mcp__basic-memory__search_notes with project="specs"
2. Display table with spec number, title, and progress
3. Show completion status from checkboxes in content

### If command is "show":
1. Use mcp__basic-memory__read_note with project="specs"
2. Display the full spec content

### If command is "review":
1. Read the specified spec and its "How to Evaluate" section
2. Review current implementation against success criteria with careful evaluation of:
   - **Functional completeness** - All specified features working
   - **Test coverage analysis** - Actual test files and coverage percentage
     - Count existing test files vs required components/APIs/composables
     - Verify unit tests, integration tests, and end-to-end tests
     - Check for missing test categories (component, API, workflow)
   - **Code quality metrics** - TypeScript compilation, linting, performance
   - **Architecture compliance** - Component isolation, state management patterns
   - **Documentation completeness** - Implementation matches specification
3. Provide honest, accurate assessment - do not overstate completeness
4. Document findings and update spec with review results using mcp__basic-memory__edit_note
5. If gaps found, clearly identify what still needs to be implemented/tested
