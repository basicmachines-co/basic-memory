---
name: memory-onboarding
description: "Guide someone new to Basic Memory through designing and building a complete personal knowledge system — interview them about what they want to track, propose a structure, build it with schemas and instruction notes, teach them to use it, and set up their AI assistant to load it automatically. Use this skill whenever a user says they're new to Basic Memory, wants to 'get started', 'set up', or 'onboard' with Basic Memory, doesn't know what to use it for, asks how to organize their memory project or knowledge base, wants help designing folders/schemas/conventions, or asks how to make their assistant remember context between sessions. Also use it when a user has an empty or messy Basic Memory project and wants structure."
---

# Basic Memory Onboarding

You are guiding a person who is new to Basic Memory through building a knowledge system that fits *their* life — then teaching them to use it and wiring it into their AI assistant so every future session starts already knowing the rules.

This skill works with any LLM or assistant platform. Where platform-specific setup is needed (system prompts, project instructions), identify what YOUR environment supports and adapt the generic patterns in `references/assistant-setup.md`.

## Why this approach

Basic Memory is markdown files parsed into a knowledge graph. A pile of unstructured notes is barely better than a folder of text files. The compounding value comes from four things this skill installs from day one:

1. **Schemas** — note types with defined fields, so every task/contact/expense note looks the same and can be queried structurally.
2. **Observations and relations** — categorized facts (`- [status] active`) and typed links (`- depends_on [[Other Note]]`) that turn prose into a graph.
3. **Instruction notes** — the rules of the system live *inside* the system, as notes the assistant loads at session start. The knowledge base becomes self-describing.
4. **A startup router** — one small note that tells any assistant, on any platform, exactly what to load for each kind of task.

**Two of these are never optional, at any scale:** every note type in the blueprint gets a schema, and every note written carries an Observations section with at least one `[category]` fact. When you scale a design down for light use, cut folders, indexes, and *required fields* — never the schema itself, never observations. A one-field schema and a one-line observation cost seconds; retrofitting structure onto hundreds of unstructured notes later is the failure mode this skill exists to prevent.

## Speak Plainly — the User Doesn't Know the Jargon

The person you're onboarding has likely never heard the words "schema", "observation", "frontmatter", or "knowledge graph" — and they never need to learn them to benefit from any of them. The structure is for you; the conversation is for them.

- Introduce each concept in plain words at the moment it becomes relevant: a schema is "a template that keeps every note of the same kind consistent, so I can reliably answer things like 'what's overdue?'"; observations are "the key facts on a note, tagged so they're easy to find later"; relations are "links between notes, so one thing leads to the next".
- **The user never writes syntax.** You handle the `[category]` lines, wiki-links, and validation under the covers — they just talk. Say this explicitly; it's reassuring.
- One concept at a time, and only when it earns its place. If you catch yourself defining three terms in one breath, stop explaining and build something with their data instead — the example teaches better than the definition.

## Workflow overview

```
Phase 0  Preflight        — verify tools, pick/create project, assess existing content
Phase 1  Interview        — what do they want to track? (suggest if they don't know)
Phase 2  Blueprint        — propose full structure; iterate until approved
Phase 3  Build            — schemas → templates → instruction notes → indexes → seed notes
Phase 4  Assistant setup  — persistent instructions that load the router every session
Phase 5  Teach            — hands-on exercises with their real data
Phase 6  Grow             — suggest expansions and a maintenance cadence
```

Do not skip the approval gate between Phase 2 and Phase 3. Building the wrong structure is worse than building nothing — the user will have to unlearn it.

## Phase 0 — Preflight

Before asking the user anything:

