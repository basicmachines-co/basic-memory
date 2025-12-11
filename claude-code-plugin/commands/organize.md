---
description: Organize and maintain your Basic Memory knowledge graph
argument-hint: [health|orphans|duplicates|relations|tags] [project]
allowed-tools: mcp__basic-memory__search_notes, mcp__basic-memory__read_note, mcp__basic-memory__list_directory, mcp__basic-memory__edit_note, mcp__basic-memory__write_note
---

# Organize

Help organize, link, and maintain your Basic Memory knowledge graph.

## Arguments

- `$1` - Action (optional): `health`, `orphans`, `duplicates`, `relations`, `tags` (default: `health`)
- `$2` - Project (optional): defaults to "main"

## Actions

### `/organize` or `/organize health`

Run a quick health check:
1. Count total notes
2. Identify orphan notes (no relations)
3. Check for potential duplicates
4. Show folder distribution
5. Report any issues found

### `/organize orphans`

Find and address orphan notes:
1. Search for notes with empty Relations sections
2. List orphans found
3. For each orphan, suggest potential relations based on content
4. Offer to add relations or create index notes

### `/organize duplicates`

Find potentially duplicate notes:
1. Search for notes with similar titles
2. Compare content for overlap
3. Suggest: merge, differentiate, or link with `supersedes`

### `/organize relations [note-title]`

Suggest relations for a specific note (or recent notes if not specified):
1. Read the target note
2. Search for related content
3. Suggest relation types:
   - `relates-to` - General connection
   - `extends` - Builds upon
   - `implements` - Realizes concept
   - `depends-on` - Requires understanding of
4. Offer to add selected relations

### `/organize tags`

Review tag consistency:
1. Gather all tags across notes
2. Find similar/duplicate tags (e.g., `arch` vs `architecture`)
3. Identify over-used or under-used tags
4. Suggest normalization

## Your Task

Execute: `/organize $ARGUMENTS`

Based on the action requested:

1. **Gather data** using search and list tools
2. **Analyze** for the specific issue (orphans, duplicates, etc.)
3. **Present findings** clearly with counts and examples
4. **Offer solutions** - ask before making changes
5. **Apply fixes** using edit_note or write_note when user approves

Always confirm before modifying notes. Show what will change and get approval.

## Examples

```
/organize                    # Quick health check
/organize health             # Same as above
/organize orphans            # Find unlinked notes
/organize duplicates         # Find similar notes
/organize relations          # Suggest links for recent notes
/organize relations "My Note" # Suggest links for specific note
/organize tags               # Review tag consistency
/organize health specs       # Health check on specs project
```
