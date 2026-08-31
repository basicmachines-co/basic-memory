---
title: head(1)
type: manpage
section: 1
name: head
summary: print the first lines of a note
generated: hand
---

# head(1)

## NAME

**head** — print the first lines of a note

## SYNOPSIS

```
bm head IDENTIFIER [-n N] [--frontmatter | --no-frontmatter]
        [--json | --plain] [--project NAME | --project-id UUID]
        [--local | --cloud]
```

## DESCRIPTION

Prints the first `-n` lines (default 10) of one note, resolved exactly by
title, permalink, or memory:// URL. head is `bm cat` with a fixed line
range, so its JSON payload is exactly cat's: content plus start_line,
end_line, and total_lines.

## OPTIONS

- **-n, --lines** — number of lines to print, from line 1 (default 10)
- **--frontmatter/--no-frontmatter** — include the YAML block; with
  --frontmatter (the default) line numbers address the full document,
  frontmatter included, and with --no-frontmatter they address the
  frontmatter-stripped body

## EXAMPLES

```
bm head specs/search
bm head specs/search -n 3 --plain
```

## SEE ALSO

- see_also [[cat(1)]]
- see_also [[tail(1)]]