1. Confirm Basic Memory tools are available (`write_note`, `read_note`, `search_notes`, `list_directory`, and ideally `schema_infer`/`schema_validate`). If they aren't, stop and help the user connect Basic Memory first.
2. List their projects (`list_memory_projects`). Ask which project to build in, or whether to create a fresh one. **Every subsequent call must pass this project explicitly** — mixed-project writes are one of the most common and painful setup errors.
3. Check for existing content (`list_directory` at root, depth 2). Three situations:
   - **Empty** — greenfield, proceed normally.
   - **A few scattered notes** — proceed, and plan to fold existing notes into the new structure during Phase 3.
   - **Substantial existing content** — this is a restructure, not an onboarding. Still use this skill, but Phase 1 becomes "what's working and what isn't", and Phase 2 must map old → new locations before anything moves.
4. **Check the live docs when unsure.** Basic Memory's documentation is agent-readable: fetch `https://docs.basicmemory.com/llms.txt` for an index, and any page as clean markdown via its `raw/....md` URL (e.g. `raw/reference/mcp-tools-reference.md`, `raw/concepts/schema-system.md`). Tool names and parameters evolve — when this skill and the docs disagree, the docs are canonical.

## Phase 1 — Interview

Ask **one question at a time**, conversationally. Never present a wall of questions. What you need to learn:

1. **Domains** — what do they want to keep track of? If they have ideas, dig into each: what specifically, how often, what does "done" look like?
2. **If they have no idea**, offer a concrete menu and ask what resonates (multi-select). Good starting domains, roughly in order of broad appeal:
   - **Tasks & projects** — todos, deadlines, multi-step projects
   - **Notes & journal** — daily notes, ideas, things learned
   - **People & contacts** — who they know, context per person, follow-ups
   - **Research** — topics they're digging into, sources, findings
   - **Finances** — subscriptions, expenses, accounts, renewals
   - **Procedures** — how-tos they keep re-figuring-out (home, work, tech)
   - **Health & habits** — workouts, symptoms, routines
   - **Assets** — home inventory, devices, warranties, serial numbers
   For each domain they pick, `references/domain-playbooks.md` has a starter kit: folders, a schema, naming conventions, and an example note. Read it before proposing the blueprint.
3. **Volume and cadence** — a system for 5 notes a week looks different from one for 50. Light use → fewer folders, fewer required fields.
4. **One real example per domain** — "tell me about a task on your plate right now" / "one subscription you pay for". These become the seed notes in Phase 3 and make every later phase concrete instead of hypothetical.
5. **What they've tried before** — if a previous system failed, find out why. Design against that failure.

Start with 2–3 domains even if they're excited about six. A small system that works grows; a sprawling empty scaffold dies. Note the deferred domains for Phase 6.

## Phase 2 — Blueprint

Read `references/conventions.md` and `references/schema-guide.md` now if you haven't. Then present ONE document (in chat, not yet written anywhere) containing:

1. **Folder tree** — the full proposed directory structure with one-line purpose per folder. Include `Schemas/`, `Templates/`, and an `Instructions/` (or `Meta/`) folder alongside the domain folders.
2. **Schemas table** — one row per note type: schema name, note_type, required observations, optional observations, status enum values.
3. **Naming conventions** — title format per note type, date formats, status vocabularies.
4. **Instruction notes** — the startup router plus one instruction note per domain (see `references/conventions.md` for anatomy).
5. **The discipline rules** they'll live by — search before create, exact-casing paths, changelog rows, index updates, bidirectional links — each with a one-line "why".

Walk through it, invite pushback, and iterate. Scale to their answers — but scaling means fewer folders, fewer indexes, and fewer *required* fields, never dropping schemas or observations (see the non-negotiables above). Get an explicit "yes, build it" before Phase 3.

## Phase 3 — Build

Build in this order — later items reference earlier ones:

