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

bm find [PATH] --meta PREDICATE [--meta PREDICATE ...] [--fields LIST]
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
predicate must hold, and `PATH` still scopes the results — server-side, by
file-path prefix, so the totals are exact and every page is reachable. The
payload becomes the search response shape (the same one `bm grep` returns),
not the directory listing. Non-markdown files carry no frontmatter and are
never metadata hits.

The scope matches the *file path* a note is indexed under, not its permalink.
A permalink stops mirroring its file path the moment a note pins `permalink:`
in its frontmatter, or is moved while `update_permalinks_on_move` is off (the
default), so scoping by permalink would drop notes that really are under the
named directory and admit notes that are not. The prefix matches on a
directory boundary and case-sensitively, identically on SQLite and Postgres:
`/specs` admits `specs/api.md`, never `specs-archive/api.md` or `Specs/api.md`,
and a `_` or `%` in a directory name is an ordinary character, not a wildcard.
Only the surrounding separators and a leading `./` are notation — the plain
listing reads them the same way, so one `PATH` names one subtree with or
without `--meta`. Everything else belongs to the path, including whitespace: a
directory named `" specs "` is addressed by that exact spelling.
`PATH` may also name a project (`bm find myproject --meta ...`) — that is a
routing prefix, a mount point rather than a subtree, and scopes to that
project's root.

`--fields` is the SELECT to the predicates' WHERE: it adds a `fields` object
to each hit carrying the named frontmatter values, so a filtered set can be
tabulated without reading every note. A field a hit does not carry renders as
null; the row is never dropped.

`--name` and `--depth` are refused alongside `--meta`. The search API has no
filename glob, and its path scope is whole-subtree, where a depth bound is not
expressible — refusing beats silently ignoring either and misreporting the
match set. Scope with `PATH` instead. `--fields` without `--meta` is refused
for the same honesty: without predicates there is nothing to project.

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
owner=null                 key missing or explicitly null
```

Values are JSON-scalar inferred: `true`/`false`/`null` and numbers become
booleans, null, and numbers. Quote a token to force the literal string —
`status="true"` matches the four-character string. Quoting also protects a
comma inside a list element: `label in "a,b",c` matches `a,b` or `c`. An
unterminated quote is a typo, not a value: `status="active` is refused rather
than searched for as the text `"active`.

`null` matches only through `=`, and it means "this note carries no value
here" — the key is absent from the frontmatter, or present and explicitly
null. Both backends extract those two cases identically, so `owner=null`
answers the same note set on SQLite and Postgres. The other operators compare
against their value and a SQL comparison with null is never true, so
`score>null` and `priority in null,high` are refused instead of answering a
confident zero.

Numbers must be finite. `score=NaN`, `score=Infinity` and an overflowing
exponent like `score=1e999` are refused by the grammar rather than failing
later as an encoding error; quote one (`score="NaN"`) to match the literal
text.

Keys accept dot-paths into nested frontmatter (`review.approved`), and
`note_type` is accepted as a spelling of the frontmatter `type` key, matching
`search-notes(3)`. A key is dot-separated names of letters, digits, `_` or
`-`, so a doubled, leading or trailing dot (`review..approved`, `.owner`,
`owner.`) is refused by the grammar rather than spent as a request the
search API will reject. Any other operator (`!=` among them — the search
API has no not-equals) fails fast, naming the supported set. That includes a
mis-spelled multi-character operator: `status==active`, `status=>active` and
`count>>3` are refused rather than read as the values `=active`, `>active`
and `>3`. An unquoted value may therefore not begin with `=`, `<` or `>`;
quote one that genuinely does, as in `range=">=5"`.

## OPTIONS

- **--name** — file-name glob, e.g. `"*.md"`; omitted matches everything.
  Cannot combine with `--meta`
- **--depth** — recursion depth, 1-10 (default 10). A non-default depth
  cannot combine with `--meta`
- **--meta** — frontmatter predicate, repeatable; see PREDICATE GRAMMAR.
  Switches the payload to the search response shape
- **--fields** — comma-separated frontmatter fields to show per hit, e.g.
  `"title,priority"`; dot-paths allowed, in the same shape predicate keys
  take, and a malformed one is refused rather than shown as null for every
  hit. A field a note does not carry shows as null. Requires `--meta`
- **--page, --page-size** — pagination (defaults 1 and 10)

## EXAMPLES

```
bm find --name "*.md"
bm find /specs --depth 3
bm find /notes --name "auth*" --plain
bm find --meta "status=active"
bm find /specs --meta "status=active" --meta "confidence>0.6"
bm find myproject --meta "status=active"
bm find --meta "owner=null" --fields title
bm find --meta "tags has security,oauth" --fields title,priority
bm find --meta "status=active" --fields title --plain
```

## SEE ALSO

- see_also [[ls(1)]]
- see_also [[tree(1)]]
- see_also [[grep(1)]]
- see_also [[list-directory(3)]]
- see_also [[search-notes(3)]]
