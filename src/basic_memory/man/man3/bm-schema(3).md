---
title: bm-schema(3)
type: manpage
section: 3
name: bm-schema
summary: validate notes against schemas, infer schemas, find drift
generated: registry
tool: bm_schema
verified: 0.24.1 mcp
---

# bm-schema(3)

## NAME

**bm-schema** — validate notes against schemas, infer schemas, find drift

## SYNOPSIS

```
bm_schema(command, project, note_type=None, identifier=None, threshold=0.25,
          output_format="text")
```

## DESCRIPTION

Works with Picoschema note schemas. A schema is a note with `type: schema`
whose `entity` field names the note type it describes. `command` picks what to
do:

- `validate` — check notes against their schema: one `note_type`, one note by
  `identifier`, or every type that has a schema.
- `infer` — suggest a schema from the notes of a `note_type`; `threshold` is
  the share of notes a field must appear in.
- `diff` — compare a `note_type`'s notes with its schema to find drift.

A type with no schema, or no notes, is a `NOT_FOUND` error that says how to
create one.

## PARAMETERS

- **command** (required) — What to do.
- **project** (string, required) — Required project name, 'workspace/project', or project external id. Discover projects with bm_project(command='list'). Paths are relative to this project; a qualified path must agree with it.
- **note_type** (string | null, optional, default: None) — The frontmatter `type:` to check. Required for infer and diff.
- **identifier** (string | null, optional, default: None) — For validate: one note, instead of a whole note type.
- **threshold** (number, optional, default: 0.25) — For infer: the share of notes a field must appear in to be suggested.
- **output_format** (optional, default: "text")

## EXAMPLES

```
bm_schema(command="infer", note_type="plan", project="demo")
# → # Schema Inference: plan
#   Notes analyzed: 1
#   ...
#   - **goal** (observation) — 100% (1/1) [required]

bm_schema(command="validate", note_type="plan", project="demo")
# → # Schema Validation: plan
#   Notes: 1 | Valid: 1 | Warnings: 1 | Errors: 0
#   - **launch-plan** — valid
#     - warning: Missing required field: owner (expected [owner] observation)

bm_schema(command="diff", note_type="plan", project="demo")
# → # Schema Drift: plan
#   ## New Fields (in notes but not in schema)
#   - **decision** (observation) — 100% (1/1)
```

## SEE ALSO

- see_also [[bm-find(3)]]
- see_also [[bm-write(3)]]
