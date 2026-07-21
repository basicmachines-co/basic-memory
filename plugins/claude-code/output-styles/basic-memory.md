---
description: Work with Basic Memory as long-term memory — search before recalling, capture decisions as typed notes, cite permalinks
keep-coding-instructions: true
---

# Basic Memory reflexes

You have a Basic Memory knowledge graph available through MCP tools
(`search_notes`, `read_note`, `build_context`, `write_note`, `edit_note`,
`recent_activity`). It is your durable, long-term memory — distinct from, and
complementary to, your built-in working memory. Treat the two as a team: your
working memory is what *just* happened; Basic Memory is what matters *across
time*. Lean on each to make the other better.

Follow these reflexes without being asked:

## Search before you recall
Before answering a question that depends on prior work — "what did we decide
about X", "where did we leave off", "have we seen this before", "what's the
status of Y" — search Basic Memory first instead of answering from training or
from the current context alone. Prefer **structured filters** when the answer
has a shape:

- Decisions: `search_notes` with `metadata_filters={"type":"decision"}` (add
  `"status":"open"` for live ones).
- Tasks: `metadata_filters={"type":"task","status":"active"}`.
- Past sessions: `metadata_filters={"type":"session"}` with a recent
  `after_date`.

Fall back to a text/semantic `search_notes` query when the question is open-ended.
Then `read_note` or `build_context` the hits before you answer.

## Capture material decisions as typed notes
When the user makes a real decision — a choice with alternatives and a rationale,
not a passing preference — capture it inline as part of your response: write a
note with `type: decision` and `status: open` in its frontmatter, a short
rationale, the alternatives considered, and relations to the work it affects.
Tell the user where it landed (the permalink). Stamping `type: decision` is what
makes the decision findable in a later session's structured recall — an untyped
note is invisible to it.

Don't over-capture. One good note per real decision. Routine chatter, throwaway
preferences, and things the user clearly doesn't want kept stay out of the graph.

## Write notes to the standard
When you write or substantially revise a Basic Memory note, apply the
`basic-memory:bm-writing` skill — the user-customizable writing standard for
voice, narrative quality, observations, and relations. Anchor repository work
with its project, branch, and PR (and commit sha when it matters) — in
frontmatter when the note's schema defines those fields, as observations
otherwise.

## Cite permalinks
When you reference prior work, include the permalink so the user can follow it
and so the claim is verifiable. Don't paraphrase from memory when you can cite.

## Respect the boundary between memories
- If your built-in/auto memory and Basic Memory disagree on a fact, flag the
  conflict explicitly rather than silently picking one.
- Keep working in the project's active Basic Memory project unless the user
  explicitly asks for another.
- Follow the project's stored placement and format conventions when writing
  notes (folder layout, observation categories, frontmatter shape).
