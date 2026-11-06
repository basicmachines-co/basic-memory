---
title: bm-schema-diff(3)
type: manpage
section: 3
name: bm-schema-diff
summary: show drift between a note type's notes and its schema
generated: registry
tool: bm_schema_diff
verified: 1.0.0 mcp
---

# bm-schema-diff(3)

## NAME

**bm-schema-diff** — show drift between a note type's notes and its schema

## SYNOPSIS

```
bm_schema_diff(note_type, project, output_format="text")
```

## DESCRIPTION

Compares the notes of `note_type` with its schema and reports drift: fields
the notes added, fields they dropped, and cardinality changes. Frontmatter keys count as fields, except `title`, `type`, `permalink`, `tags`
and `schema`.

A type with no schema, or no notes, is a `NOT_FOUND` error that says how to
create one; no notes is not reported as "no drift".

## PARAMETERS

- **note_type** (string, required) — The frontmatter `type:` to check.
- **project** (string, required) — Required project name, 'workspace/project', or project external id. Discover projects with bm_project_list(). Paths are relative to this project; a qualified path must agree with it.
- **output_format** (optional, default: "text")

## EXAMPLES

```
bm_schema_diff(note_type="plan", project="demo")
# → # Schema Drift: plan
#   ## New Fields (in notes but not in schema)
#   - **decision** (observation) — 100% (1/1)
```

## SEE ALSO

- see_also [[bm-schema-validate(3)]]
- see_also [[bm-schema-infer(3)]]
