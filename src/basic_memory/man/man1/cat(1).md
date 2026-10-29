---
title: cat(1)
type: manpage
section: 1
name: cat
summary: print a note's content from the shell
generated: cli
---

# cat(1)

## NAME

**cat** — print a note's content from the shell

## SYNOPSIS

```
bm cat IDENTIFIER [--lines LINES | --section SECTION]
       [--max-tokens MAX_TOKENS] [--frontmatter | --no-frontmatter]
       [--project PROJECT] [--json] [--local | --cloud]
```

## DESCRIPTION

Prints one note through `bm_read`, resolved by path, permalink, memory://
URL, or exact title. The content can be sliced by a 1-indexed inclusive line range (`--lines
"20-40"`, `"20-"` to the end, `"20"` for one line), by a heading
(`--section Decisions`, path form `Auth/Decisions`, or `Heading[1]` for a
duplicate), or truncated to an approximate token budget (`--max-tokens`).

Line numbers count the whole file, frontmatter included; `--frontmatter` and
`--no-frontmatter` shape only a whole-note read. `--lines` and `--section`
cannot be combined. A section response includes start_line and end_line for
follow-up `--lines` reads.

Without `--project` the configured default project is used. A path may start
with the project's own name (`<project>/<path>`) when it agrees with `--project`;
a prefix naming a different project is refused. Projects in another cloud
workspace are addressed as `--project <workspace-slug>/<project>` or
`--project <uuid>`; `bm project list` lists every workspace's projects.

A whole-note read prints the note itself. A slice prints numbered lines under
a `Lines N-M of T` header with the next range to read. `--json` prints
`bm_read`'s payload with slice metadata (start_line, end_line, total_lines,
has_more, next_start_line, truncated, continue_line).

## OPTIONS

- **--lines** — Line range "N-M", "N-" (to end), or "N" (one line); 1-indexed, frontmatter included
- **--section** — Heading slice: "Decisions", "Auth/Decisions", or "Heading[1]"
- **--max-tokens** — Approximate token budget; cuts at a section or paragraph boundary
- **--frontmatter / --no-frontmatter** (default: --frontmatter) — Include the YAML frontmatter in a whole-note read
- **--project** — Project name, 'workspace/project', or project id. Defaults to the default project.
- **--json** — Print the tool's JSON result
- **--local** — Force local API routing (ignore cloud mode)
- **--cloud** — Force cloud API routing

## EXAMPLES

```
bm cat specs/search
bm cat specs/search --lines 20-40
bm cat specs/search --section Decisions --max-tokens 500
```

## SEE ALSO

- see_also [[head(1)]]
- see_also [[grep(1)]]
- see_also [[bm-read(3)]]
