---
name: knowledge-capture
description: Capture the meaningful context of a Claude Code thread into a single coherent Basic Memory note. On subsequent invocations within the same thread (identified by the JSONL session UUID) the same note is rewritten, not appended.
---

# Knowledge Capture

Capture the gist of a Claude Code thread — the decisions made, insights surfaced, and context built — into a single coherent Basic Memory note that reflects where the thread has landed.

## Purpose

A thread has a beginning, middle, and end. Things change as the conversation progresses: an early decision gets revised, a problem looks different in light of new information, a trade-off is settled differently than it first seemed. When this skill is invoked, capture the **current state of understanding**, not the history of how it got there.

If the skill is invoked more than once in the same thread, the **same note is rewritten** so it stays coherent — not appended to. The result should read top-to-bottom as a single document about the thread's outcome, with brief prose where a meaningful change is worth acknowledging.

## When to Use

Typical timing is **mid-thread or end-of-thread**, after enough has been settled to be worth preserving.

Use this skill when:
- Key decisions have been made and shouldn't evaporate when the thread closes
- A design, debugging, or planning discussion has produced something concrete
- The user explicitly asks to capture, save, or remember what's been discussed
- Toward the end of a session, to summarize the outcome

It is fine — and expected — to invoke this skill multiple times in the same thread as the conversation evolves.

## Same-Thread Detection

Each Claude Code session has a stable UUID embedded in its transcript filename. Derive it at runtime via Bash — search across all project directories and take the most-recently-modified jsonl file:

```bash
ls -t ~/.claude/projects/*/*.jsonl 2>/dev/null | head -1 | xargs basename | sed 's/\.jsonl$//'
```

The active session is continuously appending to its jsonl file, so it's reliably the most-recent. The filename (minus extension) is the session UUID. Use this as the `thread_id` for the note's frontmatter.

**Note:** an earlier version of this command used `pwd` to scope to a single project directory, but that breaks when the shell has `cd`'d into a subdirectory of the Claude Code session's project root. The cross-project glob is more robust.

**Edge case:** if the user has multiple Claude Code sessions running simultaneously, "most recent" can flip between them. Rare in practice.

## Decision Flow

1. **Derive the session UUID** with the Bash command above.
2. **Search Basic Memory** for an existing note tagged with this UUID. Use `metadata_filters` (not `query`) — full-text query doesn't reliably match YAML frontmatter custom fields:
   ```python
   mcp__basic-memory__search_notes(
       metadata_filters={"thread_id": "<session-uuid>"},
       project="<project>"
   )
   ```
3. **If a match is found:**
   - Read the existing note (use the full permalink returned by search, e.g., `bmem/development/basic-memory/...`)
   - Synthesize a new version that integrates the latest understanding from the conversation
   - Overwrite via `write_note` with `overwrite=true` (same title, same `thread_id`, same directory)
4. **If no match is found:**
   - Synthesize the note from the conversation
   - Pass `metadata={"thread_id": "<session-uuid>"}` to `write_note` (it surfaces as a custom frontmatter field)
   - Save — the `placement` skill picks the folder

## Synthesis Rules

When updating an existing thread note, **synthesize, don't append**:

- Decisions that are still current → keep, possibly refined
- Decisions that have been superseded → replaced inline (the new one goes where the old one was)
- Significant revisions that deserve explanation → a sentence woven into the relevant section, *not* an appended changelog
- Outdated context → removed

Goal: the note reads top-to-bottom as a single coherent document. A reader who never saw the conversation should still understand the outcome from the note alone. There is no `## Changes` section at the bottom; revisions live in the prose where they're relevant.

## Escape Hatch

If the user explicitly asks for a separate note (e.g., "capture this as a new note, don't merge with the existing thread note"), skip the same-thread lookup and create a fresh note without setting `thread_id`. This is rare; the default is to update.

## Note Structure

```markdown
---
title: <descriptive title — placement skill may adjust naming convention>
type: note
thread_id: <session-uuid>
tags:
- relevant
- tags
---

# <Title>

## Context

What this thread is about — the situation, problem, or topic being explored.

## <One or more topical sections>

The actual content. Could be decisions, a design rationale, an investigation summary, etc.

## Observations

- [decision] What was decided #tag
- [insight] Key understanding gained #tag
- [tradeoff] Option A chosen over B because... #tag

## Relations

- relates-to [[Related Concept]]
- implements [[Parent Spec]]
```

## Common Observation Categories

- `[decision]` — choices made
- `[insight]` — understanding gained
- `[pattern]` — reusable approaches
- `[learning]` — lessons learned
- `[tradeoff]` — options weighed
- `[problem]` — issues identified
- `[solution]` — fixes applied

## Title

The title should reflect the thread's topic. On update, the title can be refined if the topic has clarified — but it should still describe the same thread. Don't drift to a wholly new topic; if that's needed, use the escape hatch and create a new note.

## MCP Tools Used

