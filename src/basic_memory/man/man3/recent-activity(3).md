---
title: recent-activity(3)
type: manpage
section: 3
name: recent-activity
summary: list recently changed notes, observations, and relations
generated: hand
tool: recent_activity
verified: 0.21.6 mcp+cli
---

# recent-activity(3)

## RETIRED MCP TOOL

`recent_activity` was removed from the MCP surface in 1.0. It is not registered
and has no compatibility alias. Use `bm_project(command="recent")` instead;
[[bm-project(3)]] documents its current parameters and examples.

## CLI

The shell CLI retains its own interface:

```
bm tool recent [--project NAME] [--timeframe SPEC]
                        [--type TYPE] [--depth N] [--page N] [--page-size N]
```

## SEE ALSO

- see_also [[bm-project(3)]]
