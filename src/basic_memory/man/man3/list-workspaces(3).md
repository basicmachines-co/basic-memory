---
title: list-workspaces(3)
type: manpage
section: 3
name: list-workspaces
summary: list cloud workspaces available to the authenticated user
generated: hand
tool: list_workspaces
verified: 0.21.6 mcp+cli
---

# list-workspaces(3)

## NAME

**list-workspaces** — list cloud workspaces available to the authenticated user

## SYNOPSIS

MCP:

```
list_workspaces(output_format="text")
```

CLI:

```
bm tool list-workspaces
```

## DESCRIPTION

Returns the cloud tenants the current user belongs to: `tenant_id`, `slug`,
`name`, `workspace_type`, `role`, default flag, and subscription status. The
`slug` is the preferred selector to pass as `workspace` to
[[create-memory-project(3)]] and friends; `tenant_id` is the routing
authority underneath.

For local-only users with no cloud discovery, a display-only "Personal"
workspace is synthesized so the response is never empty.

## MCP USAGE

Verified:

```
list_workspaces(output_format="json")
# → {"workspaces": [{"tenant_id": "5ccbae40-...",
#                    "slug": "basic-memory-7020de4e...",
#                    "name": "Basic Memory", "role": "owner",
#                    "is_default": true, ...}, ...],
#    "count": 2, "default_workspace_id": "5ccbae40-..."}
```

## GOTCHAS

- [gotcha] The synthesized "personal" workspace for local-only users is display-only — it is not valid as a routing selector #routing

## SEE ALSO

- see_also [[list-memory-projects(3)]]
- see_also [[create-memory-project(3)]]
