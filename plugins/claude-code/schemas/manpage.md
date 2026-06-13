---
title: Manpage
type: schema
entity: Manpage
version: 1
schema:
  gotcha?(array): string, sharp edges and surprising behavior learned from live verification
  example?(array): string, worked examples beyond the generated synopsis
  pattern?(array): string, recommended idioms and usage patterns
  bug?(array): string, known defects affecting this surface, with issue links
  see_also?(array): Entity, related manual pages — the SEE ALSO graph
settings:
  validation: warn
  frontmatter:
    section(enum, Unix manual section number): [1, 3, 5, 7, 8]
    name: string, page name without section suffix (e.g. write-note)
    summary: string, one-line NAME description
    generated?(enum, who owns the mechanical sections): [registry, typer, hand]
    tool?: string, MCP tool this page documents (section 3 pages)
    command?: string, CLI command this page documents (section 1 pages)
    verified?: string, version and path that verified this page (e.g. 0.21.6 mcp+cli)
    since?: string, version this surface first appeared
---

# Manpage

A **ManpageNote** is one page of a Unix-style manual implemented as Basic
Memory notes (issue #952): commands in section 1, MCP tools in section 3,
file formats in section 5, concepts in section 7, admin in section 8. The
manual becomes a knowledge graph — `SEE ALSO` entries are typed relations,
and pages are found by structured recall:
`search_notes(metadata_filters={"type": "manpage", "section": 3})`.

This schema is an opt-in seed for documentation projects; the canonical
manual lives in the Basic Memory team workspace `manual` project.

## What makes a good ManpageNote

- **NAME / SYNOPSIS / DESCRIPTION** — classic man-page structure, with
  PARAMETERS, MCP USAGE, CLI EQUIVALENT, EXAMPLES, GOTCHAS, SEE ALSO where
  applicable.
- **Verified examples** — EXAMPLES contain only commands that actually ran;
  the `verified` field records the version and path (mcp, cli, or both).
- **generated** — declares regeneration ownership: `registry` (from the MCP
  tool registry) and `typer` (from CLI help) pages get mechanical sections
  rewritten; curated sections (EXAMPLES, GOTCHAS, SEE ALSO, observations)
  are never overwritten.
- **gotcha / bug observations** — field knowledge accumulates on pages
  without being clobbered by regeneration; bugs link their tracking issues.

## Frontmatter

`type: manpage` plus `section` makes the manual queryable like `man -k`:
by section, by `tool`, by `command`, or by missing/stale `verified` stamps.
Validation is `warn`, never blocking.
