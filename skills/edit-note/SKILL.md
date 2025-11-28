---
name: edit-note
description: Interactively edit Basic Memory notes using MCP tools - view, modify, and update notes in a conversational workflow (works with cloud and local)
---

# Edit Note

This skill enables interactive editing of Basic Memory notes using MCP tools. It works with both Basic Memory Cloud and local installations since it operates through the MCP interface rather than direct file access.

## When to Use

Use this skill when:
- User wants to edit an existing note
- User asks to update, change, or modify note content
- User wants to refine observations or relations in a note
- User says things like "edit my note about...", "update the...", "change X to Y in..."

## Editing Workflow

### 1. Fetch the Current Note

First, retrieve the note to show the user what exists:

```python
mcp__basic-memory__read_note(
    identifier="Note Title or permalink",
    project="main"  # or specified project
)
```

Present the note content clearly, highlighting:
- Current title and metadata
- Main content sections
- Observations (with categories)
- Relations (with link targets)

### 2. Understand the Edit Request

Ask clarifying questions if needed:
- Which section to modify?
- What specifically to change?
- Add new content or replace existing?

### 3. Apply the Edit

Use the appropriate `edit_note` operation:

**Append** - Add content to the end:
```python
mcp__basic-memory__edit_note(
    identifier="note-title",
    operation="append",
    content="\n\n## New Section\n\nNew content here...",
    project="main"
)
```

**Prepend** - Add content to the beginning:
```python
mcp__basic-memory__edit_note(
    identifier="note-title",
    operation="prepend",
    content="# Updated Header\n\n",
    project="main"
)
```

**Find and Replace** - Replace specific text:
```python
mcp__basic-memory__edit_note(
    identifier="note-title",
    operation="find_replace",
    find_text="old text to find",
    content="new replacement text",
    project="main"
)
```

**Replace Section** - Replace an entire section by heading:
```python
mcp__basic-memory__edit_note(
    identifier="note-title",
    operation="replace_section",
    section="## Section Heading",
    content="## Section Heading\n\nCompletely new section content...",
    project="main"
)
```

### 4. Show the Result

After editing, fetch and display the updated note:

```python
mcp__basic-memory__read_note(
    identifier="note-title",
    project="main"
)
```

Highlight what changed so the user can verify.

## Edit Operations Reference

| Operation | Use Case | Required Parameters |
|-----------|----------|---------------------|
| `append` | Add to end | `content` |
| `prepend` | Add to beginning | `content` |
| `find_replace` | Change specific text | `find_text`, `content` |
| `replace_section` | Rewrite a section | `section`, `content` |

## Common Edit Patterns

### Adding a New Observation

```python
mcp__basic-memory__edit_note(
    identifier="note-title",
    operation="find_replace",
    find_text="## Observations",
    content="## Observations\n\n- [new-category] New observation here #tag",
    project="main"
)
```

Or append to observations section:
```python
mcp__basic-memory__edit_note(
    identifier="note-title",
    operation="append",
    content="\n- [insight] Additional insight discovered #tag",
    project="main"
)
```

### Adding a New Relation

```python
mcp__basic-memory__edit_note(
    identifier="note-title",
    operation="find_replace",
    find_text="## Relations",
    content="## Relations\n\n- relates-to [[New Related Note]]",
    project="main"
)
```

### Updating a Specific Observation

```python
mcp__basic-memory__edit_note(
    identifier="note-title",
    operation="find_replace",
    find_text="- [decision] Old decision text",
    content="- [decision] Updated decision with new context #updated",
    project="main"
)
```

### Rewriting the Context Section

```python
mcp__basic-memory__edit_note(
    identifier="note-title",
    operation="replace_section",
    section="## Context",
    content="## Context\n\nCompletely rewritten context explaining the new situation...",
    project="main"
)
```

## Multi-Step Editing Session

For complex edits, work iteratively:

1. **Show current state** → Read and display the note
2. **First edit** → Apply one change
3. **Show result** → Display updated note
4. **Next edit** → Apply another change if needed
5. **Confirm complete** → Final display and confirmation

This keeps the user informed and allows course correction.

## Best Practices

1. **Always show before and after** - User should see what changed
2. **One edit at a time** - For complex changes, do multiple operations
3. **Preserve structure** - Maintain the note's markdown format
4. **Be careful with find_replace** - Ensure the find_text is unique
5. **Confirm destructive changes** - Ask before replacing large sections
6. **Keep observations formatted** - Maintain `[category]` prefix format
7. **Keep relations formatted** - Maintain `- relation-type [[Target]]` format

## Example Conversation

**User:** "Edit my note about the async client pattern - add an observation about testing"

**Claude:**
1. Fetches "Async Client Pattern" note
2. Displays current content
3. Asks: "What observation about testing would you like to add?"

**User:** "That the context manager pattern makes mocking easier in tests"

**Claude:**
1. Uses `edit_note` with `append` to add:
   `- [testing] Context manager pattern simplifies mocking in unit tests #testability`
2. Fetches and displays updated note
3. Confirms: "Added the testing observation. Here's the updated note..."
