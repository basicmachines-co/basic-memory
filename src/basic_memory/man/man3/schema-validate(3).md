---
title: schema-validate(3)
type: manpage
section: 3
name: schema-validate
summary: validate notes against their Picoschema definitions
generated: registry
tool: schema_validate
verified: 0.21.6 mcp+cli
---

# schema-validate(3)

## NAME

**schema-validate** — validate notes against their Picoschema definitions

## SYNOPSIS

MCP:

```
schema_validate(note_type=None, identifier=None, project=None,
                project_id=None, output_format="text")
```

CLI:

```
bm tool schema-validate [TARGET] [--project NAME]
# TARGET: a note type ("manpage"), a note path, or omitted for everything
```

## PARAMETERS

- **note_type** (string | null, optional, default: None) — Note type to batch-validate (e.g., "person", "meeting"). If provided, validates all notes of this type.
- **identifier** (string | null, optional, default: None) — Specific note to validate (permalink, title, or path). If provided, validates only this note.
- **project** (string | null, optional, default: None) — Project name. Optional -- server will resolve.
- **project_id** (string | null, optional, default: None) — Project external_id (UUID). Prefer this over `project` when known — it routes to the exact project regardless of name collisions across cloud workspaces. Takes precedence over `project`. Get from list_memory_projects().
- **output_format** (string, optional, default: "text")

## DESCRIPTION

Checks notes against the schema resolved for their type (see
[[bm-schema(5)]] for resolution rules) and reports per-field results:
required fields present or missing, enum values in range, observation
categories matched, relations typed correctly — plus `unmatched_observations`
and `unmatched_relations` for content the schema doesn't cover. Severity
follows the schema's `settings.validation` (`warn` by default; `strict` or
`off`).

This manual validates itself with this tool: every page is checked against
the [[Manpage]] schema before shipping.

## MCP USAGE

Verified against this manual:

```
schema_validate(note_type="manpage", project="manual",
                output_format="json")
# → {"total_notes": 18, "valid_count": 18,
#    "warning_count": 0, "error_count": 0,
#    "results": [per-note field-by-field reports]}
```

## CLI EQUIVALENT

```
bm tool schema-validate manpage --project manual
# → same JSON report; TARGET dispatches on type vs path automatically
```

## GOTCHAS

- [gotcha] Validation runs only when you call it — write_note does not validate on save, so a CI or pre-publish validate pass is on you #workflow
- [gotcha] The CLI takes one positional TARGET (type or path, auto-detected); the MCP tool splits the same idea into note_type and identifier parameters #cli-parity
- [gotcha] Inline links create links_to relations that show up in unmatched_relations unless your schema declares them #validation

## SEE ALSO

- see_also [[schema-infer(3)]]
- see_also [[schema-diff(3)]]
- see_also [[bm-schema(5)]]
