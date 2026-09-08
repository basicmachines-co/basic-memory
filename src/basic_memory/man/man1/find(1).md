---
title: find(1)
type: manpage
section: 1
name: find
summary: recursively list files, or query notes by frontmatter metadata
generated: cli
---

# find(1)

## NAME

**find** — recursively list files, or query notes by frontmatter metadata

## SYNOPSIS

```
bm find [PATH] [--name NAME] [--depth DEPTH] [--page PAGE]
        [--page-size PAGE_SIZE] [--meta META ...] [--fields FIELDS]
        [--json | --plain] [--project PROJECT] [--project-id PROJECT_ID]
        [--local | --cloud]
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

`--fields` is the SELECT to the predicates' WHERE: each hit comes back as the
note's identity — title, permalink, file path, external id, last-updated — plus
a `fields` object carrying the named frontmatter values, so a filtered set can
be tabulated without reading every note. A field a hit does not carry renders as
null; the row is never dropped. The projection *replaces* the note body rather
than riding alongside it, so a 200-row inventory answers with the values asked
for and not with 200 note bodies. Without `--fields`, hits keep the full search
shape, content included.

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
text. A magnitude no float can hold — a 400-digit integer, which JSON keeps as
an ordinary finite `int` — travels, and the search API refuses it as the filter
error it is rather than as a server error.

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

String equality and `in` predicates on `type` or `note_type` use the same
normalization as note-type search: `Chapter` and `chapter` match the same
notes, as do `LiteraryDevice` and `literary_device`. A `metadata.note_type`
value returned by find can be reused in its next `--meta` predicate.
Other metadata keys remain case-sensitive; null, numeric, range, and array
predicates keep their existing frontmatter comparison behavior.

## OPTIONS

- **--name** — File-name glob, e.g. "*.md"
- **--depth** (default: 10) — Recursion depth (API bound 1-10)
- **--page** (default: 1) — Page number (1-indexed)
- **--page-size** (default: 10) — Nodes per page
- **--meta** — Metadata predicate, repeatable: 'status=active', 'confidence>0.6', 'priority in high,critical', 'tags has security', 'score between 0.3,0.8', 'owner=null' (key missing or null). PATH still scopes the query, by file path
- **--fields** — Comma-separated frontmatter fields to show per hit, e.g. "title,priority" (requires --meta)
- **--json** — Output raw JSON instead of formatted display
- **--plain** — Output undecorated plain text (no colors/markup), even when piped
- **--project** — The project to use. If not provided, the default project will be used.
- **--project-id** — Project external_id (UUID). Takes precedence over --project; use to disambiguate same-named projects across cloud workspaces.
- **--local** — Force local API routing (ignore cloud mode)
- **--cloud** — Force cloud API routing

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
