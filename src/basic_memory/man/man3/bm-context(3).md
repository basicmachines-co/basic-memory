---
title: bm-context(3)
type: manpage
section: 3
name: bm-context
summary: follow the knowledge graph from a note
generated: registry
tool: bm_context
verified: 0.24.1 mcp
---

# bm-context(3)

## NAME

**bm-context** — follow the knowledge graph from a note

## SYNOPSIS

```
bm_context(identifier, depth=1, since="7d", max_related=10, compact=False,
           page=1, page_size=10, output_format="json", project=None)
```

## DESCRIPTION

Returns a note, its observations, and the notes its relations reach, up to
`depth` hops (1-5). `identifier` is a path, permalink, `memory://` URL, or a
pattern such as `plans/*`. `since` limits related results to recent changes and
`max_related` caps them.

A pattern selects notes as the primary results. Their observations and relations
remain grouped under those notes. A pattern that names no note but addresses
relation or observation permalinks still selects those rows directly.

`compact` returns the graph without note bodies or observations; read chosen
notes with bm_read. Output defaults to JSON because the result is a graph; text
output names each note's path and ends with the next-page call when there is
one.

## PARAMETERS

- **identifier** (string, required) — Where to start: a note path, permalink, memory:// URL, or a pattern such as 'specs/*'.
- **depth** (integer, optional, default: 1) — Relation hops to follow (1-5).
- **since** (string, optional, default: "7d") — How far back to look: '7d', '24h', '2 days ago', 'last week', or a date. Windows shorter than one day are widened to one day.
- **max_related** (integer, optional, default: 10) — Most related results to return (0-100).
- **compact** (boolean, optional, default: False) — Omit note bodies and observations; keep the graph shape.
- **page** (integer, optional, default: 1) — Page number.
- **page_size** (integer, optional, default: 10) — Primary results per page (1-50).
- **output_format** (optional, default: "json")
- **project** (string | null, optional, default: None) — Project name, 'workspace/project', or project external id. With multiple addressable projects, scope note and search calls explicitly with this parameter or a project-qualified target.

## EXAMPLES

```
bm_context(identifier="plans/launch-plan.md", compact=True, output_format="text",
           project="demo")
# → # Context: launch-plan
#
#   ## launch-plan
#   permalink: demo/plans/launch-plan
#   path: plans/launch-plan.md
#
#   ### Relations
#   - depends_on [[Release Checklist]]
#
#   ### Related
#   - [[Release Checklist]] (plans/Release Checklist.md)
```

## SEE ALSO

- see_also [[bm-read(3)]]
- see_also [[bm-search(3)]]
