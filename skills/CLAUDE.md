# AGENTS.md

This file provides guidance to coding agents when working with Basic Memory skills in this repository.

## Project Overview

Skills collection for [Basic Memory](https://github.com/basicmachines-co/basic-memory) — a local-first knowledge graph built on markdown files and MCP. Each skill teaches AI assistants how to effectively use Basic Memory's MCP tools.

Skills can be installed via `npx skills add` or copied manually into any agent's skills directory.

## Repository Structure

Each skill is a single `SKILL.md` file in its own directory:

```
memory-tasks/SKILL.md           # Task tracking across context compaction
memory-schema/SKILL.md          # Schema lifecycle (discover, infer, validate, drift)
memory-reflect/SKILL.md         # Sleep-time memory consolidation
memory-notes/SKILL.md           # Note writing patterns and knowledge graph design
memory-defrag/SKILL.md          # Memory reorganization and cleanup
memory-metadata-search/SKILL.md # Structured metadata filtering and queries
memory-lifecycle/SKILL.md       # Entity status transitions and folder-based archival
memory-ingest/SKILL.md          # Process external input into structured entities
memory-research/SKILL.md        # Web research synthesized into Basic Memory entities
memory-literary-analysis/SKILL.md # Literary analysis knowledge graph pipeline
memory-curate/SKILL.md          # Knowledge-graph curation: orphans, relations, tags, hub notes
memory-continue/SKILL.md        # Resume prior work by rebuilding context from the graph
memory-capture/SKILL.md         # Capture a thread's current state into one coherent note
```

There is no code to compile — this is a pure markdown project. Run `just check`
from `skills/` or `just package-check-skills` from the monorepo root to validate
the `memory-*/SKILL.md` frontmatter before committing changes.

## SKILL.md Format

Every skill file has YAML frontmatter with `name` (kebab-case identifier) and `description` (one-sentence summary), followed by markdown content:

```markdown
---
name: memory-<name>
description: "What the skill does and when to use it."
---

# Skill Title

[Sections: When to Use, Key Concepts, How-to, Examples, Guidelines]
```

## Adding a New Skill

1. Create `memory-<name>/SKILL.md` with frontmatter and markdown instructions
2. Update `README.md` with the new skill's summary
3. Commit and push

## Key Concepts Referenced in Skills

- **Notes**: Markdown files representing entities in the Basic Memory knowledge graph
- **Frontmatter**: YAML metadata (title, type, tags, custom fields)
- **Observations**: Categorized facts — `- [category] content #tags`
- **Relations**: Wiki-links creating graph edges — `- relation_type [[Target Note]]`
- **Schema / Picoschema**: Compact YAML definitions for note structure validation
- **Memory URLs**: `memory://path-to-note` for programmatic access
- **MCP Tools**: `write_note`, `read_note`, `edit_note`, `move_note`, `delete_note`, `search_notes`, `build_context`, `schema_validate`, `schema_infer`, `schema_diff`
