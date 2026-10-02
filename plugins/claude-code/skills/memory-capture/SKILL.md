---
name: memory-capture
description: "Capture the current state of a working thread or conversation into a single coherent Basic Memory note — synthesize where it landed, don't append a log. On re-capture, rewrite the same note in place instead of duplicating. Use mid-thread or end-of-thread when decisions, insights, or context are worth preserving."
---

# Memory Capture

Capture the gist of a working thread — the decisions made, insights surfaced, and context built — into a single coherent Basic Memory note that reflects where the thread has landed.

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

To rewrite the same note on re-capture instead of duplicating, key the note to a stable `thread_id` in its frontmatter.

**If your agent exposes a stable session or thread id**, store it as `thread_id` so subsequent captures within the same thread find and rewrite the same note. Any value that stays constant for the duration of the thread works — a session UUID, a conversation id, a ticket number the work is scoped to.

> **Example (hosts with a JSONL transcript):** some agents write a per-session transcript whose filename is a stable session UUID. If yours does, you can derive the id from the most-recently-modified transcript file and use it as `thread_id`. This is optional — only do it if your host actually exposes such a transcript.

**If no stable id is available**, match the existing note by title/topic instead: search for a note covering the same thread (`search_notes(query="<topic>")`), and if you find the one this thread already produced, rewrite it. Omit `thread_id` and rely on a consistent title.

## Decision Flow

1. **Determine the thread key.** Use a stable session/thread id if your agent exposes one; otherwise plan to match by title/topic.
2. **Search Basic Memory** for the existing thread note.
   - With a thread id, use `metadata_filters` (not `query`) — full-text query doesn't reliably match YAML frontmatter custom fields:
     ```python
     search_notes(
         metadata_filters={"thread_id": "<thread-id>"},
         project="<project>"
     )
     ```
   - Without one, search by topic and identify the note this thread already produced:
     ```python
     search_notes(query="<thread topic>", project="<project>")
     ```
3. **If a match is found:**
   - Read the existing note (use the full permalink returned by search)
   - Synthesize a new version that integrates the latest understanding from the conversation
   - Overwrite via `write_note` with `overwrite=True` (same title, same `thread_id` if used, same directory)
4. **If no match is found:**
   - Synthesize the note from the conversation
   - If you have a thread id, pass `metadata={"thread_id": "<thread-id>"}` to `write_note` (it surfaces as a custom frontmatter field)
   - Save it

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
title: <descriptive title for the thread>
type: note
thread_id: <thread-id, if your agent exposes one>
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

- relates_to [[Related Concept]]
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
# Find existing thread note by thread id (use metadata_filters, not query)
search_notes(
    metadata_filters={"thread_id": "<thread-id>"},
    project="<project>"
)

# Or, without a thread id, find it by topic
search_notes(query="<thread topic>", project="<project>")

# Read existing thread note (use the full permalink from search results)
read_note(
    identifier="<full-permalink>",
    project="<project>"
)

# Create
write_note(
    title="<title>",
    content="<markdown body — frontmatter is generated from title/tags/metadata>",
    directory="<folder>",
    tags=["..."],
    metadata={"thread_id": "<thread-id>"},  # omit if no stable id
    project="<project>"
)

# Overwrite an existing note (same path)
write_note(
    title="<same title>",
    content="<new content>",
    directory="<same folder>",
    tags=["..."],
    metadata={"thread_id": "<same thread-id>"},  # omit if no stable id
    overwrite=True,
    project="<project>"
)
```

## Examples

### Example 1 — First capture during a brand design conversation

**Preceding conversation:** The user has been working through visual identity decisions for a new product. They settled on a deep navy primary (`#2B3651`), explored accent options and chose orange (`#F26B3A`) for warmth, and picked Inter as the body font with Helvetica Neue as the display font.

**User asks to capture.**

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

- relates_to [[Brand Strategy]]
```

### Example 2 — Update capture later in the same thread

**Preceding conversation (continued):** After the initial decisions above, the conversation continued. The orange accent felt too aggressive in mock-ups, so we tested a coral (`#E89B7A`) which read warmer and more refined. The body font also shifted: Geist felt slightly tighter and more modern than Inter. Helvetica Neue for display stayed.

**User asks to capture again — same thread.**

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

- relates_to [[Brand Strategy]]
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
