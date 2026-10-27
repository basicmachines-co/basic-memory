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

> Cloud-only connector adapter. The local Core server does not register this tool.

## NAME

**chatgpt-fetch** (tool name: `fetch`) — OpenAI-actions-compatible document fetch adapter

## SYNOPSIS

```
fetch(id)
```

## PARAMETERS

- **id** (string, required) — Document identifier (permalink, title, or memory URL)

## DESCRIPTION

**Availability:** `fetch` answers only OpenAI clients (ChatGPT connectors).
Any other MCP client gets the error `Unsupported MCP client` without a
search being run — use [[bm-search(3)]] and [[bm-read(3)]] instead.

Companion to [[chatgpt-search(3)]]: takes an `id` from a search result
(permalink, title, or memory URL) and returns the full document as a
JSON-in-text payload with `id`, `title`, `text` (the Markdown body without opening
frontmatter), `url`, and `metadata.format`.

## MCP USAGE

Verified:

```
fetch("manual/man3/bm-read-3")
# → [{"type": "text", "text": "{...JSON...}"}]
# JSON fields: id, title, text (Markdown body), url, metadata.format
```

## GOTCHAS

- [gotcha] The returned title is title-cased from the permalink ("Delete Project 3") when the body has no opening H1 #fidelity
- [gotcha] Same default-project routing as chatgpt-search — no project parameter #routing

## SEE ALSO

- see_also [[chatgpt-search(3)]]
- see_also [[bm-read(3)]]
