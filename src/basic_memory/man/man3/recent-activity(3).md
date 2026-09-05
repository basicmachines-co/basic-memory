---
title: recent-activity(3)
type: manpage
section: 3
name: recent-activity
summary: list recently changed notes, observations, and relations
generated: registry
tool: recent_activity
verified: 0.21.6 mcp+cli
---

# recent-activity(3)

## NAME

**recent-activity** — list recently changed notes, observations, and relations

## SYNOPSIS

MCP:

```
recent_activity(type="", depth=1, timeframe="7d", page=1, page_size=10,
                project=None, project_id=None, output_format="text")
```

CLI:

```
bm tool recent-activity [--project NAME] [--timeframe SPEC]
                        [--type TYPE] [--depth N] [--page N] [--page-size N]
```

## DESCRIPTION

Returns what changed in a project within a timeframe — the episodic side of
the knowledge graph (see [[episodic-memory(7)]]). Timeframes accept natural
language (`"today"`, `"2 days ago"`, `"last week"`) or compact forms
(`"7d"`, `"24h"`).

Project resolution follows the usual order: an explicit `project` /
`project_id`, else the session's active project, else the configured
default project. Only when none of those resolves does the tool switch to
**cross-project discovery mode**, summarizing activity across every project
so an agent can find where recent work happened before drilling in. On a
normal install with a default project, omitting `project` therefore returns
that project's activity — not a cross-project view.

## PARAMETERS

- **type** (string | array, optional, default: "") — Filter by content type(s). Can be a string or list of strings. Valid options: - "entity" or ["entity"] for knowledge entities - "relation" or ["relation"] for connections between entities - "observation" or ["observation"] for notes and observations Multiple types can be combined: ["entity", "relation"] Case-insensitive: "ENTITY" and "entity" are treated the same. Default is entity-only. Specify other types explicitly to include observations and relations.
- **depth** (integer, optional, default: 1) — How many relation hops to traverse (1-3 recommended)
- **timeframe** (string, optional, default: "7d") — Time window to search. Supports natural language: - Relative: "2 days ago", "last week", "yesterday" - Points in time: "2024-01-01", "January 1st" - Standard format: "7d", "24h" Aliases: since, time_range, lookback.
- **page** (integer, optional, default: 1) — Page number for pagination (default 1)
- **page_size** (integer, optional, default: 10) — Number of items per page (default 10)
- **project** (string | null, optional, default: None) — Project name to query. Optional - server will resolve using the hierarchy above: omitted, the active or default project is used, and discovery mode across all projects applies only when neither resolves. If unknown, use list_memory_projects() to discover available projects.
- **project_id** (string | null, optional, default: None) — Project external_id (UUID). Prefer this over `project` when known — it routes to the exact project regardless of name collisions across cloud workspaces. Takes precedence over `project`. Get from list_memory_projects().
- **output_format** (string, optional, default: "text") — "text" returns human-readable summary text. "json" returns a flat list of recent items.

## MCP USAGE

```
recent_activity(project="manual", timeframe="today", page_size=5)
# → "## Recent Activity: manual (today)" with grouped recent notes
#   and a pagination hint
```

## CLI EQUIVALENT

```
bm tool recent-activity --project manual --timeframe 1d
# → JSON: flat list of items, entity type, newest first
```

## GOTCHAS
- [gotcha] Discovery mode is rare in practice — with a default project configured, omitting project returns that project's activity; to survey every project, call list_memory_projects and query each, or search with search_all_projects=True #routing

- [gotcha] Default type filter is entity-only — observations and relations are excluded unless requested explicitly #filtering
- [gotcha] Text mode returns a grouped summary; json mode returns a flat list — the shapes are not interconvertible #output
- [pattern] Start a session with recent_activity on the default project to orient; when work may have landed elsewhere, list_memory_projects then query the likely ones #workflow

## SEE ALSO

- see_also [[search-notes(3)]]
- see_also [[build-context(3)]]
- see_also [[episodic-memory(7)]]
