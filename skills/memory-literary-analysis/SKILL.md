---
name: memory-literary-analysis
description: "Analyze a complete literary work into a structured Basic Memory knowledge graph. Covers schema design, entity seeding, chapter-by-chapter processing, cross-referencing, validation, and graph exploration."
---

# Memory Literary Analysis

Transform a complete literary work into a structured knowledge graph. Characters, themes, chapters, locations, symbols, and literary devices become interconnected notes — searchable, validatable, and traversable.

## When to Use

- Analyzing a novel, play, poem, or non-fiction book end-to-end
- Building a teaching or study resource for a literary text
- Creating a book club companion knowledge base
- Research projects requiring structured close reading
- Stress-testing Basic Memory at scale (~200+ notes, 1000+ relations)

## Pipeline Overview

```
Phase 0: Setup         → project, schemas, directory structure
Phase 1: Seed          → stub notes for known major entities
Phase 2: Process       → chapter-by-chapter notes in batches
Phase 3: Cross-ref     → enrich arcs, add parallels, write analysis
Phase 4: Validate      → schema checks, drift detection, consistency
Phase 5: Explore       → traverse the graph, write synthesis notes
```

## Tools

Writing always goes through `write_note` and `edit_note`. For *reading* — which is most of
the work in a long analysis — prefer the POSIX read verbs where they are available
(`enable_posix_tools` for the MCP tools; the `bm` CLI verbs are always available):

| Need | Use | Instead of |
|------|-----|-----------|
| A section of a long note | `cat <note> --section Observations` | reading the whole note |
| A line range of the source text | `cat <source>.txt --lines 4200-4890` | pulling the whole book into context |
| Notes matching frontmatter | `find --meta status=active` | reading notes to check fields |
| Fields across many notes | `find --meta ... --fields pov,setting` | one read per note |
| Where something lives | `ls`, `tree`, `find --name '*.md'` | listing everything |

The two rules that matter across a 100+ chapter run:

- **Never read a note to check a field.** That is what `--meta` predicates and `--fields`
  projection are for — one call answers what a read-per-note loop would cost.
- **Never pull a whole file into context to reach one part of it.** Sections and line ranges
  slice the *output*: the full note is still fetched, then cut down before it is returned. What
  they save is context, not I/O — a long chapter or a full source text costs you the tokens of
  the relevant part, not of the whole file.

These compound. In measured runs, predicate queries replaced 28-call scans with a single
call; across 138 chapters that difference is the run.

Three sharp edges to know before you write a query. The first two fail *quietly* — a wrong
answer, exit 0, no warning — so learn them here rather than from a graph you thought you had
audited:

- **`--meta` matches case-sensitively, and the stored value is always snake_case.** `note_type`
  is an alias for the frontmatter `type:` key, compared with SQL `=`. `write_note` normalizes
  `note_type` through `to_snake_case` before writing, so a note authored as `note_type="Chapter"`
  is stored as `type: chapter` — the casing you author with is *not* the casing on disk. Query
  the snake_case form: `--meta 'note_type=chapter'`. The capitalized spelling returns zero rows
  and exit 0. The value a result row displays is the value to query with.