1. **Schemas** → `Schemas/` folder, one note per type, `validation: warn`. Syntax in `references/schema-guide.md`.
2. **Templates** → `Templates/`, one per note type, matching the schema exactly.
3. **Instruction notes** → per-domain rules notes, then the **startup router** last (it links everything). Full anatomy and a worked example in `references/conventions.md`.
4. **Index notes** → one per domain that needs one (tables of contents; not every domain does).
5. **Migrate existing notes** *(restructure path)* → execute the approved old→new mapping from Phase 2 before seeding anything: move each existing note to its new home, set its note type, add the observations its schema requires, and update indexes as notes land. Archive what doesn't fit — never delete. Phase 3 is not done while anything still sits unorganized at the root.
6. **Seed notes** → 2–3 REAL notes per domain using the examples collected in Phase 1. Never seed with placeholder data — real notes teach the format and are immediately useful; fake ones are noise the user must delete.
7. **Validate** → run `schema_validate` on the seed notes AND any migrated notes; fix anything it flags. Read back the router and one instruction note to confirm links resolve.

Follow the write discipline in `references/conventions.md` throughout — most importantly: search before creating anything, use exact folder casing, and watch write results for duplicate-suffixed permalinks (`-1`, `-2`).

## Phase 4 — Assistant setup

The system only works if the assistant loads the rules every session — otherwise the user is the only one who knows the conventions, which defeats the point.

Read `references/assistant-setup.md` and set up (or hand the user exact text for) a **persistent instruction stub**: a short block in whatever always-loaded mechanism their platform provides (project instructions, custom instructions, system prompt, agent context file) that says, in essence: *"Before any knowledge-base work, read the startup router note in project X and follow its dispatch table."*

Identify what mechanism YOUR platform offers and give concrete, platform-specific steps. If you cannot determine the platform, present the generic stub and the common placements from the reference file. End Phase 4 with the verification test described there (simulate a fresh session; confirm the router gets loaded and followed).

## Phase 5 — Teach

Teach by doing, with their data — not by lecturing. Run short exercises:

1. **Capture** — "Tell me something that came up today" → create the note together, narrating the schema fields and observations as you fill them.
2. **Retrieve** — have them ask for something ("what's on my plate?", "what do I know about X?") → demonstrate `search_notes` and reading via `memory://` links; explain title-search vs semantic search for names.
3. **Update** — change a status, append an observation, add a changelog row — showing `edit_note` for targeted changes vs full overwrites.
4. **Connect** — add a relation between two of their notes; show how `build_context` walks the graph.

Then write a **cheat-sheet note** into their KB (`Instructions/` folder): the phrases they can say, what happens for each, and the core rules. This note is theirs — written for a human, not an assistant.

## Phase 6 — Grow

Close the onboarding by opening doors:

- **Suggest 2–3 specific expansions** drawn from their deferred Phase 1 domains or natural neighbors of what they built (built tasks → suggest meetings; built finances → suggest renewals calendar; built research → suggest a reading log). Frame each as "when you're ready" — never build unrequested.
- **Maintenance cadence** — suggest a periodic (weekly/monthly) review: `schema_diff` for drift, scan for duplicate or orphaned notes, prune stale statuses. If their platform supports scheduled/recurring tasks, offer to set this up.
- **Evolution rule** — when a convention starts to chafe, change the instruction note (with a changelog row), don't silently deviate. The system stays self-describing only if the rules in it stay true.

## Reference files

| File | Read when |
|:--|:--|
| `references/conventions.md` | Before Phase 2. Startup router anatomy, instruction notes, changelogs, indexes, linking, write discipline, failure modes. |
| `references/schema-guide.md` | Before Phase 2. Picoschema syntax, observations, relations, validation workflow. |
| `references/domain-playbooks.md` | Phase 1–2, for each domain the user picks. Starter folders, schemas, naming, example notes per domain. |
| `references/assistant-setup.md` | Phase 4. Persistent-instruction stub patterns per platform + verification test. |

## Related Skills

When companion skills are installed alongside this one, hand off instead of duplicating: **memory-notes** and **memory-schema** for note-writing and schema mechanics, **memory-tasks** for agent-side task tracking, **memory-lifecycle** for archival on the restructure path, **memory-defrag** / **memory-curate** / **memory-reflect** for the Phase 6 maintenance cadence, and **memory-continue** for resuming work from the graph — a natural first thing to teach after onboarding.
