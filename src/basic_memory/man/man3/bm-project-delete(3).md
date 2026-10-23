---
title: bm-project-delete(3)
type: manpage
section: 3
name: bm-project-delete
summary: remove a project
generated: registry
tool: bm_project_delete
verified: 0.24.1 mcp
---

# bm-project-delete(3)

## NAME

**bm-project-delete** — remove a project

## SYNOPSIS

```
bm_project_delete(name, delete_notes=False, workspace=None,
                  output_format="text")
```

## DESCRIPTION

Removes a project from Basic Memory. A local project's files stay on disk
unless `delete_notes=true`; a Cloud project's files are always deleted and can
be recovered only from a snapshot. This cannot be undone.

The tool is hidden unless the `enable_project_delete` setting is on
(`BASIC_MEMORY_ENABLE_PROJECT_DELETE=true`). The default project cannot be
deleted.

## PARAMETERS

- **name** (string, required) — The project to delete, as bm_project(command='list') names it.
- **delete_notes** (boolean, optional, default: False) — Also delete the project's note files.
- **workspace** (string | null, optional, default: None) — In Cloud: the workspace slug, name or id that holds the project.
- **output_format** (optional, default: "text")

## EXAMPLES

```
bm_project_delete(name="scratch")
# → ✓ Project 'scratch' removed successfully
#   ...
#   Note files remain on disk but the project is no longer tracked by Basic Memory.
#   Re-add the project to access its content again.
```

## SEE ALSO

- see_also [[bm-project(3)]]
