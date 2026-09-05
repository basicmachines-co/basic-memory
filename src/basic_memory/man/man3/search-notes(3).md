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
             time_kind=None)
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

**Valid time** (`valid_at`, `valid_overlaps`, `time_kind`) queries what a note
*says was true*, not when it was last edited. Observations can carry a
qualifier — a range, `- [decision] @effective[2026-06-10,2026-07-27) ...`, or
a point, `- [decision] @effective:2026-07-27 ...` / `- [decision] @2026-07-27
...` — and these filters match against that authored interval. A point means
the span its precision covers: `@2026` is that year, `@2026-06` that month,
and `@2026-06-10` from that date onward. An unquoted point is one
whitespace-delimited token; a multi-word or month-only date goes in double
quotes, which end the token at the closing quote: `@occurred:"June 10, 2026"`,
`@"June 2026"`. A relative date is not accepted in a qualifier at all — it would
name a different span on every index pass — so write the date it should mean. It
is a separate axis from `after_date`, which keeps filtering last-indexed time.
Bounds follow
PostgreSQL range conventions, calendar dates and instants never convert into
one another, and a source with no qualifier is excluded from any valid-time
query. Because one note can carry several assertions that disagree, these
queries return observation-level results, each carrying the assertion that
matched.

## PARAMETERS

- **query** (string | null, optional, default: None) — Optional search query string (supports boolean operators, phrases, patterns). Omit or pass None for filter-only searches using metadata_filters, tags, or status.
- **project** (string | null, optional, default: None) — Project name to search in. Optional - server will resolve using hierarchy. If unknown, use list_memory_projects() to discover available projects.
- **project_id** (string | null, optional, default: None) — Project external_id (UUID). Prefer this over `project` when known — it routes to the exact project regardless of name collisions across cloud workspaces. Takes precedence over `project`. Get from list_memory_projects().
- **search_all_projects** (boolean, optional, default: False) — Optional opt-in to search every accessible project. Ignored when `project` or `project_id` is supplied.
- **page** (integer, optional, default: 1) — The page number of results to return (default 1). Aliases: page_number.
- **page_size** (integer, optional, default: 10) — The number of results to return per page (default 10). Aliases: limit, per_page.
- **search_type** (string | null, optional, default: None) — Type of search to perform, one of: "text", "title", "permalink", "vector", "semantic", "hybrid". Default is dynamic: "hybrid" when semantic search is enabled, otherwise "text".
- **output_format** (string, optional, default: "text") — "text" preserves existing structured search response behavior. "json" returns a machine-readable dictionary payload.
- **note_types** (array | null, optional, default: None) — Optional list of note types to search (e.g., ["note", "person"])
- **entity_types** (array | null, optional, default: None) — Optional list of entity types to filter by (e.g., ["entity", "observation"])
- **categories** (array | null, optional, default: None) — Optional list of observation categories for exact matching (e.g., ["requirement"]). Pair with entity_types=["observation"] to return only observations whose category matches exactly.
- **after_date** (string | null, optional, default: None) — Optional date filter for recent content (e.g., "1 week", "2d", "2024-01-01")
- **metadata_filters** (object | null, optional, default: None) — Optional structured frontmatter filters (e.g., {"status": "in-progress"}). Integer values match integer YAML fields ({"section": 3} works). A None value is an is-null match: notes where the key is absent or explicitly null. None inside $in/$between/a contains list/a comparison is refused — those compare against the value, and a comparison with null is never true.
- **tags** (array | null, optional, default: None) — Optional tag filter (frontmatter tags); shorthand for metadata_filters["tags"]. Accepts a list (["a", "b"]) or a comma-separated string ("a,b"), matching the write_note tags convention and the tag: query shorthand.
- **status** (string | null, optional, default: None) — Optional status filter (frontmatter status); shorthand for metadata_filters["status"]
- **min_similarity** (number | null, optional, default: None) — Optional float to override the global semantic_min_similarity threshold for this query. E.g., 0.0 to see all vector results, or 0.8 for high precision. Only applies to vector and hybrid search types.
- **valid_at** (string | null, optional, default: None) — Optional date ("2026-07-28") or RFC 3339 instant ("2026-07-28T09:00:00Z"; a timestamp with no offset is read as UTC). Returns sources whose authored valid range contains it. Sources with no temporal qualifier are excluded. Aliases: as_of, valid_on.
- **valid_overlaps** (string | null, optional, default: None) — Optional PostgreSQL-style range literal ("[2026-06-10,2026-07-27)", "(,2026-07-27]", "[2026-06-10,)"). Returns sources whose authored valid range overlaps it. Mutually exclusive with valid_at; also excludes undated sources. Aliases: overlaps, valid_during.
- **time_kind** (string | null, optional, default: None) — Optional kind of valid time to narrow to: "effective", "valid", "occurred", "due", or "mentioned". Valid on its own. Alias: kind.

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
- [gotcha] An authored token that does not read as a date is left as ordinary observation content with no warning; only a qualifier the author plainly meant is reported — an unknown kind (`@asserted:2026-06-10`), an unterminated quote, or a date the one-token rule truncated #valid-time
- [gotcha] An unquoted authored point is one whitespace-delimited token: `@occurred:2026-06-10` and `@occurred:03/04/2026` work, but a multi-word date like `@occurred:June 10, 2026` is left as content because nothing can tell where it ends #valid-time
- [gotcha] Double quotes lift the one-token rule and end the point at the closing quote, so `@occurred:"June 10, 2026"` and `@"June 2026"` both file — inside quotes even a month-only or year-only date is taken, since the author delimited it #valid-time
- [gotcha] A relative date is never filed as a qualifier, quoted or not: `@occurred:yesterday`, `@occurred:"2 days ago"` and a bare month name like `@occurred:March` each name a different span depending on when the note is indexed, so they stay ordinary content — quoting settles where a token ends, not what a date means #valid-time
- [gotcha] Relative wording still works for `timeframe` in recent-activity and build-context, which ask about edit time rather than authored time — only the valid-time qualifier requires a date that means the same thing on every pass #valid-time
- [gotcha] Only `"` opens a quoted point, never `'`, and an unterminated quote is reported rather than swallowing the rest of the line #valid-time
- [gotcha] `@occurred:03/04/2026` resolves by the `date_order` setting (YMD/DMY read it as 3 April, MDY as 4 March); ISO dates are never re-guessed #valid-time

## SEE ALSO

- see_also [[read-note(3)]]
- see_also [[build-context(3)]]
- see_also [[recent-activity(3)]]
- see_also [[bm-note(5)]]
