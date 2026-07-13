# Conventions & Discipline

The structural patterns that keep a Basic Memory knowledge base coherent as it grows. These are defaults, not laws — but each exists because its absence causes a specific, recurring failure. When you relax one for a light-use setup, tell the user what they're trading away.

## Contents

1. [The startup router](#the-startup-router)
2. [Instruction notes](#instruction-notes)
3. [Changelog tables](#changelog-tables)
4. [Index notes](#index-notes)
5. [Linking discipline](#linking-discipline)
6. [Write discipline](#write-discipline)
7. [Known failure modes](#known-failure-modes)

---

## The startup router

One note — suggested location `Instructions/Startup Router` — that any assistant reads at the start of every session, before doing any knowledge-base work. It is the single entry point to the whole system. It contains:

1. **A read-me-first banner** — "Read this note at the start of every session, before any task work."
2. **Non-negotiable rules** — the handful of rules that apply to every task type and can't be overridden by anything else (e.g. "always search before creating", "always pass the project name explicitly"). Keep this list short — five to eight items. A rule that only matters for one domain belongs in that domain's instruction note instead.
3. **A dispatch table** — task type → which notes to load, in order:

   | Task type | Load in order |
   |:--|:--|
   | Task / project work | 1. `memory://instructions/task-instructions` · 2. `[[Task Board]]` |
   | New contact / person update | 1. `memory://instructions/people-instructions` |
   | Expense / subscription | 1. `memory://instructions/finance-instructions` · 2. `[[Subscriptions Index]]` |
   | Anything else / ambiguous | load all instruction notes |

4. **Known failure modes** — a running list of documented mistakes to check before writing (see below).
5. **Canonical reference data** — if the system has a roster-like table (accounts, categories, family members, clients), keep the ONE canonical copy here and make other notes point to it. Duplicated rosters drift apart; nobody notices until something routes to the wrong place.

**Why a router instead of one big rules note:** context is finite. The router is small and always loaded; everything else loads only when the task needs it. It also gives the system one place to grow — a new domain is a new dispatch row, not a rewrite.

The router itself is a note, so it gets a changelog table and observations like any other. When the user changes a convention, the change lands here or in a domain instruction note — with a changelog row — so the system stays self-describing.

## Instruction notes

One note per domain (`Instructions/Task Instructions`, `Instructions/Finance Instructions`, ...) holding everything an assistant needs to work in that domain:

- **Scope** — what this note covers and what it doesn't ("for vendor stuff, see [[Vendor Instructions]]")
- **Folder map** — where each note type lives, with exact casing
- **Schemas in play** — table of note types, their schema notes, and required observations
- **Naming conventions** — title formats with examples, date formats, status vocabularies
- **Workflows** — step-by-step for the recurring operations ("closing a task: 1. set `[status] done` 2. add close date 3. append row to [[Task Board]]")
- **Domain-specific rules** — anything true here that isn't true globally

Write instruction notes for a reader with zero context: the assistant loading it next month is effectively a stranger. Explain *why* behind rules that aren't obvious — an assistant that understands the reason applies the rule correctly in situations the rule didn't anticipate.

## Changelog tables

Every structural or long-lived note carries exactly one changelog table, immediately below the title:

```markdown
| Notes | Modification Date | Approved By |
|:--|:--|:--|
| Initial Document | July 12, 2026 | {user's name} |
| Added renewal-date field to all subscription rows | August 3, 2026 | {user's name} |
```

Rules:
- **Append, never edit.** Existing rows are history; new rows record change.
- **One table per note.** Before overwriting any existing note, read the full note first, carry the existing changelog forward verbatim, and append your row. If you ever find two tables, merge them into one at the top before writing.
- **Consistent date format** — pick one (`Month D, YYYY` works well) and use it everywhere.

Why: an assistant (or the user) reading a note months later can see what changed, when, and on whose authority — without any external version control. It also forces the read-before-overwrite habit that prevents accidental content loss.

For a light setup, changelogs on *instruction notes, schemas, and indexes* are the non-negotiable core; changelogs on every individual content note are optional.

## Index notes

An index is a note containing a table of contents for one domain — one row per note, with wiki-links and a few key columns (status, date, owner):

```markdown
# Task Board

| Task | Status | Due |
|:--|:--|:--|
| [[Fix gutter downspout]] | in-progress | July 20, 2026 |
| [[Renew car registration]] | open | August 1, 2026 |
```

Rules:
- **Update the index as part of any change to its members** — a stale index is worse than none, because it gets trusted.
- **Full overwrite, not section-append.** When updating an index with `write_note`, always rewrite the whole note (read → modify → overwrite). Section-level append operations on tables create duplicate sections and rows.
- Not every domain needs one. Indexes earn their keep where the user will ask "show me all X with status Y" or wants a glanceable board. Journals and reference notes usually don't need one — search covers them.

## Linking discipline

- **Bidirectional links for paired entities.** If a task note links its project, the project note links its tasks. If a device links its owner, the owner links the device. One-directional links rot: graph traversal from the unlinked side finds nothing. Make "both directions, always" a rule for whichever entity pairs the user's system has.
- **Relations sections for typed links, inline wiki-links for prose.** `- part_of [[Kitchen Renovation]]` in Relations; "discussed with [[Dana Reyes]]" in the body. Both create graph edges.
- **Reference notes by title or `memory://` path — never by raw permalink strings.** Permalinks can be regenerated when notes are rewritten or moved between projects; hardcoded permalink strings silently break. `[[Title]]` resolves by title and `memory://` URLs match on path, so both survive.
- **Link targets may not exist yet.** `[[Future Note]]` is valid and resolves when the note is created — use this to sketch structure ahead of content.

## Write discipline

The habits that prevent 90% of knowledge-base corruption:

1. **Search before create.** Always search (try 2–3 query variations: full name, abbreviation, keywords) before writing a new note. Duplicates fragment the graph. If the entity exists, `edit_note` it instead. For people and other proper nouns, use **title search** — semantic search is unreliable for names.
2. **Exact casing, exact paths.** `Tasks/` and `tasks/` are different directories. Take folder paths from the instruction note, character for character. If a write would create a new top-level folder you didn't plan, that's a casing error — stop and fix.
3. **Explicit project on every call.** In multi-project setups, pass the project explicitly on every read, search, and write. Writes to the wrong project are invisible until much later.
4. **Read before overwrite.** Full-note overwrites must start from the current content — never reconstruct a note from memory of what it contained.
5. **Watch write results.** If a write returns a permalink ending in `-1` or `-2`, you just created a duplicate: delete it and re-write the original with overwrite semantics.
6. **Prefer targeted edits.** `edit_note` (append an observation, find-and-replace a status) over full overwrites wherever possible — smaller blast radius.

## Known failure modes

Keep a "Known Failure Modes" section in the startup router and **add to it whenever a mistake happens twice**. Documented mistakes stop recurring; undocumented ones don't. Seed it during onboarding with the universal ones:

- Duplicate notes from skipping search-before-create (or from name-search using semantic instead of title matching)
- Wrong-casing paths creating parallel folder trees
- Section-append on index tables creating duplicate sections
- Second changelog table created by overwriting without reading first
- Writes landing in the wrong project when project isn't passed explicitly

Each entry: the mistake, how to recognize it, what to do instead. This list is the system's immune memory — it's often the highest-value section in the whole knowledge base.
