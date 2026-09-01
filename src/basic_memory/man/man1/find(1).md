---
title: find(1)
type: manpage
section: 1
name: find
summary: recursively list files, or query notes by frontmatter metadata
generated: hand
---

# find(1)

## NAME

**find** — recursively list files, or query notes by frontmatter metadata

## SYNOPSIS

```
bm find [PATH] [--name GLOB] [--depth N] [--page N] [--page-size N]
        [--json | --plain] [--project NAME | --project-id UUID]
        [--local | --cloud]

bm find [PROJECT] --meta PREDICATE [--meta PREDICATE ...] [--fields LIST]
        [--page N] [--page-size N] [--json | --plain]
        [--project NAME | --project-id UUID] [--local | --cloud]
```

## DESCRIPTION

Two modes, chosen by `--meta`.

Without `--meta`, find recursively lists files under a directory (default:
the project root), optionally filtered by a file-name glob. Depth is bounded
1-10 by the directory API. On a TTY results render as a table; `--plain`
prints one path per line, find(1) style; `--json` (or piped output) emits the
listing with pagination and totals.

With `--meta`, find queries notes by their frontmatter instead: every
predicate must hold, across the whole project. The payload becomes the search
response shape (the same one `bm grep` returns), not the directory listing.
Totals are exact and every page is reachable. Non-markdown files carry no
frontmatter and are never metadata hits.

`--fields` is the SELECT to the predicates' WHERE: it adds a `fields` object
to each hit carrying the named frontmatter values, so a filtered set can be
tabulated without reading every note. A field a hit does not carry renders as
null; the row is never dropped.

`--name`, `--depth`, and a `PATH` below the project root are all refused
alongside `--meta`, because the search API expresses none of them: no
filename glob, no depth bound, and no file-path filter. Its one path-shaped
predicate is a permalink prefix, and a permalink is not a file path — a note
that pins `permalink:` in its frontmatter, or that was moved while
`update_permalinks_on_move` is off (the default), keeps a permalink that no
longer says where the file lives. Scoping by it would drop notes that really
are under the named directory, admit notes that are not, and still report the
count as exact. Refusing beats misreporting the match set. `PATH` may still
name a project (`bm find myproject --meta ...`) — that is a routing prefix,
not a subtree. `--fields` without `--meta` is refused for the same honesty:
without predicates there is nothing to project.

## PREDICATE GRAMMAR

One predicate per `--meta`, one predicate per key; repeated flags AND
together. A repeated key is an error, not last-wins — use `between` for a
range.

```
status=active              equality
confidence>0.6             comparison: > >= < <=
priority in high,critical  any of the listed values
tags has security,oauth    array contains ALL listed values
score between 0.3,0.8      inclusive range
```

Values are JSON-scalar inferred: `true`/`false`/`null` and numbers become
booleans, null, and numbers. Quote a token to force the literal string —
`status="true"` matches the four-character string. Quoting also protects a
comma inside a list element: `label in "a,b",c` matches `a,b` or `c`.

Keys accept dot-paths into nested frontmatter (`review.approved`), and
`note_type` is accepted as a spelling of the frontmatter `type` key, matching
`search-notes(3)`. Any other operator (`!=` among them — the search API has
no not-equals) fails fast, naming the supported set. That includes a
mis-spelled multi-character operator: `status==active`, `status=>active` and
`count>>3` are refused rather than read as the values `=active`, `>active`
and `>3`. An unquoted value may therefore not begin with `=`, `<` or `>`;
quote one that genuinely does, as in `range=">=5"`.

## OPTIONS

- **--name** — file-name glob, e.g. `"*.md"`; omitted matches everything.
  Cannot combine with `--meta`
- **--depth** — recursion depth, 1-10 (default 10). A non-default depth
  cannot combine with `--meta`. Nor can a `PATH` below the project root
- **--meta** — frontmatter predicate, repeatable; see PREDICATE GRAMMAR.
  Switches the payload to the search response shape
- **--fields** — comma-separated frontmatter fields to show per hit, e.g.
  `"title,priority"`; dot-paths allowed. Requires `--meta`
- **--page, --page-size** — pagination (defaults 1 and 10)

## EXAMPLES

```
bm find --name "*.md"
bm find /specs --depth 3
bm find /notes --name "auth*" --plain
bm find --meta "status=active"
bm find --meta "status=active" --meta "confidence>0.6"
bm find myproject --meta "status=active"
bm find --meta "tags has security,oauth" --fields title,priority
bm find --meta "status=active" --fields title --plain
```

## SEE ALSO

- see_also [[ls(1)]]
- see_also [[tree(1)]]
- see_also [[grep(1)]]
- see_also [[list-directory(3)]]
- see_also [[search-notes(3)]]
