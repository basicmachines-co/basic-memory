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
bm grep PATTERN [--semantic] [--lines CONTEXT_LINES]
        [--max-matches MAX_MATCHES] [--path PATH] [--page PAGE]
        [--page-size PAGE_SIZE] [--project PROJECT] [--json]
        [--local | --cloud]
```

## DESCRIPTION

Searches note content through `bm_search` in text mode: full-text keyword
matching, so a pattern that appears in no note returns no results. It is not a regex: the pattern is tokenized like any
full-text query. `-s` (`--semantic`) ranks by meaning instead, with hybrid
semantic plus full-text search when semantic search is enabled for the project.
Semantic results are nearest neighbours, so they can include notes that do not
contain the pattern. The text result lists each hit with its path and a match
excerpt; `--json` emits the search response with pagination.

With `-C N`, each hit returns its matching lines with N lines of context
instead of an excerpt. The full-text index selects a page of ranked notes, then
their current content is scanned for lines containing the query's words or
quoted phrases. Line numbers include frontmatter and can be passed directly to
`bm cat ... --lines N-M`. Overlapping context windows merge to avoid repeated
text, and `--max-matches` bounds the lines shown per note.

This is not an exhaustive filesystem grep: index tokenization, stemming, or edits
can rank a note with no matching line, and such notes are dropped from the page.
`total` therefore counts ranked candidates and `total_is_exact` is false; page with
`has_more`. Each hit reports `match_count`, `windows`, and `next_match_line` (the
first omitted matching line, or null). Line positions can change if the note is
edited between calls.

## OPTIONS

- **-s, --semantic** — Rank by meaning instead of matching keywords
- **-C, --lines** — Return matching lines with this many lines of context (0-10)
- **--max-matches** — Matching lines per note with -C (1-100)
- **--path** — Search only this folder
- **--page** — Page number (1-indexed)
- **--page-size** — Results per page (1-100)
- **--project** — Project name, 'workspace/project', or project id. Defaults to the default project.
- **--json** — Print the tool's JSON result
- **--local** — Force local API routing (ignore cloud mode)
- **--cloud** — Force cloud API routing

## EXAMPLES

```
bm grep "retry" -C 3 --max-matches 5
bm grep --semantic "auth token rotation"
bm grep "BASIC_MEMORY_FORCE_LOCAL"
bm grep "deploy checklist" --json | jq '.results[].permalink'
```

## SEE ALSO

- see_also [[cat(1)]]
- see_also [[find(1)]]
- see_also [[bm-search(3)]]
