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

- **url** (string, required) — memory:// URI pointing to discussion content (e.g. memory://specs/search), or a bare permalink path.
- **project** (string | null, optional, default: None) — Project name to build context from. Optional - server will resolve using hierarchy. If unknown, use list_memory_projects() to discover available projects.
- **project_id** (string | null, optional, default: None) — Project external_id (UUID). Prefer this over `project` when known — it routes to the exact project regardless of name collisions across cloud workspaces. Takes precedence over `project`. Get from list_memory_projects().
- **depth** (string | integer | null, optional, default: 1) — How many relation hops to traverse (1-3 recommended for performance)
- **timeframe** (string | null, optional, default: "7d") — How far back to look. Supports natural language like "2 days ago", "last week"
- **page** (integer, optional, default: 1) — Page number of results to return (default: 1)
- **page_size** (integer, optional, default: 10) — Number of primary results to return per page (default: 10, maximum: 50)
- **max_related** (integer, optional, default: 10) — Maximum total related results to return (default: 10, maximum: 100)
- **output_format** (string, optional, default: "json") — Response format - "json" for structured JSON dict, "text" for compact markdown text

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
