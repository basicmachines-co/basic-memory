---
name: memory-defrag
description: "Defragment and reorganize agent memory files: split bloated files, merge duplicates, remove stale information, and restructure the memory hierarchy. Use when memory files have grown unwieldy, contain redundancies, or need reorganization. Run periodically (weekly) or on demand."
---

# Memory Defrag

Reorganize memory files for clarity, efficiency, and relevance. Like filesystem defragmentation but for knowledge.

## When to Run

- **Periodic**: Weekly or biweekly via cron (recommended)
- **On demand**: User asks to clean up, reorganize, or defrag memory
- **Threshold**: When MEMORY.md exceeds ~500 lines or daily notes accumulate without consolidation

## Process

### 1. Audit Current State

Inventory all memory files:
```
MEMORY.md           — long-term memory
memory/             — daily notes, tasks, topical files
memory/tasks/       — active and completed tasks
```

For each file, note: line count, last modified, topic coverage, staleness.

### 2. Identify Problems

Look for these common issues:

| Problem | Signal | Fix |
|---------|--------|-----|
| **Bloated file** | >300 lines, covers many topics | Split into focused files |
| **Duplicate info** | Same fact in multiple places | Consolidate to one location |
| **Stale entries** | References to completed work, old dates, resolved issues | Remove or archive |
| **Orphan files** | Files in memory/ never referenced or updated | Review, merge, or remove |
| **Inconsistencies** | Contradictory information across files | Resolve to ground truth |
| **Poor organization** | Related info scattered across files | Restructure by topic |
| **Recursive nesting** | `memory/memory/memory/...` directories | Delete nested dirs (indexer bug artifact) |

### 3. Plan Changes

Before making edits, write a brief plan:
```markdown
## Defrag Plan
- [ ] Split MEMORY.md "Key People" section → memory/people.md
- [ ] Remove completed tasks older than 30 days from memory/tasks/
- [ ] Merge memory/bm-marketing-ideas.md into memory/competitive/
- [ ] Update stale project status entries in MEMORY.md
```

### 4. Execute

Apply changes one at a time:
- **Split**: Extract sections from large files into focused topical files
- **Merge**: Combine related small files into coherent documents
- **Prune**: Remove information that is no longer relevant or accurate
- **Restructure**: Move files to appropriate directories, rename for clarity
- **Update**: Fix outdated facts, dates, statuses

### 5. Verify & Log

After changes:
- Verify no information was lost (compare before/after)
- Update any cross-references between files
- Log what was done in today's daily note:

```markdown
## Memory Defrag (HH:MM)
- Files reviewed: N
- Split: [list]
- Merged: [list]
- Pruned: [list]
- Net result: X files, Y total lines (was Z lines)
```

## Guidelines

- **Preserve raw daily notes.** Don't delete or modify `memory/YYYY-MM-DD.md` files — they're the audit trail.
- **Target 15-25 focused files.** Too few means bloated files; too many means fragmentation. Aim for the sweet spot.
- **File names should be scannable.** Use descriptive names: `people.md`, `project-status.md`, `competitive-landscape.md` — not `notes-2.md`.
- **Don't over-organize.** One level of directories is usually enough. `memory/tasks/` and `memory/competitive/` are fine; `memory/work/projects/active/basic-memory/notes/` is not.
- **Completed tasks**: Tasks with `status: done` older than 14 days can be removed. Their insights should already be in MEMORY.md via reflection.
- **Ask before destructive changes.** If uncertain whether information is still relevant, keep it with a `(review needed)` tag rather than deleting.
