---
title: build-context(3)
type: manpage
section: 3
name: build-context
summary: traverse the knowledge graph outward from a memory:// URL
generated: registry
tool: build_context
verified: 0.21.6 mcp
---

# build-context(3)

## NAME

**build-context** — traverse the knowledge graph outward from a memory:// URL

## SYNOPSIS

MCP:

```
build_context(url, project=None, project_id=None, depth=1, timeframe="7d",
              page=1, page_size=10, max_related=10, output_format="json")
```

CLI:

```
bm tool build-context URL [--project NAME] [--depth N] [--timeframe SPEC]
                      [--max-related N]
```

## DESCRIPTION

The conversation-continuity tool: given a note (or pattern of notes), return
it together with its graph neighborhood — observations, typed relations, and
related entities up to `depth` hops out. This is how an agent rebuilds
working context from a cold start: follow a `memory://` URL captured in an
earlier conversation and the relevant subgraph comes back in one call.

URL forms: `"folder/note"`, `"memory://folder/note"`, and patterns
(`"folder/*"` — but see GOTCHAS for cloud projects). Each traversal step
costs two depth levels internally (relation, then entity).

## PARAMETERS

- **url** — memory:// URI or bare permalink path
- **depth** — relation hops (1–3 recommended; higher gets slow)
- **timeframe** — recency filter on traversed items; natural language
  accepted (`"last week"`, `"2 days ago"`, `"7d"`)
- **max_related** — cap on related results per primary note
- **output_format** — `json` (structured, default) or `text` (compact
  markdown for LLM consumption)

## MCP USAGE

Verified against this manual:

```
build_context("man3/write-note-3", project="manual", depth=1,
              output_format="text")
# → "# Context: write-note(3)" with the page content, its observations,
#   its relations, and related pages like bm-note(5)
```

## GOTCHAS

- [gotcha] Default output_format is json here, unlike most sibling tools that default to text #output
- [gotcha] depth is measured in graph steps where one hop consumes two levels (relation + entity) — depth=1 returns direct neighbors only #traversal
- [pattern] Capture memory:// URLs in conversation summaries and handoffs; build_context on that URL is the cheapest way to restore working state #workflow

## SEE ALSO

- see_also [[read-note(3)]]
- see_also [[search-notes(3)]]
- see_also [[recent-activity(3)]]
- see_also [[bm-relation(5)]]
