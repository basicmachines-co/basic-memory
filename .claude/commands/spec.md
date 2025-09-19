---
allowed-tools: mcp__basic-memory__write_note, mcp__basic-memory__read_note, mcp__basic-memory__search_notes, mcp__basic-memory__edit_note, Task
argument-hint: [create|status|implement|review] [spec-name]
description: Manage specifications in our development process
---

## Context

You are managing specifications using our specification-driven development process defined in @docs/specs/SPEC-001.md.

Available commands:
- `create [name]` - Create new specification
- `status` - Show all spec statuses
- `implement [spec-name]` - Hand spec to appropriate agent
- `review [spec-name]` - Review implementation against spec

## Your task

Execute the spec command: `/spec $ARGUMENTS`

### If command is "create":
1. Get next SPEC number by searching existing specs
2. Create new spec using template from @docs/specs/Slash\ Commands\ Reference.md
3. Place in `/specs` folder with title "SPEC-XXX: [name]"
4. Include standard sections: Why, What, How, How to Evaluate

### If command is "status":
1. Search all notes in `/specs` folder
2. Display table with spec number, title, and status
3. Show any dependencies or assigned agents

### If command is "implement":
1. Read the specified spec
2. Determine appropriate agent based on content:
   - Frontend/UI → vue-developer
   - Architecture/system → system-architect  
   - Backend/API → python-developer
3. Launch Task tool with appropriate agent and spec context

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
4. Document findings and update spec with review results
5. If gaps found, clearly identify what still needs to be implemented/tested

Use the agent definitions from @docs/specs/Agent\ Definitions.md for implementation handoffs.