- **`find` pages, and the default page is 10.** Any query whose answer is "all N chapters"
  needs `--page-size 200` (the maximum) — see [Coverage Checks](#coverage-checks).
- **`--name` cannot combine with `--meta`.** The metadata search has no filename glob. Scope a
  `--meta` query with the positional path instead: `find /characters --meta
  'note_type=character'`. That path is matched on a directory boundary against the *file path*
  a note is indexed under — where the note actually lives, not its permalink, which stops
  mirroring the file path once a note pins `permalink:` in frontmatter or is moved. So
  `/characters` reaches everything filed under `characters/` (including `characters/major/`),
  and never `characters-cut/`.

If the POSIX verbs are unavailable, every step below still works with `search_notes`,
`read_note`, and `list_directory` — it just costs more.

## Phase 0: Setup

### Create the Project

```python
create_memory_project(name="<work-name>", path="~/basic-memory/<work-name>")
```

Use a kebab-case slug of the work's title (e.g., `great-gatsby`, `hamlet`, `beloved`).

### Define Schemas

Write 6 schema notes to `schema/`. Each schema defines the entity type's fields, observation categories, and relation types. Adapt fields to fit the work — the schemas below are starting points, not rigid templates.

#### Character Schema

```python
write_note(
  title="Character",
  directory="schema",
  note_type="schema",
  metadata={
    "entity": "Character",
    "version": 1,
    "schema": {
      "role(enum)": "[protagonist, antagonist, supporting, minor], character's narrative role",
      "description": "string, brief character description",
      "first_appearance?": "string, chapter or scene of first appearance",
      "status?(enum)": "[alive, dead, unknown, transformed], character status at end of work"
    },
    "settings": {"validation": "warn"}
  },
  content="""# Character

Schema for character entity notes.

## Observations
- [convention] Major characters in characters/major/, minor in characters/minor/
- [convention] Observation categories: trait, motivation, arc, quote, appearance, relationship, symbolism, fate
- [convention] Relations: appears_in, contrasts_with, allied_with, commands, symbolizes, associated_with"""
)
```

Add work-specific fields as needed — e.g., `rank` for military fiction, `house` for family sagas, `species` for fantasy.

#### Theme Schema

```python
write_note(
  title="Theme",
  directory="schema",
  note_type="schema",
  metadata={
    "entity": "Theme",
    "version": 1,
    "schema": {
      "description": "string, what this theme explores",
      "prevalence(enum)": "[major, minor], how central to the work",
      "first_introduced?": "string, where theme first appears"
    },
    "settings": {"validation": "warn"}
  },
  content="""# Theme

Schema for thematic analysis notes.

## Observations
- [convention] Observation categories: definition, manifestation, evolution, counterpoint, quote, interpretation
- [convention] Relations: embodied_by, contrasts_with, reinforced_by, explored_in, expressed_through"""
)
```

#### Chapter Schema

```python
write_note(
  title="Chapter",
  directory="schema",
  note_type="schema",
  metadata={
    "entity": "Chapter",
    "version": 1,
    "schema": {
      "chapter_number": "integer, sequential chapter number",
      "pov?": "string, point-of-view character or narrator mode",
      "setting?": "string, primary location",
      "narrative_mode?(enum)": "[dramatic, expository, reflective, epistolary, mixed], chapter's primary mode"
    },
    "settings": {"validation": "warn"}
  },
  content="""# Chapter

Schema for chapter-level analysis notes.

## Observations
- [convention] Chapters stored in chapters/ directory
- [convention] Observation categories: summary, event, tone, technique, quote, significance, foreshadowing
- [convention] Relations: features, set_in, explores, contains, employs, follows, precedes, parallels"""
)
```

#### Location Schema

```python
write_note(
  title="Location",
  directory="schema",
  note_type="schema",
  metadata={
    "entity": "Location",
    "version": 1,
    "schema": {
      "description": "string, what this place is",
      "location_type(enum)": "[city, building, landscape, body_of_water, region, fictional, vehicle], type of place",
      "real_or_fictional(enum)": "[real, fictional, both], whether the place exists"
    },
    "settings": {"validation": "warn"}
  },
  content="""# Location

Schema for location and setting notes.

## Observations
- [convention] Observation categories: description, atmosphere, symbolism, significance, geography
- [convention] Relations: setting_for, associated_with, symbolizes, contains, part_of"""
)
```

#### Symbol Schema

```python
write_note(
  title="Symbol",
  directory="schema",
  note_type="schema",
  metadata={
    "entity": "Symbol",
    "version": 1,
    "schema": {
      "description": "string, what the symbol is literally",
      "symbol_type(enum)": "[object, animal, color, action, natural_phenomenon, body_part], category of symbol",
      "primary_meaning": "string, most common interpretation"
    },
    "settings": {"validation": "warn"}
  },
  content="""# Symbol

Schema for symbolic element notes.

## Observations
- [convention] Observation categories: meaning, appearance, ambiguity, interpretation, quote, evolution
- [convention] Relations: represents, associated_with, appears_in, contrasts_with, located_at"""
)
```

#### LiteraryDevice Schema

```python
write_note(
  title="LiteraryDevice",
  directory="schema",
  note_type="schema",
  metadata={
    "entity": "LiteraryDevice",
    "version": 1,
    "schema": {
      "description": "string, what the device is",
      "device_type(enum)": "[rhetorical, structural, figurative, narrative, dramatic], category",
      "frequency(enum)": "[pervasive, frequent, occasional, rare], how often used"
    },
    "settings": {"validation": "warn"}
  },
  content="""# LiteraryDevice

Schema for literary technique and device notes.

## Observations
- [convention] Observation categories: definition, usage, effect, example, significance
- [convention] Relations: used_in, characterizes, expresses, related_to"""
)
```

### Directory Structure

```
<project>/
  <work>.txt         # the source text, verbatim (see Phase 2)
  schema/            # 6 schema definitions
  chapters/          # one note per chapter/section + prologue/epilogue
  characters/
    major/           # protagonist, antagonist, key supporting
    minor/           # named characters with limited roles
  themes/            # thematic analysis notes
  locations/         # settings and places
  symbols/           # symbolic elements
  literary-devices/  # techniques and devices
  analysis/          # cross-cutting synthesis
  tasks/             # processing tracker
```

## Phase 1: Seed Entities

Before processing chapters, create stub notes for major entities so `[[wiki-links]]` resolve from the start.

### Characters (major)

For each major character, create a stub with known metadata:

```python
write_note(
  title="<Character Name>",
  directory="characters/major",
  note_type="Character",
  tags=["character", "major", "<role>"],
  metadata={"role": "<role>", "description": "<brief description>"},
  content="""# <Character Name>

## Observations
- [role] <Character's role in the work>
- [appearance] <Key physical description>

## Relations
- associated_with [[<Related Character>]]
- appears_in [[<Key Location>]]"""
)
```

### Seed Checklist

Identify the work's major entities before you start reading. A good starting inventory:

| Type | Typical Count | What to Include |
|------|--------------|-----------------|
| Characters (major) | 8-20 | Protagonist, antagonist, key supporting cast |
| Themes | 5-12 | Central concerns the work explores |
| Locations | 4-10 | Primary settings, symbolically significant places |
| Symbols | 4-10 | Recurring objects, images, or motifs with layered meaning |

Stubs don't need to be complete — they give `[[wiki-link]]` targets and will be enriched during chapter processing.

## Phase 2: Chapter Processing

### Source Text Preparation

Obtain the full text and identify chapter/section boundaries. For public domain works, Project Gutenberg is a good source. For copyrighted works, work from a physical or licensed digital copy.

**Put the source text inside the project directory, as `.txt`, and index it once.** `bm cat`
resolves a *note identifier*, not a filesystem path — it can only reach a file the project
index has observed. A one-time index pass gives the raw text an entity row, after which the
line-range slice works against it:

```bash
cp ~/Downloads/moby-dick.txt ~/basic-memory/moby-dick/moby-dick.txt
bm reindex --search -p moby-dick          # one pass; the .txt becomes readable
```

Two constraints that make this the right shape, both worth respecting:

- **Keep it `.txt`, do not convert it to `.md`.** Basic Memory injects frontmatter into
  markdown notes, which shifts every line number by the height of that block — an offset map
  built from the original file would then be silently wrong. A `.txt` is stored verbatim, so
  its line numbers stay 1:1 with the file on disk.
- **Keep it inside the project.** A source text elsewhere on disk is not an entity, and
  `bm cat` answers `Error: Entity not found`. If you must leave it outside, drop the BM verbs
  for the source and use plain shell (`sed -n '4200,4890p' <path>`) — the notes still get the
  BM verbs, only the raw source falls back to the shell.

**Then build a chapter offset map once, before processing.** Scan the text for chapter
headings and record the line range of each chapter, then read chapters by range rather than
re-reading the whole book into context:

```bash
grep -n '^CHAPTER ' ~/basic-memory/moby-dick/moby-dick.txt   # heading -> line number
bm cat moby-dick.txt --lines 4200-4890 --plain               # returns one chapter, not the whole text
```

`grep -n` here is the shell's grep on a filesystem path (this is the map-building step, and
it needs the file). `bm cat` then takes the *note identifier* — `moby-dick.txt`, the file's
path within the project — and returns exactly that slice plus a `lines 4200-4890 of N`
footer. `bm head moby-dick.txt -n 40` is the cheap way to eyeball the heading format before
writing the grep pattern.

