---
title: cat(1)
type: manpage
section: 1
name: cat
summary: print a note's content from the shell
generated: hand
---

# cat(1)

## NAME

**cat** — print a note's content from the shell

## SYNOPSIS

```
bm cat IDENTIFIER [--lines N-M | --section HEADING] [--max-tokens N]
       [--frontmatter | --no-frontmatter] [--json | --plain]
       [--project NAME | --project-id UUID] [--local | --cloud]
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

- **--lines** — line range; cannot combine with --section
- **--section** — heading slice; the response's line range supports
  follow-up `--lines` reads
- **--max-tokens** — truncate at a section/paragraph boundary
- **--frontmatter/--no-frontmatter** — include the YAML block (ignored for
  section/token slices)

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
