---
name: memory-curate
description: "Curate the Basic Memory knowledge graph: find orphan notes and suggest links, propose typed relations, merge duplicates, audit tags and folders, and build hub notes. Use to organize, connect, and improve a knowledge base as notes accumulate."
---

# Memory Curate

Maintain a healthy, well-connected knowledge graph. As notes accumulate, it pays to periodically organize, link, and curate the knowledge base so isolated notes become a connected graph.

This skill curates the **knowledge graph** — the notes, relations, and tags that make up the knowledge base. (For hygiene on an agent's own memory *files* — splitting bloated files, pruning stale entries — see **memory-defrag**.)

## When to Use

- Asked to organize, clean up, or improve the knowledge base
- Asked to find connections between notes, or what isn't linked yet
- Orphan or unlinked notes are mentioned
- Asked about duplicate or similar notes
- Asked for help with folder organization or tag consistency
- Phrases like "help me organize", "find related notes", "what's not linked", "clean up my notes"

## Curation Capabilities

### 1. Find Orphan Notes

Orphans have no relations to other notes — they're islands in the graph.

```python
# List notes, then read each to inspect its Relations section
search_notes(query="*", page_size=50)
read_note(identifier="note-to-check")
# Orphans have an empty (or missing) Relations section
```

If the `bm` CLI is available, `bm orphans` lists notes with no relations in one call.

**What to do with orphans:**
- Suggest relations based on content similarity
- Ask whether they should connect to existing topics
- Propose hub notes to gather related orphans (see capability 6)

### 2. Suggest Typed Relations

Analyze a note's content and propose meaningful connections.

```python
read_note(identifier="note-to-analyze")
# Pull out key terms, then search for related notes
search_notes(query="key terms from the note")
```

Suggest relations based on shared topics, complementary content (problem/solution,
question/answer), sequence (part 1 → part 2), or hierarchy (parent concept → detail).

**Relation-type vocabulary:**
- `relates_to` — general topical connection
- `extends` — builds upon or elaborates
- `implements` — realizes a concept or spec
- `depends_on` — requires understanding of
- `part_of` — hierarchy or composition
- `contrasts_with` — presents an alternative view
- `inspired_by` — source of insight
- `enables` — makes something possible

Custom relation types are fine — use whatever verb is descriptive.

Add a confirmed relation with `edit_note`:

```python
edit_note(
    identifier="API Design Decisions",
    operation="insert_after_section",
    section="Relations",
    content="- depends_on [[Rate Limiter]]",
)
```

### 3. Identify Similar / Duplicate Notes

Find notes that may cover the same ground.

```python
search_notes(query="topic keywords")
# Compare results for: similar titles, overlapping observations,
# shared tags, close-together timestamps
```

**Actions for duplicates:**
- **Merge** into a single comprehensive note, then redirect the loser with a relation
- Link with `supersedes` / `updates` when one revises the other
- **Differentiate** by adding context that clarifies each note's distinct focus

```python
# Point an older note at the one that replaces it
edit_note(
    identifier="DB Schema v1",
    operation="insert_after_section",
    section="Relations",
    content="- updates [[DB Schema v2]]",
)
```

### 4. Folder Organization Review

```python
list_directory(dir_name="/", depth=3)
```

Look for overcrowded folders, single-note folders, inconsistent naming, and notes
that belong elsewhere. Suggest grouping related notes into topic folders, adding
subfolders for large categories, and a consistent naming convention. Move misplaced
notes with `move_note`. By default the permalink stays the same after a move, so links keep resolving. Projects with `update_permalinks_on_move` enabled rewrite it from the new path.

```python
move_note(
    identifier="API Design Decisions",
    destination_path="architecture/api-design-decisions.md",
)
```

### 5. Tag Consistency

```python
search_notes(query="*", page_size=100)
# Inspect tag patterns across results
```

Look for:
- **Variant tags** — `architecture` vs `arch`; pick one and standardize
- **Unused tags** — present on a single note, no longer carrying weight
- **Over-used generic tags** — so broad they don't aid discovery
- **Missing tags** — relevant notes lacking an obvious tag

### 6. Create Index / Hub Notes

After finding a cluster of related notes, build a navigation hub.

```python
write_note(
    title="Architecture Decisions Index",
    directory="indexes",
    tags=["architecture", "index"],
    note_type="index",
    content="""# Architecture Decisions Index

A hub linking architecture-related decisions and patterns.

## Decisions
- [[Database Selection Decision]]
- [[API Design Patterns]]
- [[Authentication Architecture]]

## Patterns
- [[Repository Pattern]]
- [[Async Client Pattern]]

## Observations
- [index] Central hub for architecture knowledge #navigation

## Relations
- indexes [[Architecture]]""",
)
```

### 7. Enrich Sparse Notes

Find notes lacking structure and fill them in.

```python
read_note(identifier="sparse-note")
```

If the note is missing an Observations section, suggest categories. If it has no
Relations, suggest links. If it has no tags, suggest relevant ones. If it lacks
context, suggest adding background. Apply with `edit_note`.

## Curation Workflows

Scale the pass to the request. A health check reports orphans, likely duplicates,
recent activity (`recent_activity`), and folder distribution. A deep session works
through the capabilities above in whatever order the findings suggest. A topic pass
maps one subject with `search_notes` and `build_context`, fills the gaps it finds, and
ends with a hub note.

## Best Practices

1. **Work incrementally.** Don't reorganize everything at once.
2. **Confirm before changing.** Always ask before moving, merging, or editing notes.
3. **Preserve permalinks.** Changing a permalink breaks inbound `memory://` links. Moves keep the permalink by default (unless the project enables `update_permalinks_on_move`).
4. **Explain suggestions.** Say *why* a relation or merge makes sense.
5. **Respect the existing system.** Enhance the user's organization — don't impose a new taxonomy.
6. **Show the graph.** Use `build_context` to help the user see how notes connect.

## Example Conversations

**User:** "Help me organize my notes"

The assistant:
1. Runs a health check on the knowledge base
2. Reports: "You have 47 notes. I found 12 orphans and 3 potential duplicates."
3. Asks: "Want to start by connecting the orphans, or review the duplicates first?"

**User:** "Find notes that should link to my API design note"

The assistant:
1. Reads the API design note
2. Searches for related content
3. Suggests: "5 notes could relate —
   - 'REST Best Practices' → `relates_to`
   - 'Authentication Flow' → `implements`
   - 'Rate Limiting Decision' → `extends`
   Should I add any of these relations?"

**User:** "Are there notes on similar topics?"

The assistant:
1. Analyzes titles and content for clusters
2. Reports: "Possible overlaps —
   - 'Auth Flow' and 'Authentication Design' cover similar ground
   - 'DB Schema v1' and 'DB Schema v2' likely want a `supersedes` relation
   Want to review either?"
