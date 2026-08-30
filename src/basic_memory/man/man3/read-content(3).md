---
title: read-content(3)
type: manpage
section: 3
name: read-content
summary: read raw file bytes without knowledge-graph processing
generated: hand
tool: read_content
verified: 0.21.6 mcp
---

# read-content(3)

## NAME

**read-content** — read raw file bytes without knowledge-graph processing

## SYNOPSIS

```
read_content(path, project=None, project_id=None)
```

## DESCRIPTION

Returns a file's raw content with `content_type` and `encoding` metadata —
no identifier cascade, no miss suggestions, no graph awareness. This is the
tool for non-note files (images, canvas files, binaries) and for reading a
note exactly as it sits on disk. Accepts a file path, permalink, or
memory:// URL.

## MCP USAGE

Verified:

```
read_content("man5/bm-note(5).md", project="manual")
# → {"type": "text", "text": "---\ntitle: bm-note(5)...",
#    "content_type": "text/markdown", "encoding": "utf-8"}
```

## GOTCHAS

- [gotcha] File paths include the extension ("man5/bm-note(5).md"); permalinks do not ("manual/man5/bm-note-5") — both are accepted but they are different namespaces #identifiers
- [gotcha] No CLI wrapper exists; for local projects plain cat is the equivalent #cli-parity

## SEE ALSO

- see_also [[read-note(3)]]
- see_also [[view-note(3)]]
- see_also [[canvas(3)]]
