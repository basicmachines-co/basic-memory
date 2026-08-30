---
title: search-notes(3)
type: manpage
section: 3
name: search-notes
summary: search the knowledge base by text, similarity, or metadata
generated: hand
tool: search_notes
verified: 0.21.6 mcp+cli
---

# search-notes(3)

## NAME

**search-notes** — search the knowledge base by text, similarity, or metadata

## SYNOPSIS

MCP:

```
search_notes(query=None,
             project=None, project_id=None, search_type=None,
             note_types=None, entity_types=None, categories=None,
             metadata_filters=None, tags=None, status=None, after_date=None,
             min_similarity=None, search_all_projects=False,
             page=1, page_size=10, output_format="text")
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

## PARAMETERS

- **query** — search string; optional. Omit it for filter-only searches
- **search_type** — see modes above; default is dynamic (`hybrid` if semantic
  search is enabled, else `text`)
- **metadata_filters** — dict of frontmatter field → value; integer values
  match integer YAML fields (`{"section": 3}` works)
- **tags** — list or comma string, same convention as [[write-note(3)]]
- **min_similarity** — float override for vector/hybrid threshold; `0.0`
  shows everything, `0.8` is high precision
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

## SEE ALSO

- see_also [[read-note(3)]]
- see_also [[build-context(3)]]
- see_also [[recent-activity(3)]]
- see_also [[bm-note(5)]]