```python
# Find existing thread note (use metadata_filters, not query)
mcp__basic-memory__search_notes(
    metadata_filters={"thread_id": "<session-uuid>"},
    project="<project>"
)

# Read existing thread note (use the full permalink from search results)
mcp__basic-memory__read_note(
    identifier="<full-permalink>",
    project="<project>",
    include_frontmatter=True
)

# Create
mcp__basic-memory__write_note(
    title="<title>",
    content="<markdown body — frontmatter is generated from title/tags/metadata>",
    directory="<folder>",
    tags=["..."],
    metadata={"thread_id": "<session-uuid>"},
    project="<project>"
)

# Overwrite an existing note (same path)
mcp__basic-memory__write_note(
    title="<same title>",
    content="<new content>",
    directory="<same folder>",
    tags=["..."],
    metadata={"thread_id": "<same session-uuid>"},
    overwrite=True,
    project="<project>"
)
```

The `placement` skill runs automatically before the write (via PreToolUse hook) to pick the folder.

## Examples

### Example 1 — First capture during a brand design conversation

**Preceding conversation:** The user has been working through visual identity decisions for a new product. They settled on a deep navy primary (`#2B3651`), explored accent options and chose orange (`#F26B3A`) for warmth, and picked Inter as the body font with Helvetica Neue as the display font.

**User invokes:** `/knowledge-capture`

**Result — note created:**

```markdown
---
title: Visual identity — initial decisions
type: note
thread_id: 7c1d4a2e-3b5f-4d8a-9e1c-2f6b8a4d7c39
tags:
- branding
- design
---

# Visual identity — initial decisions

## Context

Working through the visual identity for the new product. This thread covers the initial palette and typography pass — a starting point that will likely be refined.

## Color palette

- Primary: deep navy `#2B3651` — calm and professional
- Accent: warm orange `#F26B3A` — energy and warmth as a complement to the navy

## Typography

- Body: Inter — neutral, readable at small sizes
- Display: Helvetica Neue — strong presence for headings without being heavy

## Observations

- [decision] Primary color is navy `#2B3651` #branding
- [decision] Accent color is orange `#F26B3A` #branding
- [decision] Inter for body, Helvetica Neue for display #typography
- [tradeoff] Considered teal as accent; orange tested better for warmth #branding

## Relations

- relates-to [[Brand Strategy]]
```

### Example 2 — Update capture later in the same thread

**Preceding conversation (continued):** After the initial decisions above, the conversation continued. The orange accent felt too aggressive in mock-ups, so we tested a coral (`#E89B7A`) which read warmer and more refined. The body font also shifted: Geist felt slightly tighter and more modern than Inter. Helvetica Neue for display stayed.

**User invokes:** `/knowledge-capture` again — same thread.

**Result — same note rewritten (note the same `thread_id`):**

```markdown
---
title: Visual identity — initial decisions
type: note
thread_id: 7c1d4a2e-3b5f-4d8a-9e1c-2f6b8a4d7c39
tags:
- branding
- design
---

# Visual identity — initial decisions

## Context

Working through the visual identity for the new product. This thread settled on a navy + coral palette and a Geist/Helvetica typography pairing after a round of refinement.

## Color palette

- Primary: deep navy `#2B3651` — calm and professional
- Accent: coral `#E89B7A` — warm and refined

The accent went through a round of revision: an initial orange (`#F26B3A`) felt too aggressive in mock-ups, so we shifted to a coral that reads warmer and more refined while keeping the energy.

## Typography

- Body: Geist — slightly tighter and more modern than Inter, which we tried first
- Display: Helvetica Neue — strong presence for headings without being heavy

## Observations

- [decision] Primary color is navy `#2B3651` #branding
- [decision] Accent color is coral `#E89B7A` — warmer and more refined than the originally-chosen orange #branding
- [decision] Geist for body, Helvetica Neue for display #typography
- [tradeoff] Inter felt neutral but Geist edged it for spacing and modernity #typography
- [tradeoff] Orange accent rejected as too aggressive; coral preferred #branding

## Relations

- relates-to [[Brand Strategy]]
```

Notice that:
- The orange and Inter decisions are **no longer the primary content** — they're acknowledged in prose ("which we tried first," "originally-chosen orange") and in tradeoff observations
- There is **no "Changes" section** at the bottom — revisions are integrated where they belong
- The note still reads top-to-bottom as a single coherent document
- The `thread_id` is unchanged, so the note was updated in place rather than duplicated

## Best Practices

1. **Capture the current state, not the history.** The note represents where the thread has landed.
2. **Synthesize, don't log.** Each invocation produces a coherent document, not an accumulating record.
3. **Brief prose for revisions.** A sentence in the section that changed is enough — don't add a changelog.
4. **Always run the same-thread lookup** before deciding to create or update.
5. **Use observations for the structured layer.** Decisions, insights, tradeoffs go in `## Observations` so they're searchable.
6. **Link relations liberally.** Notes the user might want to reach from this one.
