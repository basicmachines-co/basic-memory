---
title: bm-schema-infer(3)
type: manpage
section: 3
name: bm-schema-infer
summary: suggest a Picoschema schema from a note type's notes
generated: registry
tool: bm_schema_infer
verified: 1.0.0 mcp
---

# bm-schema-infer(3)

## NAME

**bm-schema-infer** — suggest a Picoschema schema from a note type's notes

## SYNOPSIS

```
bm_schema_infer(note_type, project, threshold=0.25, output_format="text")
```

## DESCRIPTION

Suggests a schema for `note_type` from the fields its notes share. A field is
suggested when at least `threshold` of the notes have it. Frontmatter keys count as fields, except `title`, `type`, `permalink`, `tags`
and `schema`.
A schema is a note with `type: schema` whose `entity` field names the note
type it describes. Save the suggestion with bm_write to start validating.

A type with no notes, or no field above the threshold, is a `NOT_FOUND` error
that says what to try.

## PARAMETERS

- **note_type** (string, required) — The frontmatter `type:` to check.
- **project** (string, required) — Required project name, 'workspace/project', or project external id. Discover projects with bm_project_list(). Paths are relative to this project; a qualified path must agree with it.
- **threshold** (number, optional, default: 0.25) — The share of notes a field must appear in to be suggested.
- **output_format** (optional, default: "text")

## EXAMPLES

```
bm_schema_infer(note_type="plan", project="demo")
# → # Schema Inference: plan
#   Notes analyzed: 1
#   ...
#   - **goal** (observation) — 100% (1/1) [required]
```

## SEE ALSO

- see_also [[bm-schema-validate(3)]]
- see_also [[bm-schema-diff(3)]]
- see_also [[bm-find(3)]]
