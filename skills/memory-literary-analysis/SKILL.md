---
name: memory-literary-analysis
description: "Analyze a complete literary work into a structured Basic Memory knowledge graph. Covers schema design, entity seeding, chapter-by-chapter processing, cross-referencing, validation, and visualization."
---

# Memory Literary Analysis

Transform a complete literary work into a structured knowledge graph. Characters, themes, chapters, locations, symbols, and literary devices become interconnected notes — searchable, validatable, and visualizable.

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
Phase 5: Visualize     → canvas files for character webs, timelines
```

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

**1. Read the chapter carefully.** If working from a source text file, read the relevant section.

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

### Relation Consistency
Spot-check bidirectional relations: if Chapter X `features [[Character]]`, does Character have observations referencing Chapter X? Fix gaps.

## Phase 5: Visualization

Generate canvas files for visual exploration:

```python
# Character relationship web
canvas(query="type:Character AND role:protagonist OR role:antagonist OR role:supporting")

# Theme connections
canvas(query="type:Theme")

# Chapter timeline with key events
canvas(query="type:Chapter", layout="timeline")
```

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
- **Observations are your index.** The knowledge graph's value comes from categorized observations. Be generous with categories and specific with content.
- **Relations are your web.** Every chapter should link to characters, themes, locations, and devices. Every entity should link back to chapters where it appears.
- **Enrich iteratively.** Entity notes grow richer with each chapter. Don't try to write the perfect character note upfront — append as you go.
- **Add prose for depth.** After structured data is in place, add interpretive essays to major notes. The prose captures what observations cannot: argument, nuance, opinion, and voice.
- **Validate periodically.** Run `schema_validate` after each batch, not just at the end. Catch drift early.
- **Quote generously.** Literary analysis lives on textual evidence. Include significant quotes as `[quote]` observations with chapter attribution.
- **Review and revise.** After completing all chapters, review the full graph from an external perspective. Look for thin notes, missing connections, and gaps in coverage. The first pass is never the last.
- **Analysis comes last.** Synthesis notes in `analysis/` should be written after all chapters are processed, when you have the full picture.
