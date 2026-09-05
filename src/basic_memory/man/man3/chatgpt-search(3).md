---
title: chatgpt-search(3)
type: manpage
section: 3
name: chatgpt-search
summary: OpenAI-actions-compatible search adapter
generated: registry
tool: search
verified: 0.21.6 mcp
---

# chatgpt-search(3)

## NAME

**chatgpt-search** (tool name: `search`) — OpenAI-actions-compatible search adapter

## SYNOPSIS

```
search(query)
```

## PARAMETERS

- **query** (string, required) — Search query (full-text syntax supported by `search_notes`)

## DESCRIPTION

**Availability:** `search` answers only OpenAI clients (ChatGPT connectors).
Any other MCP client gets the error `Unsupported MCP client` without a
search being run — use [[search-notes(3)]] and [[read-note(3)]] instead.

A minimal adapter for clients that expect the OpenAI actions search shape
(ChatGPT connectors). Delegates to [[search-notes(3)]] with defaults
(page 1, size 10) and re-encodes the response as a single text content item
whose body is a JSON string with `results` (id/title/url), `total_count`,
and the echoed `query`.

## MCP USAGE

Verified:

```
search("overwrite conflict")
# → [{"type": "text", "text": "{\"results\": [{\"id\":
#    \"manual/man3/write-note-3\", \"title\": \"write-note(3)\",
#    \"url\": \"manual/man3/write-note-3\"}, ...],
#    \"total_count\": 10, \"query\": \"overwrite conflict\"}"}]
```

## GOTCHAS

- [gotcha] No project parameter exists — the search runs against the session's active project (the last one used), not necessarily the configured default #routing
- [gotcha] The payload is JSON-inside-text by design (OpenAI compatibility); normal MCP clients should prefer search_notes #encoding

## SEE ALSO

- see_also [[search-notes(3)]]
- see_also [[chatgpt-fetch(3)]]
