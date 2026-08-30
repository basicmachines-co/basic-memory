---
title: read-content(3)
type: manpage
section: 3
name: read-content
summary: read a file's content without knowledge-graph processing
generated: hand
tool: read_content
verified: 0.21.6 mcp
---

# read-content(3)

## NAME

**read-content** — read a file's content without knowledge-graph processing

## SYNOPSIS

```
read_content(path, project=None, project_id=None)
```

## DESCRIPTION

Returns a file's content with no identifier cascade, no miss suggestions,
and no graph awareness. Accepts a file path, permalink, or memory:// URL.
What comes back depends on the file type:

- **text** — returned exactly as it sits on disk, with `content_type` and
  `encoding` metadata; this is the way to read a note byte for byte
- **images** — resized and re-encoded as JPEG (base64) to fit a response, so
  the bytes are *not* the original file
- **other binaries** — returned base64-encoded as a document up to 350,000
  bytes; larger files return an error instead of content

## MCP USAGE

Verified:

```
read_content("man5/bm-note(5).md", project="manual")
# → {"type": "text", "text": "---\ntitle: bm-note(5)...",
#    "content_type": "text/markdown", "encoding": "utf-8"}
```

## GOTCHAS

- [gotcha] File paths include the extension ("man5/bm-note(5).md"); permalinks do not ("manual/man5/bm-note-5") — both are accepted but they are different namespaces #identifiers
- [gotcha] Only text is byte-exact — images come back as a re-encoded JPEG and binaries over 350,000 bytes return an error; for original bytes read the file from disk #fidelity
- [gotcha] No CLI wrapper exists; for local projects plain cat is the equivalent #cli-parity

## SEE ALSO

- see_also [[read-note(3)]]
- see_also [[view-note(3)]]
- see_also [[canvas(3)]]
