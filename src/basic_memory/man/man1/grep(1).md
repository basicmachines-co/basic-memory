---
title: grep(1)
type: manpage
section: 1
name: grep
summary: search note content from the shell
generated: hand
---

# grep(1)

## NAME

**grep** — search note content from the shell

## SYNOPSIS

```
bm grep PATTERN [-F | --literal] [--page N] [--page-size N]
        [-C N | --context-lines N] [--max-matches N]
        [--json | --plain] [--project NAME | --project-id UUID]
        [--local | --cloud]
```

## DESCRIPTION

Searches note content, semantically when semantic search is enabled for the
project, full-text otherwise. `-F` (`--literal`) forces literal full-text
matching, like real grep's fixed-strings flag. Results carry title, score,
permalink, and the matched snippet; on a TTY they render as a table, and
`--json` (or piped output) emits the search response with pagination.

With `-F -C N`, return compact literal match windows instead. The full-text index
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

- **-F, --literal** — literal full-text matching instead of semantic search
- **-C, --context-lines** — opt into line scanning with 0-10 lines around each match; requires -F
- **--max-matches** — matching lines to show per candidate in line mode, 1-100 (default 10)
- **--page, --page-size** — result pagination (defaults 1 and 10)

## EXAMPLES

```
bm grep -F "retry" -C 3 --max-matches 5 --plain
bm grep "auth token rotation"
bm grep -F "BASIC_MEMORY_FORCE_LOCAL"
bm grep "deploy checklist" --json | jq '.results[].permalink'
```

## SEE ALSO

- see_also [[cat(1)]]
- see_also [[find(1)]]
- see_also [[search-notes(3)]]
