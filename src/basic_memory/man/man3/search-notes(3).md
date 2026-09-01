---
title: search-notes(3)
type: manpage
section: 3
name: search-notes
summary: search the knowledge base by text, similarity, or metadata
generated: registry
tool: search_notes
verified: 0.21.6 mcp+cli
---

# search-notes(3)

## NAME

**search-notes** — search the knowledge base by text, similarity, or metadata

## SYNOPSIS

MCP:

```
search_notes(query=None, project=None, project_id=None,
             search_all_projects=False, page=1, page_size=10,
             search_type=None, output_format="text", note_types=None,
             entity_types=None, categories=None, after_date=None,
             metadata_filters=None, tags=None, status=None,
             min_similarity=None, valid_at=None, valid_overlaps=None,
             time_role=None)
```

CLI:

```
bm tool search-notes [QUERY] [--project NAME] [--search-type TYPE]
                     [--page N] [--page-size N] [--local | --cloud] ...
```

## DESCRIPTION

One tool, three retrieval modes, and a structured-metadata filter layer that
composes with all of them.

**Retrieval modes** (`search_type`): `hybrid` (default when semantic search
is enabled — full-text and vector results fused), `text` (SQLite FTS with
boolean operators, phrases, and prefix patterns), `title`, `permalink`, and
`vector`/`semantic` (similarity only, tunable via `min_similarity`).

**Filters** compose with any mode, or stand alone with no query at all:
`note_types` (frontmatter `type:`), `entity_types` (entity vs observation
rows), `categories` (observation categories, paired with
`entity_types=["observation"]`), `tags`, `status`, `after_date`, and
`metadata_filters` — equality matches against arbitrary frontmatter fields,
which is how the manual implements apropos (see [[Manpage]]).

**Valid time** (`valid_at`, `valid_overlaps`, `time_role`) queries what a note
*says was true*, not when it was last edited. Observations can carry a
qualifier — a range, `- [decision] @effective[2026-06-10,2026-07-27) ...`, or
a point, `- [decision] @effective:2026-07-27 ...` / `- [decision] @2026-07-27
...` — and these filters match against that authored interval. A point means
the span its precision covers: `@2026` is that year, `@2026-06` that month,
and `@2026-06-10` from that date onward. It is a separate axis from
`after_date`, which keeps filtering last-indexed time. Bounds follow
PostgreSQL range conventions, calendar dates and instants never convert into
one another, and a source with no qualifier is excluded from any valid-time
query. Because one note can carry several assertions that disagree, these
queries return observation-level results, each carrying the assertion that
matched.

## PARAMETERS

- **query** — search string; optional. Omit it for filter-only searches
- **search_type** — see modes above; default is dynamic (`hybrid` if semantic
  search is enabled, else `text`)
- **metadata_filters** — dict of frontmatter field → value; integer values
  match integer YAML fields (`{"section": 3}` works). A `None` value is an
  is-null match — notes where the key is absent or explicitly null. `None`
  inside `$in`, `$between`, a contains list, or a comparison is refused: those
  compare against the value, and a comparison with null is never true
- **tags** — list or comma string, same convention as [[write-note(3)]]
- **min_similarity** — float override for vector/hybrid threshold; `0.0`
  shows everything, `0.8` is high precision
- **valid_at** — date (`2026-07-28`) or RFC 3339 instant
  (`2026-07-28T09:00:00Z`) that the authored range must contain; a timestamp
  written without an offset is read as UTC (aliases: `as_of`, `valid_on`)
- **valid_overlaps** — range literal the authored range must overlap:
  `[2026-06-10,2026-07-27)`, `(,2026-07-27]`, `[2026-06-10,)`. Mutually
  exclusive with `valid_at` (aliases: `overlaps`, `valid_during`)
- **time_role** — valid-time axis: `effective`, `valid`, `occurred`, `due`,
  or `mentioned`; usable on its own (aliases: `role`, `time_axis`)
- **search_all_projects** — opt-in cross-project search; ignored when
  `project`/`project_id` is given
- **page**, **page_size** — pagination (aliases: `page_number`, `limit`,
  `per_page`)

## MCP USAGE

All verified against this project:

```
search_notes(project="manual", query="frontmatter AND metadata",
             search_type="text")
# → 2 results, total: 2, has_more: false  (FTS gives exact totals)

search_notes(project="manual", query="write-note", search_type="title")
# → 1 result

search_notes(project="manual", tags="manpage-example", note_types=["note"])
# → filter-only search, no query needed

search_notes(project="manual",
             metadata_filters={"type": "manpage", "section": 3})
# → apropos: every section-3 page of this manual
```

## CLI EQUIVALENT

```
bm tool search-notes "conflict error" --project manual --page-size 2
# → hybrid results as JSON (QUERY is positional)
```

## GOTCHAS

- [gotcha] Hybrid and vector searches return total: 0 even with results — counting would cost a second semantic pass, so only has_more is meaningful there; exact totals exist only in text/title/permalink modes #pagination
- [gotcha] Score semantics differ by mode: FTS rank scores in text mode, similarity scores in hybrid/vector — don't compare across modes #scoring
- [gotcha] The CLI takes QUERY positionally; there is no --query flag #cli-parity
- [gotcha] search_all_projects is silently ignored when a project is specified #routing
- [gotcha] A valid-time filter excludes every source without a temporal qualifier — an undated note makes no claim about when it holds, so drop the filter to search dated and undated content together #valid-time
- [gotcha] valid_at and valid_overlaps never mix calendar dates with instants: a date query matches only date ranges and an instant query only instant ranges, so `2026-07-27` and `2026-07-27T00:00:00Z` are different questions #valid-time
- [gotcha] A timestamp written without an offset is read as UTC, in an authored qualifier and in a filter alike — same convention as every other naive datetime in Basic Memory #valid-time
- [gotcha] An authored token that does not read as a date is left as ordinary observation content with no warning; only an unknown role (`@asserted:2026-06-10`) is reported #valid-time

## SEE ALSO

- see_also [[read-note(3)]]
- see_also [[build-context(3)]]
- see_also [[recent-activity(3)]]
- see_also [[bm-note(5)]]
