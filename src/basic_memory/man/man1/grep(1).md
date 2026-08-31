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
        [--json | --plain] [--project NAME | --project-id UUID]
        [--local | --cloud]
```

## DESCRIPTION

Searches note content, semantically when semantic search is enabled for the
project, full-text otherwise. `-F` (`--literal`) forces literal full-text
matching, like real grep's fixed-strings flag. Results carry title, score,
permalink, and the matched snippet; on a TTY they render as a table, and
`--json` (or piped output) emits the search response with pagination.

## OPTIONS

- **-F, --literal** — literal full-text matching instead of semantic search
- **--page, --page-size** — result pagination (defaults 1 and 10)

## EXAMPLES

```
bm grep "auth token rotation"
bm grep -F "BASIC_MEMORY_FORCE_LOCAL"
bm grep "deploy checklist" --json | jq '.results[].permalink'
```

## SEE ALSO

- see_also [[cat(1)]]
- see_also [[find(1)]]
- see_also [[search-notes(3)]]
