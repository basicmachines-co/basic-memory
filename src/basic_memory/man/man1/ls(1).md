---
title: ls(1)
type: manpage
section: 1
name: ls
summary: list one directory level of a project
generated: cli
---

# ls(1)

## NAME

**ls** — list one directory level of a project

## SYNOPSIS

```
bm ls [PATH] [--glob GLOB] [--sort SORT] [--page PAGE]
      [--page-size PAGE_SIZE] [--project PROJECT] [--json]
      [--local | --cloud]
```

## DESCRIPTION

Lists the immediate contents of one directory (default: the project root)
through `bm_list`. `--glob` filters file names and `--sort` orders files;
folders always come first. `--json` emits the listing with pagination and
totals.

`bm ls` lists files inside one project; the unrelated `bm project ls` lists
projects.

Without `--project` the configured default project is used. A path may start
with the project's own name (`<project>/<path>`) when it agrees with `--project`;
a prefix naming a different project is refused. Projects in another cloud
workspace are addressed as `--project <workspace-slug>/<project>` or
`--project <uuid>`; `bm project list` lists every workspace's projects.

## OPTIONS

- **--glob** — File-name glob, e.g. '*.md'
- **--sort** — File order
- **--page** — Page number (1-indexed)
- **--page-size** — Results per page (1-100)
- **--project** — Project name, 'workspace/project', or project id. Defaults to the default project.
- **--json** — Print the tool's JSON result
- **--local** — Force local API routing (ignore cloud mode)
- **--cloud** — Force cloud API routing

## EXAMPLES

```
bm ls
bm ls /specs
bm ls /notes --glob '*.md' --sort updated_desc
```

## SEE ALSO

- see_also [[find(1)]]
- see_also [[tree(1)]]
- see_also [[bm-list(3)]]
