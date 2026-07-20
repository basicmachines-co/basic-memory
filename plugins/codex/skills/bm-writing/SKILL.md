---
name: bm-writing
description: Apply the user-customizable writing standard for Basic Memory notes created or substantially revised by Codex. Use with bm-checkpoint, bm-decide, bm-remember, and other Basic Memory note-writing workflows.
---

# Write Useful Project Memory

Use this shared standard whenever a Codex Basic Memory skill writes or
substantially revises a note. This file is intentionally user-customizable: edit
the voice, emphasis, and preferred structure here to fit how you want to remember
your work.

Task-specific skills still own required metadata, schemas, evidence gathering,
and workflow. This skill shapes the note; it never overrides factual constraints.

## Voice

- Write for a human or agent returning later and trying to understand what happened.
- Be clear, direct, warm, and technically honest.
- Prefer concrete observations over generic praise.
- Have a point of view when the evidence supports it. It is fine to call a
  change elegant, messy, risky, boring, or satisfying when you explain why.
- Keep personality in service of memory, not performance.

## Tell The Story

- Give the note a narrative spine: problem -> approach -> current state and impact.
- Explain why the approach works, how the system or workflow changed, and why
  that difference matters.
- Name relevant tradeoffs, sharp edges, useful simplifications, removals, and
  intentionally parked work.
- Prefer exact behavior, component names, paths, and commands over phrases such
  as "made progress" or "updated the implementation."
- Use substantive prose for context and reasoning. Do not reduce the note to a
  wall of bullets or a commit-by-commit changelog.
- Match depth to the subject. A small remembered fact should remain small.

## Preserve The Semantic Layer

- Distill durable facts under `## Observations` as `- [category] fact`.
- Record decisions as `[decision]` observations, not plain bullets in a separate
  Decisions section.
- Put graph edges under `## Relations` using `- relation_type [[Target Note]]`.
  Never represent a relation as an observation such as `[relates_to]`.
- Add relations when they clarify context; do not manufacture targets merely to
  make a note look connected.

## Evidence Boundary

- Do not invent intent, impact, verification, decisions, or drama.
- State uncertainty and missing evidence plainly.
- Never claim a test or deployment passed unless it ran or the user supplied the result.
