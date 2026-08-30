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

- **type** — filter by item type: `entity` (default), `observation`,
  `relation`, or a list combining them; case-insensitive
- **depth** — relation hops to include around recent items (1–3 recommended)
- **timeframe** — how far back to look (aliases: `since`, `time_range`,
  `lookback`)
- **project** / **project_id** — target project; omitted, the active or
  default project is used, and discovery mode only when neither resolves
- **output_format** — `text` (human summary grouped by kind) or `json`
  (flat item list)

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