Store the map in the project (a note or a small JSON file) so later batches — and a resumed
run after context compaction — do not have to rediscover it. On a long work this is the
single largest context saving in the pipeline.

### Batching Strategy

Process ~10 chapters per batch to balance depth with progress. Group by narrative arc or thematic focus:

| Batch | Typical Content |
|-------|----------------|
| 1 | Opening: setting, character introductions, world-building |
| 2-3 | Rising action: conflicts established, relationships develop |
| 4-6 | Middle: complications, turning points, thematic deepening |
| 7-8 | Climax approach: escalation, revelations, crises |
| Final | Climax, resolution, epilogue |

Adjust batch size based on chapter length and density. Short, action-heavy chapters can be batched in larger groups; long, philosophically dense chapters may need smaller batches.

### Per-Chapter Workflow

For each chapter:

**1. Read the chapter carefully.** Read the chapter's line range from the offset map
(`bm cat <source>.txt --lines <start>-<end>`), not the whole file. Read the actual text —
never work from memory or a summary; textual evidence is the entire point.

**2. Create the chapter note:**

```python
write_note(
  title="Chapter <N> - <Title>",
  directory="chapters",
  note_type="Chapter",
  tags=["chapter", "<arc-phase>"],
  metadata={
    "chapter_number": <N>,
    "pov": "<narrator or POV character>",
    "setting": "<primary location>",
    "narrative_mode": "<mode>"
  },
  content="""# Chapter <N> - <Title>

## Observations
- [summary] <1-2 sentence synopsis>
- [event] <Key plot events>
- [tone] <Emotional and stylistic atmosphere>
- [technique] <Notable narrative techniques>
- [quote] "<Significant passage>"
- [significance] <Why this chapter matters to the whole>
- [foreshadowing] <Hints at future events>

## Relations
- features [[<Character>]]
- set_in [[<Location>]]
- explores [[<Theme>]]
- contains [[<Symbol>]]
- employs [[<Literary Device>]]
- follows [[Chapter <N-1> - <Previous Title>]]
- precedes [[Chapter <N+1> - <Next Title>]]"""
)
```

