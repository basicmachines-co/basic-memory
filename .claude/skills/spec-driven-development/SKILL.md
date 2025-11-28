---
name: spec-driven-development
description: Guide implementation based on specs stored in Basic Memory, following the SPEC-1 specification-driven development process
---

# Spec-Driven Development

This skill guides implementation work based on specifications stored in the Basic Memory "specs" project, following the process defined in SPEC-1.

## When to Use

Use this skill when:
- Implementing a feature defined by a spec
- Creating a new specification before implementation
- Reviewing implementation against spec criteria
- Need to understand what a spec requires
- Updating spec progress as work completes

## The Spec-Driven Process

From SPEC-1, the workflow is:

1. **Create** - Write spec as complete thought in Basic Memory "specs" project
2. **Discuss** - Iterate and refine the specification
3. **Implement** - Execute implementation directly
4. **Validate** - Review implementation against spec criteria
5. **Document** - Update spec with learnings and decisions

## Spec Structure

Every spec contains:
- **Why** - The reasoning and problem being solved
- **What** - What is affected or changed
- **How** - High-level approach to implementation
- **How to Evaluate** - Testing/validation procedure

### Progress Tracking Format

Specs use living documentation with checklists:

```markdown
### Feature Area
- ✅ Basic functionality implemented
- ✅ Props and events defined
- [ ] Add sorting controls
- [ ] Improve accessibility
- [x] Currently implementing responsive design
```

- `✅` - Completed items
- `[ ]` - Pending items
- `[x]` - In-progress items

## Working with Specs

### Reading a Spec

```python
# Get the full spec
mcp__basic-memory__read_note(
    identifier="SPEC-24: Postgres Database Migration",
    project="specs"
)

# Or search for it
mcp__basic-memory__search_notes(
    query="postgres migration",
    project="specs"
)
```

### Creating a New Spec

```python
# 1. First, find the next spec number
mcp__basic-memory__search_notes(
    query="SPEC-",
    project="specs"
)

# 2. Create the spec with proper structure
mcp__basic-memory__write_note(
    title="SPEC-30: Your Feature Name",
    content="""---
title: 'SPEC-30: Your Feature Name'
type: spec
tags:
- feature-area
- component
---

# SPEC-30: Your Feature Name

## Why

[Problem statement and motivation]

## What

[What is affected or changed]
- Affected areas
- Components involved
- Scope boundaries

## How (High Level)

[Implementation approach]

### Phase 1: Foundation
- [ ] Task 1
- [ ] Task 2

### Phase 2: Core Features
- [ ] Task 3
- [ ] Task 4

## How to Evaluate

### Success Criteria
- [ ] Criterion 1
- [ ] Criterion 2

### Testing Procedure
1. Step 1
2. Step 2

## Observations

- [goal] Primary objective #tag
- [constraint] Known limitation #tag

## Relations

- relates-to [[Related Spec]]
- depends-on [[Dependency]]
""",
    folder="",  # Root of specs project
    project="specs"
)
```

### Updating Spec Progress

```python
# Mark items complete as you implement
mcp__basic-memory__edit_note(
    identifier="SPEC-24: Postgres Database Migration",
    operation="find_replace",
    find_text="- [ ] Create migration scripts",
    content="- ✅ Create migration scripts",
    project="specs"
)

# Or add new observations
mcp__basic-memory__edit_note(
    identifier="SPEC-24: Postgres Database Migration",
    operation="append",
    content="\n- [learning] Alembic autogenerate works well for model changes #migration",
    project="specs"
)
```

### Reviewing Implementation

When reviewing against a spec:

1. **Read the spec's "How to Evaluate" section**
2. **Check each success criterion:**
   - Functional completeness
   - Test coverage (count test files, check categories)
   - Code quality (TypeScript, linting, performance)
   - Architecture compliance
   - Documentation completeness
3. **Be honest** - Don't overstate completeness
4. **Document findings** - Update spec with review results
5. **Identify gaps** - Clearly note what still needs work

## Implementation Workflow

### Starting Implementation

1. **Read the spec thoroughly**
   ```python
   mcp__basic-memory__read_note(
       identifier="SPEC-XX: Feature Name",
       project="specs"
   )
   ```

2. **Understand dependencies**
   - Check Relations section for dependencies
   - Read related specs if needed

3. **Plan your approach**
   - Break "How" section into concrete tasks
   - Identify what to implement first

4. **Mark first item in-progress**
   ```python
   mcp__basic-memory__edit_note(
       identifier="SPEC-XX",
       operation="find_replace",
       find_text="- [ ] First task",
       content="- [x] First task",
       project="specs"
   )
   ```

### During Implementation

1. **Update progress as you complete items**
2. **Add observations for decisions made**
3. **Note any deviations from the spec**
4. **Capture learnings that might help future specs**

### After Implementation

1. **Run full evaluation against criteria**
2. **Mark all completed items with ✅**
3. **Add final observations**
4. **Document any follow-up work needed**

## Spec Naming Convention

Format: `SPEC-X: Descriptive Title`

Examples:
- `SPEC-24: Postgres Database Migration`
- `SPEC-25: Cloud Index Service`
- `SPEC-26: Multi-User Security and Permissions`

## Common Spec Patterns

### Feature Spec
```markdown
## Why
User need or problem

## What
- New UI components
- API endpoints
- Database changes

## How
Implementation phases with checkboxes
```

### Architecture Spec
```markdown
## Why
Technical debt or scalability need

## What
- System components affected
- Data flow changes
- Integration points

## How
Migration strategy with rollback plan
```

### Process Spec
```markdown
## Why
Workflow improvement need

## What
- Process steps changed
- Tools involved
- Team impact

## How
Rollout plan and adoption strategy
```

## Best Practices

1. **Spec first, code second** - Write spec before implementation
2. **Keep specs living** - Update as understanding evolves
3. **Be specific in criteria** - Vague criteria = vague completion
4. **Link related specs** - Build the knowledge graph
5. **Capture decisions** - Future you will thank you
6. **Review honestly** - Incomplete is okay, dishonest isn't
7. **Close the loop** - Mark items done as you complete them

## Using with Slash Commands

The `/spec` command provides quick access:
- `/spec create [name]` - Create new specification
- `/spec status` - Show all spec statuses
- `/spec show [name]` - Read a specific spec
- `/spec review [name]` - Validate implementation
