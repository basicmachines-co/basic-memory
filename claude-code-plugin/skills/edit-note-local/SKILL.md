---
name: edit-note-local
description: Edit Basic Memory notes directly as local files - enables full file editing with automatic sync (local installations only)
---

# Edit Note Local

This skill enables direct file-based editing of Basic Memory notes. It works by editing the actual markdown files in the knowledge base, which Basic Memory's sync service automatically picks up. This provides a more seamless editing experience for local installations.

## When to Use

Use this skill when:
- User has a local Basic Memory installation (not cloud-only)
- User wants to make substantial edits to a note
- User prefers working with the full file content
- User wants changes to sync automatically via `basic-memory sync --watch`

**Note:** This skill requires local file access. For cloud-only users, use the `edit-note` skill instead.

## Editing Workflow

### 1. Find the Note's File Path

First, get the note metadata to find its file location:

```python
# Search for the note
mcp__basic-memory__search_notes(
    query="note title or keywords",
    project="main"
)

# Read the note to get file_path from metadata
mcp__basic-memory__read_note(
    identifier="note-title",
    project="main"
)
```

The response includes `file_path` which gives the relative path within the knowledge base.

### 2. Determine Full File Path

Basic Memory projects have a root directory. Common locations:
- Default: `~/basic-memory/`
- Custom: Check project configuration

Construct the full path:
```
{project_root}/{file_path}
```

For example:
- Project root: `/Users/username/basic-memory`
- File path from note: `notes/My Note.md`
- Full path: `/Users/username/basic-memory/notes/My Note.md`

### 3. Read the File

Use Claude Code's Read tool to get the full file content:

```python
Read(file_path="/Users/username/basic-memory/notes/My Note.md")
```

Display the content to the user, explaining the structure:
- Frontmatter (YAML between `---` markers)
- Main content
- Observations section
- Relations section

### 4. Edit the File

Use Claude Code's Edit tool for precise changes:

```python
Edit(
    file_path="/Users/username/basic-memory/notes/My Note.md",
    old_string="text to replace",
    new_string="new text"
)
```

Or use Write for complete rewrites:

```python
Write(
    file_path="/Users/username/basic-memory/notes/My Note.md",
    content="Complete new file content..."
)
```

### 5. Sync Happens Automatically

If the user has `basic-memory sync --watch` running, changes are picked up automatically. Otherwise, they can run:
```bash
basic-memory sync
```

## File Structure Reference

Basic Memory notes follow this markdown structure:

```markdown
---
title: Note Title
type: note
permalink: note-title
tags:
- tag1
- tag2
---

# Note Title

## Context

Background and situation explanation.

## Main Content

The primary content of the note...

## Observations

- [category] Observation text #optional-tag
- [decision] A decision that was made #tag
- [insight] An insight discovered

## Relations

- relates-to [[Other Note]]
- implements [[Parent Concept]]
- learned-from [[Source Note]]
```

## Editing Patterns

### Edit Frontmatter Tags

```python
Edit(
    file_path="/path/to/note.md",
    old_string="tags:\n- old-tag",
    new_string="tags:\n- old-tag\n- new-tag"
)
```

### Add New Section

```python
Edit(
    file_path="/path/to/note.md",
    old_string="## Observations",
    new_string="## New Section\n\nNew content here.\n\n## Observations"
)
```

### Modify Observation

```python
Edit(
    file_path="/path/to/note.md",
    old_string="- [decision] Old decision",
    new_string="- [decision] Updated decision with new info #updated"
)
```

### Add Relation

```python
Edit(
    file_path="/path/to/note.md",
    old_string="## Relations\n",
    new_string="## Relations\n\n- relates-to [[New Related Note]]\n"
)
```

### Complete Rewrite

For major changes, read the file, construct new content preserving the frontmatter structure, and write:

```python
Write(
    file_path="/path/to/note.md",
    content="""---
title: Note Title
type: note
permalink: note-title
tags:
- updated
---

# Note Title

Completely rewritten content...

## Observations

- [rewrite] Complete rewrite of this note #major-update

## Relations

- updates [[Previous Version]]
"""
)
```

## Finding the Project Root

To find where Basic Memory stores files, you can:

1. **Check common locations:**
   - `~/basic-memory/`
   - `~/Documents/basic-memory/`
   - Current working directory

2. **Use the list_directory tool:**
   ```python
   mcp__basic-memory__list_directory(
       dir_name="/",
       project="main"
   )
   ```

3. **Ask the user:** "Where is your Basic Memory knowledge base located?"

## Advantages of Local Editing

1. **Full file access** - Edit any part of the file including frontmatter
2. **Multi-line edits** - Make complex structural changes easily
3. **Batch operations** - Edit multiple files in sequence
4. **Version control** - Changes tracked by git if the folder is a repo
5. **Instant preview** - Use any markdown editor alongside
6. **Auto-sync** - `sync --watch` picks up changes automatically

## Best Practices

1. **Preserve frontmatter** - Don't break the YAML structure
2. **Keep valid markdown** - Maintain proper formatting
3. **Preserve permalinks** - Changing them can break links
4. **Show diffs** - Tell the user what changed
5. **Suggest sync** - Remind about `basic-memory sync` if not watching
6. **Handle missing files** - Check if file exists before editing

## Example Conversation

**User:** "I want to completely rewrite my architecture decision note"

**Claude:**
1. Searches for the note via MCP
2. Gets the file path
3. Reads the current file content
4. Asks: "Here's the current note. What would you like the new version to say?"

**User:** Provides new content

**Claude:**
1. Preserves the frontmatter (title, permalink, type)
2. Writes the new content using Write tool
3. Confirms: "Updated the file at `/path/to/note.md`. If you have sync --watch running, it's already indexed. Otherwise run `basic-memory sync`."
