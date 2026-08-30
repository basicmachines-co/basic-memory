---
title: chatgpt-fetch(3)
type: manpage
section: 3
name: chatgpt-fetch
summary: OpenAI-actions-compatible document fetch adapter
generated: hand
tool: fetch
verified: 0.21.6 mcp
---

# chatgpt-fetch(3)

## NAME

**chatgpt-fetch** (tool name: `fetch`) — OpenAI-actions-compatible document fetch adapter

## SYNOPSIS

```
fetch(id)
```

## DESCRIPTION

**Availability:** `fetch` answers only OpenAI clients (ChatGPT connectors).
Any other MCP client gets the error `Unsupported MCP client` without a
search being run — use [[search-notes(3)]] and [[read-note(3)]] instead.

Companion to [[chatgpt-search(3)]]: takes an `id` from a search result
(permalink, title, or memory URL) and returns the full document as a
JSON-in-text payload with `id`, `title`, `text` (the full markdown including
frontmatter), `url`, and `metadata.format`.

## MCP USAGE

Verified:

```
fetch("manual/man3/delete-project-3")
# → [{"type": "text", "text": "{\"id\": ..., \"title\": \"Delete Project 3\",
#    \"text\": \"---\\ntitle: delete-project(3)...\", \"url\": ...,
#    \"metadata\": {\"format\": \"markdown\"}}"}]
```

## GOTCHAS

- [gotcha] The returned title is title-cased from the permalink ("Delete Project 3"), not the note's actual title ("delete-project(3)") #fidelity
- [gotcha] Same session-active-project routing as chatgpt-search — no project parameter #routing

## SEE ALSO

- see_also [[chatgpt-search(3)]]
- see_also [[read-note(3)]]
