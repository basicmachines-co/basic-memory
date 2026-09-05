---
title: create-memory-project(3)
type: manpage
section: 3
name: create-memory-project
summary: create a new project, locally or in a cloud workspace
generated: registry
tool: create_memory_project
verified: 0.21.6 mcp+cli
---

# create-memory-project(3)

## NAME

**create-memory-project** — create a new project, locally or in a cloud workspace

## SYNOPSIS

MCP:

```
create_memory_project(project_name, project_path, set_default=False,
                      workspace=None, output_format="text")
```

CLI:

```
bm project add NAME [PATH] [--cloud] [--workspace SELECTOR]
               [--visibility shared|private] [--local-path PATH]
```

## PARAMETERS

- **project_name** (string, required) — Name for the new project (must be unique)
- **project_path** (string, required) — File system path where the project will be stored
- **set_default** (boolean, optional, default: False) — Whether to set this project as the default (optional, defaults to False)
- **workspace** (string | null, optional, default: None) — Optional cloud workspace selector to create the project in. Slug is preferred for AI callers, but tenant_id and unique name are also accepted. When omitted, the connection's default workspace is used. Discover values via `list_workspaces`. A workspace selector implies cloud routing: without cloud credentials the call fails fast instead of silently creating a local project (#954).
- **output_format** (string, optional, default: "text") — "text" returns the existing human-readable result text. "json" returns structured project creation metadata.

## DESCRIPTION

Creates and registers a project. Local projects take a filesystem path;
cloud projects take a cloud-relative path (`"/manual"`) and an optional
`workspace` selector (slug, name, or tenant id — discover via
[[list-workspaces(3)]]). Creating an already-existing project name returns
the existing project rather than erroring.

## MCP USAGE

Verified (local project):

```
create_memory_project("manual-scratch-952", "/tmp/bm-manual-scratch-952",
                      output_format="json")
# → {"name": "manual-scratch-952", "external_id": "6a3fb50d-...",
#    "created": true, "already_exists": false}
```

## CLI EQUIVALENT

Verified (cloud team-workspace project — this manual's own project):

```
bm project add manual --cloud \
    --workspace "basic-memory-7020de4e..." --visibility shared
# → "Project 'manual' added successfully"
```

## GOTCHAS

- [gotcha] Cloud project paths are tenant-relative ("/manual"); passing one to a local create attempts a literal filesystem mkdir #paths
- [gotcha] Re-creating an existing name is not an error — check already_exists in the json response #semantics

## SEE ALSO

- see_also [[list-memory-projects(3)]]
- see_also [[delete-project(3)]]
- see_also [[list-workspaces(3)]]
