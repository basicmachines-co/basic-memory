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
bm cat IDENTIFIER [--lines LINES] [--section SECTION]
       [--max-tokens MAX_TOKENS] [--frontmatter | --no-frontmatter] [--json]
       [--plain] [--project PROJECT] [--project-id PROJECT_ID] [--local]
       [--cloud]
```

## DESCRIPTION

Prints one note, resolved exactly by title, permalink, or memory:// URL.
The content can be sliced by a 1-indexed inclusive line range (`--lines
"20-40"`, `"20-"` to the end, `"20"` for one line), by a heading
(`--section Decisions`, path form `Auth/Decisions`, or `Heading[1]` for a
duplicate), or truncated to an approximate token budget (`--max-tokens`).

On a TTY the note renders as formatted Markdown; `--plain` writes the raw
content to stdout (slice details go to stderr); `--json`, or piped output,
emits the structured payload with slice metadata (start_line, end_line,
total_lines, truncated, continue_line).

## OPTIONS

- **--lines** — Line range "N-M", "N-" (to end), or "N" (one line); 1-indexed inclusive
- **--section** — Heading slice: "Decisions", "Auth/Decisions", or "Heading[1]"
- **--max-tokens** — Approximate token budget; truncates at a section/paragraph boundary
- **--frontmatter / --no-frontmatter** (default: --frontmatter) — Include the YAML frontmatter block (ignored for section/token slices)
- **--json** — Output raw JSON instead of formatted display
- **--plain** — Output undecorated plain text (no colors/markup), even when piped
- **--project** — The project to use. If not provided, the default project will be used.
- **--project-id** — Project external_id (UUID). Takes precedence over --project; use to disambiguate same-named projects across cloud workspaces.
- **--local** — Force local API routing (ignore cloud mode)
- **--cloud** — Force cloud API routing

## EXAMPLES

```
bm cat specs/search
bm cat specs/search --lines 20-40 --plain
bm cat specs/search --section Decisions --max-tokens 500
```

## SEE ALSO

- see_also [[head(1)]]
- see_also [[grep(1)]]
- see_also [[read-note(3)]]
