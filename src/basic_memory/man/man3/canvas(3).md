---
title: canvas(3)
type: manpage
section: 3
name: canvas
summary: generate an Obsidian canvas visualization
generated: hand
tool: canvas
verified: 0.21.6 mcp
---

# canvas(3)

## NAME

**canvas** — generate an Obsidian canvas visualization

## SYNOPSIS

```
canvas(nodes, edges, title, directory,
       project=None, project_id=None)
```

## DESCRIPTION

Writes a `<title>.canvas` file following the JSON Canvas 1.0 spec, openable
in Obsidian. Nodes are dicts (`type: "file"` referencing project notes by
file path, or `type: "text"` for free-standing labels) with explicit
x/y/width/height geometry; edges connect node ids and may carry labels.

Because file nodes reference real notes, a canvas stays live: opening it in
Obsidian shows the current content of each page.

## MCP USAGE

Verified — this manual's own graph diagram:

```
canvas(title="manual-graph", directory="diagrams", project="manual",
       nodes=[{"id": "n1", "type": "file",
               "file": "man7/basic-memory(7).md",
               "x": 0, "y": 0, "width": 360, "height": 120}, ...],
       edges=[{"id": "e1", "fromNode": "n1", "toNode": "n2",
               "label": "see_also"}, ...])
# → "Created: diagrams/manual-graph.canvas"
```

## GOTCHAS

- [gotcha] file nodes use file paths with extension ("man7/basic-memory(7).md"), not permalinks #identifiers
- [gotcha] All geometry is manual — nothing auto-layouts; compute x/y yourself #layout
- [gotcha] Canvas files are not notes: they don't enter the knowledge graph and search won't find their contents #indexing

## SEE ALSO

- see_also [[read-content(3)]]
- see_also [[build-context(3)]]
