# Domain Playbooks

Starter kits for the common things people track. Each gives folders, a schema sketch, naming, an index decision, and expansion ideas. These are starting points — adapt every one to the user's Phase 1 answers rather than installing verbatim. Statuses, folder names, and required fields should use the user's own vocabulary where they have one.

Every domain also gets: an instruction note (`Instructions/{Domain} Instructions`), a row in the startup router's dispatch table, and templates for its note types.

## Contents

1. [Tasks & projects](#tasks--projects)
2. [Notes & journal](#notes--journal)
3. [People & contacts](#people--contacts)
4. [Research](#research)
5. [Finances](#finances)
6. [Procedures](#procedures)
7. [Health & habits](#health--habits)
8. [Assets](#assets)

---

## Tasks & projects

```
Tasks/
├── Task Board.md          ← index: open/in-progress tasks
└── {task notes}
Projects/
├── {Project Name}/        ← one folder per active multi-step project
│   └── {Project Name}.md  ← project note; links its tasks
```

- **Task schema:** `status(enum): [open, in-progress, blocked, done]` · `due?` · `priority?(enum)` · `project?: Project`
- **Project schema:** `status(enum): [active, paused, done]` · `goal` · `target_date?`
- **Naming:** tasks get short imperative titles (`Renew car registration`); completed tasks can be retitled with a date prefix (`2026-07-12 — Renew car registration`) when archived, so archives sort chronologically.
- **Index:** yes — the Task Board is usually the single most-used note in the system. Open items only; done tasks drop off the board (search finds them).
- **Bidirectional rule:** task ↔ project, both directions.
- **Key workflow to document:** closing a task (set status, add close date, update board, move to archive folder if using one).
- **Expansions:** meeting notes with action items that become tasks; a weekly-review note; recurring-task conventions.

## Notes & journal

```
Journal/
└── {YYYY-MM-DD}.md        ← one note per day
Notes/
└── {topic notes}          ← ideas, things learned, free-form
```

- **Journal Entry schema (keep it tiny — one required field):** `date` · `mood?(enum)` · `highlight?`. Even the loosest domain gets a schema and observations — but here the observation load is deliberately one line (`- [date] 2026-07-12`, plus `[highlight]`/`[idea]`/`[mood]` when they apply), so capture stays frictionless.
- **Note schema (topic notes):** `note_type?(enum): [idea, reference, learning]` — one optional field; the requirement is simply that every note carries at least one observation (`[idea]`, `[source]`, `[lesson]`...).
- **Naming:** `YYYY-MM-DD` for journal (sorts itself); descriptive titles for topic notes.
- **Index:** no. Date-named journals self-organize; topic notes are found by search.
- **The one convention that matters:** when a journal entry mentions a person, project, or topic that has (or deserves) a note, wiki-link it. Journals become the connective tissue of the graph.
- **Expansions:** weekly reflection summarizing the week's entries; automatic extraction of `[idea]` observations into topic notes.

## People & contacts

```
People/
└── {Full Name}.md         ← one note per person
```

- **Schema:** `relationship?(enum): [family, friend, coworker, professional, acquaintance]` · `email?` · `phone?` · `company?` · `last_contact?`
- **Naming:** full name, consistently — `Dana Reyes`, never `dana` in one note and `Dana R.` in another. Titles are graph identifiers.
- **Index:** optional — useful if the user wants a follow-up board (`last_contact` older than N months).
- **Critical rule:** ALWAYS title-search (full name, then last name) before creating a person note — people are the most-duplicated entity type, and semantic search misses names.
- **Body guidance:** this is where prose shines — how they met, what they care about, gift ideas, kids' names. Observations for the queryable bits (`- [birthday] March 12`).
- **Expansions:** interaction log (`- [contact] July 12, 2026 — coffee, discussed job change` appended per touch); linking people to meetings, projects, gifts.

## Research

```
Research/
├── {Topic}/
│   ├── {Topic}.md         ← topic hub: question, current understanding, links to sources
│   └── {source notes}     ← one note per significant source
```

- **Topic schema:** `status(enum): [active, parked, concluded]` · `question` (what they're trying to answer)
- **Source schema:** `source_type(enum): [article, paper, video, book, conversation]` · `url?` · `date_consumed?`
- **Naming:** topic hubs by topic name; sources by title of the source.
- **Index:** the topic hub IS the index for its sources.
- **Key conventions:** every source note ends with `- part_of [[Topic]]`; the hub's "current understanding" section gets rewritten (with changelog row) as understanding evolves — the hub is a living synthesis, not an append-only log.
- **Expansions:** a reading queue; `[claim]`/`[evidence]`/`[contradiction]` observation categories for contested topics; concluded-topic summaries.

## Finances

```
Finances/
├── Subscriptions Index.md ← index: every recurring charge, cost, renewal date
├── Subscriptions/
│   └── {Service}.md       ← one note per subscription/recurring bill
├── Accounts/
│   └── {Account}.md       ← one note per account (no credentials — see below)
└── Records/
    └── {YYYY-MM-DD} — {Description}.md   ← one-off: big purchases, tax docs, claims
```

- **Subscription schema:** `cost` · `billing_cycle(enum): [monthly, annual, quarterly]` · `renewal_date` · `payment_method?` · `status(enum): [active, cancelled, trial]`
- **Naming:** records get `{YYYY-MM-DD} — {Description}`, dated by the document date, not the filing date.
- **Index:** yes — the Subscriptions Index (name, cost, cycle, renewal, method) is the payoff note: total monthly spend at a glance, upcoming renewals visible.
- **Two hard rules for the instruction note:** (1) **no secrets ever** — no account numbers beyond last-4, no passwords, no full card numbers; notes are plaintext markdown. (2) **amounts and payment methods come from the user or a document — never inferred or guessed.**
- **Expansions:** annual-cost review workflow; warranty/receipt records linking to Assets; a renewals-this-month recurring check.

## Procedures

```
Procedures/
├── {area}/                ← optional grouping: Home/, Tech/, Work/
└── {Procedure Name}.md    ← one note per how-to
```

- **Schema:** `procedure_type(enum): [how-to, troubleshooting, checklist, reference]` · `applies_to?` · `last_verified?`
- **Naming:** descriptive, search-first titles — the phrase the user would actually say (`Reset the water softener`, not `Softener maintenance procedure v2`).
- **Index:** optional per area once a folder exceeds ~15 notes.
- **Key conventions:** numbered steps; prerequisites up front; `last_verified` observation updated whenever the procedure is confirmed still-correct — and when a procedure is used and found wrong, fixing the note is part of finishing the task.
- **Expansions:** seasonal checklists; linking procedures to the assets they service.

## Health & habits

```
Health/
├── Log/
│   └── {YYYY-MM-DD} — {type}.md    ← workouts, symptoms, appointments
└── {condition/goal notes}          ← ongoing threads: an injury, a training goal
```

- **Log schema:** `entry_type(enum): [workout, symptom, appointment, measurement]` · `date`
- **Index:** optional — a current-goals note works better than a full log index.
- **Key conventions:** log entries are quick and low-friction (two observations and a sentence beats an unfilled template); ongoing threads link their log entries so `build_context` on "left knee" pulls the full history.
- **Sensitivity note for the instruction file:** health data in plaintext markdown — the user should decide consciously what level of detail they're comfortable storing, and where the project syncs to.
- **Expansions:** habit streaks; pre-appointment summaries generated from the log.

## Assets

```
Assets/
├── Asset Index.md         ← index: every asset, location, value band
└── {Asset Name}.md        ← one note per significant item
```

- **Schema:** `asset_type(enum): [electronics, appliance, vehicle, furniture, tool, other]` · `serial_number?` · `purchase_date?` · `purchase_price?` · `warranty_until?` · `location?` · `status(enum): [in-use, stored, loaned, sold, disposed]`
- **Naming:** `{Make} {Model}` or a distinguishing name (`Garage Freezer`); serial in observations, not the title.
- **Index:** yes — this is the insurance-claim / "where is the..." note.
- **Bidirectional rule:** if assets are assigned to people (family laptops), asset ↔ person, both directions.
- **Expansions:** warranty-expiry checks; maintenance logs linking to Procedures; purchase records linking to Finances.

---

## Combining domains

Domains compound through relations — mention these links when the user picks the pairs:

- Tasks ↔ People (`waiting_on [[Dana Reyes]]`) · Tasks ↔ Projects
- Finances ↔ Assets (purchase records ↔ asset notes)
- Procedures ↔ Assets (`services [[Garage Freezer]]`)
- Journal → everything (daily entries wiki-link whatever they mention)
- Research ↔ Notes (ideas graduate into research topics)

The cross-domain links are where a knowledge *graph* starts beating a folder of documents — surface one concrete example using the user's own notes during Phase 5.