**3. Enrich related entities:**

```python
edit_note(
  identifier="characters/major/<character-slug>",
  operation="append",
  heading="Observations",
  content="""- [arc] Ch.<N>: <What happens to this character>
- [quote] "<Attributed quote>" (Ch.<N>)"""
)
```

**4. Track progress** using the memory-tasks skill to create a processing task that survives context compaction.

### What to Capture Per Chapter

| Category | What to Look For |
|----------|-----------------|
| `[summary]` | 1-2 sentence chapter synopsis |
| `[event]` | Key plot events (actions, revelations, arrivals) |
| `[tone]` | Emotional and stylistic atmosphere |
| `[technique]` | Narrative innovations (POV shifts, structural experiments, genre blending) |
| `[quote]` | Memorable or thematically significant passages |
| `[significance]` | Why this chapter matters to the whole |
| `[foreshadowing]` | Hints at future events |

### Entity Enrichment Per Chapter

As each chapter is processed, append observations to relevant entities:
- **Characters**: `[arc]` moments, new `[trait]` revelations, `[quote]` attributions
- **Themes**: `[manifestation]` in this chapter, `[evolution]` shifts
- **Symbols**: `[appearance]` with context, new `[interpretation]` angles
- **Locations**: `[atmosphere]` as described, `[significance]` in scene
- **Literary devices**: `[example]` from this chapter

### Adding Prose and Interpretation

After the structured observations are in place, consider adding interpretive prose to major entity notes. Prepend 2-4 paragraphs of critical essay before the Observations section using `edit_note(operation="prepend")`. This prose should:

- Argue for a reading of the character, theme, or symbol — not just describe it
- Connect the entity to the work's larger concerns and to literary tradition
- Include subjective opinions clearly marked as such ("In my reading...", "I find...")
- Ground claims in textual evidence cited by chapter number

