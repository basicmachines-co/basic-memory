---
title: grep(1)
type: manpage
section: 1
name: grep
summary: search note content from the shell
generated: cli
---

# grep(1)

## NAME

**grep** — search note content from the shell

## SYNOPSIS

```
bm grep PATTERN [--semantic] [--context-lines CONTEXT_LINES]
        [--max-matches MAX_MATCHES] [--page PAGE] [--page-size PAGE_SIZE]
        [--json | --plain] [--project PROJECT] [--project-id PROJECT_ID]
        [--local | --cloud]
```

## DESCRIPTION

Searches note content by full-text keyword matching, so a pattern that appears in
no note returns no results. It is not a regex: the pattern is tokenized like any
full-text query. `-s` (`--semantic`) ranks by meaning instead, with hybrid
semantic plus full-text search when semantic search is enabled for the project.
Semantic results are nearest neighbours, so they can include notes that do not
contain the pattern. Results carry title, score, permalink, and the matched
snippet; on a TTY they render as a table, and `--json` (or piped output) emits
the search response with pagination.

With `-C N`, return compact literal match windows instead. The full-text index
selects a page of candidate notes, then their current content is checked for
case-insensitive literal substrings. Matching line numbers include frontmatter
and can be passed directly to `read_note(start_line=..., end_line=...)` or
`bm cat ... --lines N-M`. Overlapping context windows merge to avoid repeated text.
The full body and search excerpt are omitted in this mode, including JSON output.

This is not an exhaustive filesystem grep: index tokenization, stemming, or edits
can produce a candidate with zero literal matches, or omit a substring-only match.
Pagination and totals refer to search candidates, not exact matching notes or lines.
Each candidate reports `match_count`, `total_lines`, `windows`, and `next_match_line`
(the first omitted matching line, or null). Use the latter for a targeted note read.
Line positions can change if the note is edited between calls.

## OPTIONS

- **-s, --semantic** — Rank by meaning instead of matching keywords
- **-C, --context-lines** — Compact line matches with surrounding context (not with --semantic)
- **--max-matches** (default: 10) — Matching lines per candidate in context mode
- **--page** (default: 1) — Page number (1-indexed)
- **--page-size** (default: 10) — Results per page
- **--json** — Output raw JSON instead of formatted display
- **--plain** — Output undecorated plain text (no colors/markup), even when piped
- **--project** — The project to use. If not provided, the default project will be used.
- **--project-id** — Project external_id (UUID). Takes precedence over --project; use to disambiguate same-named projects across cloud workspaces.
- **--local** — Force local API routing (ignore cloud mode)
- **--cloud** — Force cloud API routing

## EXAMPLES

```
bm grep "retry" -C 3 --max-matches 5 --plain
bm grep --semantic "auth token rotation"
bm grep "BASIC_MEMORY_FORCE_LOCAL"
bm grep "deploy checklist" --json | jq '.results[].permalink'
```

## SEE ALSO

- see_also [[cat(1)]]
- see_also [[find(1)]]
- see_also [[search-notes(3)]]
