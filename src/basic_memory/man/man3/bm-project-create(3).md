---
title: bm-project-create(3)
type: manpage
section: 3
name: bm-project-create
summary: create a project whose notes live in a folder
generated: registry
tool: bm_project_create
verified: 1.0.0 mcp
---

# bm-project-create(3)

## NAME

**bm-project-create** — create a project whose notes live in a folder

## SYNOPSIS

```
bm_project_create(name, path, set_default=False, workspace=None,
                  output_format="text")
```

## DESCRIPTION

Creates a project named `name` over the folder `path` and indexes it before
answering. `set_default` makes it the default project. In Cloud, `workspace`
picks the workspace that holds it.

Creating the same project again (the same folder, in any name case or with a
trailing slash) is not an error: the answer says it already exists, and a project
whose first index never ran is indexed then. The same name over another folder is
refused with ALREADY_EXISTS.

While the local server is running, its watcher picks up the new folder at the next
project reload. Once the watch is attached, a one-time index pass includes files
copied in before attachment; later changes use normal file events.

The shell counterpart is `bm project add`.

## PARAMETERS

- **name** (string, required) — The new project's name.
- **path** (string, required) — The folder that holds the project's notes.
- **set_default** (boolean, optional, default: False) — Make the new project the default.
- **workspace** (string | null, optional, default: None) — In Cloud: the workspace slug, name or id that holds the project.
- **output_format** (optional, default: "text")

## EXAMPLES

```
bm_project_create(name="demo", path="/Users/me/demo", set_default=True)
# → ✓ Project 'demo' added successfully
#   ...
#   Project is now available for use in tool calls; indexing completed.
```

## SEE ALSO

- see_also [[bm-project-list(3)]]
- see_also [[bm-project-delete(3)]]