The prose adds the interpretive texture that structured observations alone cannot capture.

## Phase 3: Cross-Referencing

After all chapters are processed:

### Find What Needs Enriching

Do not re-read every note to decide what is thin. Query for it:

```bash
bm find --meta 'note_type=chapter' --fields chapter_number,pov,setting --page-size 200
bm find --meta 'note_type=character' --fields role,status --page-size 200   # who is still a stub
bm find --meta 'chapter_number>100' --fields pov --page-size 200           # late-book POV drift
```

A field a note never set comes back as a blank cell (`null` under `--json`), so rows with
blanks are the work queue. This turns "audit the graph" from a read of every note into one
call per question.

Note the lowercase `chapter`/`character` — `write_note` snake-cases `note_type` before the
note is written, so that is the value on disk no matter how your Phase 0 schemas spelled it.
Match it exactly; the capitalized spelling returns zero rows and exit 0. And `--page-size 200`
is not decoration: without it these return the first 10 rows and the work queue looks ten
items long.

### Character Arcs
For each major character, write a full `[arc]` summary observation covering their trajectory across the work.

### Theme Evolution
For each theme, add `[evolution]` observations tracing how it develops from introduction to resolution.

### Chapter Parallels
Add `parallels` and `contrasts_with` relations between structurally similar chapters (e.g., mirrored scenes, repeated settings, thematic echoes).

### Analysis Notes
Create synthesis notes in `analysis/`:

```python
write_note(
  title="Narrative Structure",
  directory="analysis",
  note_type="note",
  tags=["analysis", "structure"],
  content="""# Narrative Structure

Analysis of the work's narrative architecture.

## Observations
- [structure] <Overall arc description>
- [technique] <Key narrative strategies>
...

## Relations
- analyzes [[<Protagonist>]]
- analyzes [[<Key Character>]]
- explores [[<Central Theme>]]
..."""
)
```

Recommended analysis notes:
- **Narrative Structure** — overall architecture and pacing
- **Work Overview** — synthesis of the complete work (summary, thesis, legacy)
- **Critical Reception** — historical and contemporary interpretations

### Discover Emergent Entities
During chapter processing, new minor characters, locations, and symbols will emerge. Create notes for any that appear in 3+ chapters or carry thematic weight.

## Phase 4: Validation

### Schema Validation

```python
# Validate each entity type
schema_validate(noteType="Character")
schema_validate(noteType="Theme")
schema_validate(noteType="Chapter")
schema_validate(noteType="Location")
schema_validate(noteType="Symbol")
schema_validate(noteType="LiteraryDevice")
```

### Drift Detection

```python
schema_diff(noteType="Character")
# ... for each type
```

Fix issues found — common fixes:
- Missing required observation categories → add them via `edit_note`
- Enum values outside allowed set → correct metadata
- Fields in notes but not schema → add as optional to schema if legitimate

### Coverage Checks

Schema validation proves notes match their shape. These prove the graph is *complete*:

```bash
bm find --meta 'note_type=chapter' --fields chapter_number --page-size 200   # every chapter present?
bm find --meta 'note_type=chapter' --fields pov,setting --page-size 200      # missing context?
bm find /characters --meta 'note_type=character' --fields role --page-size 200  # inventory vs. seed list
```

**A coverage check that pages is not a coverage check.** `bm find` defaults to
`--page-size 10`, so the un-sized form of the first query "proves" a 138-chapter work has 10
chapters. 200 is the maximum page size; past that, iterate with `--page 2`, `--page 3`, … .
Scope a `--meta` query with the positional path (`/characters`), never `--name` — the two
options are mutually exclusive, because the metadata search has no filename glob. The
positional path scopes by the *file path* a note is indexed under, matched on a directory
boundary: `/characters` admits `characters/major/ahab.md` but never `characters-cut/`. It is
not a permalink match, so a note that pins its own `permalink:` is still found where its file
lives.

Read the count off the footer, not off the rows you can see. Every `find` result reports
`page 1 • total 138`, and appends `• more available (--page)` when the page truncated the
answer — that suffix appearing is the check *failing*, whatever the visible rows say.

For the sequence gap — the failure that a count alone cannot catch — take the numbers from
`--json`, which carries `total`, `total_is_exact`, and `has_more`:

