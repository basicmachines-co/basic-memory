---
title: bm-search(3)
type: manpage
section: 3
name: bm-search
summary: search a project's notes, ranked by relevance
generated: registry
tool: bm_search
verified: 0.24.1 mcp
---

# bm-search(3)

## NAME

**bm-search** — search a project's notes, ranked by relevance

## SYNOPSIS

```
bm_search(query=None, mode=None, filters=None, note_types=None, items=None,
          categories=None, since=None, lines=None, max_matches=10,
          identifier=None, path=None, compact=False, valid_at=None,
          valid_overlaps=None, time_kind=None, min_similarity=None,
          projects=None, all_projects=False, page=1, page_size=10,
          output_format="text", project=None)
```

## DESCRIPTION

Searches notes, observations and relations. `query` supports quoted phrases,
AND/OR/NOT and `tag:name`. `mode` picks `hybrid` (the default when semantic
search is available), `text`, `semantic`, `title` or `permalink` (globs with
`*`). A `memory://` URL as the query searches that permalink.

Narrow with `filters` (frontmatter), `note_types`, `items` (notes,
observations, relations), `since` and `path` (a folder). `lines` returns each
hit's matching lines with that many lines of context, like grep; `identifier`
searches one note's lines. `projects` or `all_projects` search several projects
at once, and each hit's path names its project.

Give one scope: `identifier`, `path`, a `memory://` query, `projects` and
`all_projects` each say where to search, and more than one is refused. Results
are paged; read full notes with bm_read.

The shell counterpart is grep(1).

## PARAMETERS

- **query** (string | null, optional, default: None) — What to search for. Omit to search by filters alone.
- **mode** (string | null, optional, default: None) — Search mode.
- **filters** (object | null, optional, default: None) — Frontmatter filters as {field: condition}. Nested fields use dot notation ("schema.confidence"). A condition is a value for equality ({"status": "draft"}), null for absent-or-null ({"owner": null}), a list for contains-all ({"tags": ["a", "b"]}), or an operator object with $gt, $gte, $lt, $lte, $in, $between, $contains or $exists, e.g. {"priority": {"$in": ["high"]}}. Several operators on one field AND together.
- **note_types** (array | null, optional, default: None) — Frontmatter `type` values, e.g. ['person', 'meeting'].
- **items** (array | null, optional, default: None) — What to return: notes, observations, relations, or a mix. Default: notes.
- **categories** (array | null, optional, default: None) — Observation categories to match exactly, e.g. ['decision'].
- **since** (string | null, optional, default: None) — How far back to look: '7d', '24h', '2 days ago', 'last week', or a date. Windows shorter than one day are widened to one day.
- **lines** (integer | null, optional, default: None) — Return each hit's matching lines with this many lines of context (0-10), instead of a match excerpt.
- **max_matches** (integer, optional, default: 10) — Matching lines shown per note with `lines`.
- **identifier** (string | null, optional, default: None) — Search only this note; returns its matching lines.
- **path** (string | null, optional, default: None) — Search only this folder and its subfolders.
- **compact** (boolean, optional, default: False) — Omit note bodies and excerpts; keep identifiers and scores.
- **valid_at** (string | null, optional, default: None) — Specialist: only facts whose authored valid time contains this date.
- **valid_overlaps** (string | null, optional, default: None) — Specialist: only facts whose valid range overlaps '[start,end)'.
- **time_kind** (string | null, optional, default: None) — Specialist: effective, valid, occurred, due or mentioned.
- **min_similarity** (number | null, optional, default: None) — Override the semantic similarity floor (0-1).
- **projects** (array | null, optional, default: None) — Search these projects together.
- **all_projects** (boolean, optional, default: False) — Search every project you can access.
- **page** (integer, optional, default: 1) — Page number.
- **page_size** (integer, optional, default: 10) — Results per page (1-100).
- **output_format** (optional, default: "text")
- **project** (string | null, optional, default: None) — Project name, 'workspace/project', or project external id. With multiple addressable projects, scope note and search calls explicitly with this parameter or a project-qualified target.

## EXAMPLES

```
bm_search(query="freeze", lines=0, mode="text", project="demo")
# → Results for 'freeze' in project 'demo':
#   - plans/launch-plan.md | launch-plan
#       9: - [decision] freeze on Friday

bm_search(query="launch", note_types=["plan"], compact=True, project="demo")
# → Results for 'launch' in project 'demo':
#   - plans/launch-plan.md | launch-plan | score 1.212

bm_search(query="release", mode="text", page_size=1, output_format="json", project="demo")
# → {"results": [{"title": "release-checklist", "file_path": "plans/release-checklist.md",
#    ...}], "current_page": 1, "page_size": 1, "total": 2, "has_more": true}
```

## GOTCHAS

- `max_matches` bounds matching lines; without `lines` or `identifier` it is
  refused.
- A search across projects reaches at most result 1000 (`page * page_size`).
- `hybrid` mode also returns notes that are only near in meaning. With `lines`,
  candidates without a matching line are omitted in every mode. Searching one
  note with `identifier` explicitly says when no lines match.
- Line matching filters one ranked page at a time. An empty page can still have
  a next-page call; follow it to check the remaining candidates.
- Line results report `total_is_exact: false`; `total` counts ranked candidates,
  so page with `has_more` instead of using it as a result count.

## SEE ALSO

- see_also [[grep(1)]]
- see_also [[bm-find(3)]]
- see_also [[bm-read(3)]]
- see_also [[bm-context(3)]]
