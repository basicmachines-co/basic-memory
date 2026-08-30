---
title: cloud-info(3)
type: manpage
section: 3
name: cloud-info
summary: return Basic Memory Cloud overview and setup guidance (hosted server only)
generated: hand
tool: cloud_info
verified: 0.21.6 mcp
---

# cloud-info(3)

## NAME

**cloud-info** — return Basic Memory Cloud overview and setup guidance (hosted server only)

## SYNOPSIS

```
cloud_info()
```

## DESCRIPTION

**Availability:** registered only on the hosted Basic Memory Cloud MCP
server. It was removed from the local server in v0.22 (#1145), so a local
client will not find this tool.

Returns a static markdown blurb describing the optional cloud add-on
(hosted access, cross-device sync, multi-client workflows) and the
`bm cloud login` entry point. Exists so agents can answer "what is Basic
Memory Cloud?" without leaving MCP. No parameters, read-only.

## MCP USAGE

Verified:

```
cloud_info()
# → "# Basic Memory Cloud (optional) ..." markdown
```

## SEE ALSO

- see_also [[list-workspaces(3)]]
