---
title: bm-schema-validate(3)
type: manpage
section: 3
name: bm-schema-validate
summary: check notes against their Picoschema schema
generated: registry
tool: bm_schema_validate
verified: 1.0.0 mcp
---

# bm-schema-validate(3)

## NAME

**bm-schema-validate** — check notes against their Picoschema schema

## SYNOPSIS

```
bm_schema_validate(project, note_type=None, identifier=None,
                   output_format="text")
```

## DESCRIPTION

Checks notes against their schema: one `note_type`, one note by `identifier`,
or, with neither, every note type that has a schema. Giving both is refused,
because a note's own type decides its schema. A schema is a note with `type: schema` whose `entity` field names the note
type it describes.

A schema field is satisfied by a frontmatter key or an observation category of
the same name. Frontmatter is checked first: `attendees: 4` in frontmatter
satisfies `attendees: integer`, and a `- [attendees]` observation is used only
when the key is absent. Enum checks apply to either. A field whose type is
another note type is satisfied by relations.

A type with no schema, or no notes, is a `NOT_FOUND` error that says how to
create one.

## PARAMETERS

- **project** (string, required) — Required project name, 'workspace/project', or project external id. Discover projects with bm_project_list(). Paths are relative to this project; a qualified path must agree with it.
- **note_type** (string | null, optional, default: None) — The frontmatter `type:` to check.
- **identifier** (string | null, optional, default: None) — One note, instead of a whole note type.
- **output_format** (optional, default: "text")

## EXAMPLES

```
bm_schema_validate(note_type="plan", project="demo")
# → # Schema Validation: plan
#   Notes: 1 | Valid: 1 | Warnings: 1 | Errors: 0
#   - **launch-plan** — valid
#     - warning: Missing required field: owner (expected owner: in frontmatter or a [owner] observation)
```

## SEE ALSO

- see_also [[bm-schema-infer(3)]]
- see_also [[bm-schema-diff(3)]]
- see_also [[bm-write(3)]]
