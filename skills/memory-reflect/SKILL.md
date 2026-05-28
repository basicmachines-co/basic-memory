---
name: memory-reflect
description: "Sleep-time memory reflection: review recent conversations and daily notes, extract insights, and consolidate into long-term memory. Use when triggered by cron, heartbeat, or explicit request to reflect on recent activity. Runs as background processing to improve memory quality over time."
---

# Memory Reflect

Review recent activity and consolidate valuable insights into long-term memory.

Inspired by sleep-time compute — the idea that memory formation happens best *between* active sessions, not during them.

## When to Run

- **Cron/heartbeat**: Schedule as a periodic background task (recommended: 1-2x daily)
- **On demand**: User asks to reflect, consolidate, or review recent memory
- **Post-compaction**: After context window compaction events

## Process

### 1. Gather Recent Material

Find what changed recently, then read the relevant files:

```python
# Find recently modified notes — use json format for the complete list
# (text format truncates to ~5 items in the summary)
recent_activity(timeframe="2d", output_format="json")

# Read specific daily notes
read_note(identifier="memory/2026-02-27")
read_note(identifier="memory/2026-02-26")

# Check active tasks
search_notes(note_types=["task"], status="active")
```

### 2. Evaluate What Matters

For each piece of information, ask:
- Is this a **decision** that affects future work? → Keep
- Is this a **lesson learned** or mistake to avoid? → Keep
- Is this a **preference** or working style insight? → Keep
- Is this a **relationship** detail (who does what, contact info)? → Keep
- Is this **transient** (weather checked, heartbeat ran, routine task)? → Skip
- Is this **already captured** in MEMORY.md or another long-term file? → Skip

### 3. Update Long-Term Memory

Write consolidated insights to `MEMORY.md` following its existing structure:
- Add new sections or update existing ones
- Use concise, factual language
- Include dates for temporal context
- Remove or update outdated entries that the new information supersedes

### 4. Log the Reflection

Append a brief entry to today's daily note:
```markdown
## Reflection (HH:MM)
- Reviewed: [list of files reviewed]
- Added to MEMORY.md: [brief summary of what was consolidated]
- Removed/updated: [anything cleaned up]
```

## Guidelines

- **Be selective.** The goal is distillation, not duplication. MEMORY.md should be curated wisdom, not a copy of daily notes.
- **Preserve voice.** If the agent has a personality/soul file, reflections should match that voice.
- **Don't delete daily notes.** They're the raw record. Reflection extracts from them; it doesn't replace them.
- **Merge, don't append.** If MEMORY.md already has a section about a topic, update it in place rather than adding a duplicate entry.
- **Flag uncertainty.** If something seems important but you're not sure, add it with a note like "(needs confirmation)" rather than skipping it entirely.
- **Restructure over time.** If MEMORY.md is a chronological dump, restructure it into topical sections during reflection. Curated knowledge > raw logs.
- **Check for filesystem issues.** Look for recursive nesting (memory/memory/memory/...), orphaned files, or bloat while gathering material.
