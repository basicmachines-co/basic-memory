---
title: bm-project-list(3)
type: manpage
section: 3
name: bm-project-list
summary: list the projects you can reach, grouped by workspace
generated: registry
tool: bm_project_list
verified: 1.0.0 mcp
---

# bm-project-list(3)

## NAME

**bm-project-list** — list the projects you can reach, grouped by workspace

## SYNOPSIS

```
bm_project_list(output_format="text")
```

## DESCRIPTION

Lists every project this server can address. Local projects come first; with
Basic Memory Cloud credentials, cloud projects follow, grouped by workspace. Each
row carries the project's name, its qualified `workspace/project` name and its
external id, any of which can be passed as `project` to the other tools.

JSON output has `projects`, `default_project` and `constrained_project`. A server
started for one project (`BASIC_MEMORY_MCP_PROJECT`) lists only that project.

The shell counterpart is `bm project list`.

## PARAMETERS

- **output_format** (optional, default: "text")

## EXAMPLES

```
bm_project_list()
# → Available projects:
#   - demo (local) [b2a21302…]
#   - main (local) [9b4d5944…]
```

## SEE ALSO

- see_also [[bm-project-create(3)]]
- see_also [[bm-recent(3)]]
- see_also [[bm-project-delete(3)]]