```bash
expected=138; page=1; rows='[]'
while :; do
  resp=$(bm find --meta 'note_type=chapter' --fields chapter_number \
           --page-size 200 --page $page --project <work> --json)
  rows=$(jq -n --argjson acc "$rows" --argjson r "$resp" \
           '$acc + [$r.results[] | {title, n: .fields.chapter_number}]')
  [ "$(jq -r '.has_more' <<<"$resp")" = "true" ] || break
  page=$((page + 1))
done
jq -n --argjson rows "$rows" --argjson expected "$expected" '
  ([$rows[] | select(.n != null and (.n | tostring | test("^[0-9]+$"))) | .n | tonumber]) as $n
  | { total:        ($rows | length),
      unnumbered:   [$rows[] | select(.n == null or (.n | tostring | test("^[0-9]+$") | not))
                             | .title],
      missing:      ([range(1; $expected + 1)] - $n),
      duplicates:   ($n | group_by(.) | map(select(length > 1) | .[0])),
      out_of_range: ($n | map(select(. < 1 or . > $expected)) | unique) }'
```

The loop is not ceremony. `--page-size` caps at 200, so a single call cannot inventory a work
with more than 200 chapters — and rerunning it with `--page 2` *replaces* the numbers rather
than accumulating them, which reports chapters 1-200 as missing on a corpus that is complete.
Walk until `has_more` is false and check the union.

Pass the work's **actual** chapter count as `$expected` — deriving the range from the highest
number found lets an incomplete graph pass. With 138 rows numbered 1..137 plus one duplicate,
a max-derived check reports `missing: []` while a chapter is genuinely absent: the duplicate
keeps the count right and the missing tail moves the goalpost. The check passes on
`unnumbered: []`, `missing: []`, `duplicates: []`, **and** `out_of_range: []` together, over
the combined pages.

`unnumbered` is not decoration either. A `chapter` note that never got a `chapter_number` comes
back as `null`, and feeding that straight to `tonumber` aborts the whole pipeline with `null
cannot be parsed as a number` — so the check *crashes on exactly the malformed inventory it
exists to find*. Partitioning first turns that into a named row.

`out_of_range` is not hypothetical: a prologue or epilogue typed as `chapter` lands at 0 or at
`$expected + 1`, and without that key the report reads clean — every expected number present,
none repeated — while the inventory holds a note the numbering does not account for. Type
front and back matter as its own note type, or widen `$expected` deliberately.

A gap in the middle of a batch is the most common processing failure and the easiest to miss
by eye; a duplicated chapter number is the second, and it hides the first.

### Relation Consistency
Spot-check bidirectional relations: if Chapter X `features [[Character]]`, does Character have observations referencing Chapter X? Fix gaps.

Orphans are the other half of this check — a note with no inbound or outbound relations is
either genuinely isolated or was never linked back into the graph:

```bash
bm orphans          # entities with no relations in the graph
```

Graph quality is relation *density*, not note count. A pass that adds notes while leaving
orphans behind has made the graph worse.

## Phase 5: Explore the Graph

With the graph complete, traverse it to find what the chapter-by-chapter pass could not see:

```bash
bm tool build-context 'memory://characters/major/*' --depth 2         # the character web
bm find --meta 'note_type=theme' --fields prevalence --page-size 200  # thematic weight
bm grep -F "doubloon" --page-size 100 --project <work>                # every mention of a symbol
```

`build-context` takes its URL as a positional argument — there is no `--url` option.

`grep` defaults to semantic ranking and a page of 10, which answers "what is this about?" but
quietly truncates "where does this appear?" — a symbol in 40 chapters comes back as 10. For
symbol tracing, pass `-F` for literal matching and raise `--page-size`; the meaning shifts you
are hunting are usually in the later occurrences, which the default would have dropped.

`--page-size` raises the ceiling, it does not remove it. A symbol in a long work can exceed
even 100, so check whether the last page was full and walk `--page 2`, `--page 3` until it is
not. A truncated symbol search fails the same silent way as an unpaginated `find`: a plausible
answer, exit 0, and no sign that the tail is missing.

