---
title: 'SPEC-2: Slash Commands Reference'
type: spec
permalink: specs/spec-2-slash-commands-reference
tags:
- commands
- process
- reference
---

# SPEC-2: Slash Commands Reference

This document defines the slash commands used in our specification-driven development process.

## /spec create [name]

**Purpose**: Create a new specification document

**Usage**: `/spec create notes-decomposition`

**Process**:
1. Create new spec document in `/specs` folder
2. Use SPEC-XXX numbering format (auto-increment)
3. Include standard spec template:
   - Why (reasoning/problem)
   - What (affected areas)
   - How (high-level approach)
   - How to Evaluate (testing/validation)
4. Tag appropriately for knowledge graph
5. Link to related specs/components

**Template**:
```markdown
# SPEC-XXX: [Title]

## Why
[Problem statement and reasoning]

## What
[What is affected or changed]

## How (High Level)
[Approach to implementation]

## How to Evaluate
[Testing/validation procedure]

## Notes
[Additional context as needed]
```

## /spec status

**Purpose**: Show current status of all specifications

**Usage**: `/spec status`

**Process**:
1. Search all specs in `/specs` folder
2. Display table showing:
   - Spec number and title
   - Status (draft, approved, implementing, complete)
   - Assigned agent (if any)
   - Last updated
   - Dependencies

## /spec implement [name]

**Purpose**: Hand specification to appropriate agent for implementation

**Usage**: `/spec implement SPEC-002`

**Process**:
1. Read the specified spec
2. Analyze requirements to determine appropriate agent:
   - Frontend components → vue-developer
   - Architecture/system design → system-architect  
   - Backend/API → python-developer
3. Launch agent with spec context
4. Agent creates implementation plan
5. Update spec with implementation status

## /spec review [name]

**Purpose**: Review implementation against specification criteria

**Usage**: `/spec review SPEC-002`

**Process**:
1. Read original spec and "How to Evaluate" section
2. Examine current implementation
3. Test against success criteria
4. Document gaps or issues
5. Update spec with review results
6. Recommend next actions (complete, revise, iterate)

## Command Extensions

As the process evolves, we may add:
- `/spec link [spec1] [spec2]` - Create dependency links
- `/spec archive [name]` - Archive completed specs
- `/spec template [type]` - Create spec from template
- `/spec search [query]` - Search spec content

## References

- Claude Slash commands: https://docs.anthropic.com/en/docs/claude-code/slash-commands

## Creating a command

Commands are implemented as Claude slash commands: 

Location in repo: .claude/commands/

In the following example, we create the /optimize command:
```bash
# Create a project command
mkdir -p .claude/commands
echo "Analyze this code for performance issues and suggest optimizations:" > .claude/commands/optimize.md
```