And `grep` searches **your notes, not the source**. The `<work>.txt` is indexed as an entity,
but its body is not in the searchable text, so an occurrence you never carried into a note is
unreachable — verified: a word present only in the source returns `total: 0` while a word in
both returns just the note. So this answers "where have I written about the doubloon", not
"where does the doubloon appear in the book". For the latter, search the file itself and use
the [chapter offset map](#source-text-preparation) to turn a hit into a chapter.

Traversal is where second-order questions get answered — which characters share the most
chapters, which themes converge in the final act, where a symbol's meaning shifts. Capture
what you find as `analysis/` notes; those syntheses are the payoff of having built the graph.

## Adapting to Other Genres

This pipeline works for any literary text. Adjust schemas for genre:

| Genre | Schema Adjustments |
|-------|-------------------|
| **Novel** | Base schemas work as-is; add genre-specific Character fields as needed |
| **Play** | Add `Act` and `Scene` schemas; Character gets `speaking_lines` field |
| **Poetry collection** | Replace Chapter with `Poem`; add `form`, `meter`, `rhyme_scheme` fields |
| **Non-fiction** | Replace Chapter with `Section`; add `Argument`, `Evidence` schemas |
| **Short story collection** | Add `Story` schema with `narrator`, `setting`, `word_count` |
| **Epic/myth** | Add `Deity`, `Prophecy` schemas; Location gets `mythological_significance` |
| **Memoir** | Character schema gets `relationship_to_narrator`; add `Memory` schema |

### Scaling Guidance

| Work Length | Batch Size | Estimated Notes |
|-------------|-----------|----------------|
| Novella (~40K words) | 5-10 chapters | ~50-80 |
| Novel (~80K words) | 8-12 chapters | ~100-150 |
| Long novel (~200K+ words) | 10-15 chapters | ~200-300 |
| Series (multiple volumes) | 1 volume at a time | ~200+ per volume |

## Related Skills

- **memory-schema** — Schema creation, validation, and drift detection
- **memory-tasks** — Track chapter processing progress across context compaction
- **memory-notes** — Note writing patterns, observation categories, wiki-links
- **memory-ingest** — Processing external input into structured entities
- **memory-metadata-search** — Querying notes by frontmatter fields
- **memory-lifecycle** — Archiving completed analysis phases

## Guidelines

- **Seed before processing.** Create entity stubs first so wiki-links resolve immediately during chapter processing.
- **Batch for sanity.** Processing ~10 chapters at a time balances depth with momentum. Track progress with a Task note.
- **Read the source text.** Don't rely on memory or summaries. Read (or re-read) the actual text for each batch before creating notes. Textual evidence is everything.
- **Read narrowly.** Keep the source text in the project as `.txt`, index it once, build the chapter offset map once, then read chapters by line range and notes by section. On a long work, whole files landing in context are the largest avoidable cost in the pipeline.
- **Query, don't scan.** When you need to know which notes have a field, ask with `--meta` predicates and `--fields` projection. Reading notes to check frontmatter is the mistake this pipeline makes at scale. Two ways these queries lie quietly: `--meta` is case-sensitive against the frontmatter `type:` your schemas authored, and `find` returns 10 rows unless you pass `--page-size`.
- **Observations are your index.** The knowledge graph's value comes from categorized observations. Be generous with categories and specific with content.
- **Relations are your web.** Every chapter should link to characters, themes, locations, and devices. Every entity should link back to chapters where it appears.
- **Enrich iteratively.** Entity notes grow richer with each chapter. Don't try to write the perfect character note upfront — append as you go.
- **Add prose for depth.** After structured data is in place, add interpretive essays to major notes. The prose captures what observations cannot: argument, nuance, opinion, and voice.
- **Validate periodically.** Run `schema_validate` after each batch, not just at the end. Catch drift early.
- **Quote generously.** Literary analysis lives on textual evidence. Include significant quotes as `[quote]` observations with chapter attribution.
- **Review and revise.** After completing all chapters, review the full graph from an external perspective. Look for thin notes, missing connections, and gaps in coverage. The first pass is never the last.
- **Analysis comes last.** Synthesis notes in `analysis/` should be written after all chapters are processed, when you have the full picture.
