---
title: schema-diff(3)
type: manpage
section: 3
name: schema-diff
summary: detect drift between a schema and actual note usage
generated: registry
tool: schema_diff
verified: 0.21.6 mcp
---

# schema-diff(3)

## NAME

**schema-diff** — detect drift between a schema and actual note usage

## SYNOPSIS

```
schema_diff(note_type, project=None, project_id=None, output_format="text")
```

## DESCRIPTION

Compares the declared schema for a type against how notes of that type are
actually written, reporting three kinds of drift:

- **New fields** — used in notes but absent from the schema
- **Dropped fields** — declared but rarely or never used
- **Cardinality changes** — declared array but used single-value, or vice versa

Run it periodically: schemas describe intent, notes describe reality, and
the gap between them is editorial work waiting to be done.

## MCP USAGE

Verified — this manual's own drift report:

```
schema_diff(note_type="manpage", project="manual")
# → New: links_to (relation, 86%) — inline wikilinks, unschematized
#   Dropped: example (observation, 0%) — declared but unused so far
#   Cardinality: pattern, bug declared array but typically single-value
```

That report did real editorial work: it caught that this manual's pages
put examples in EXAMPLES sections rather than [example] observations.

## GOTCHAS

- [gotcha] Like schema-infer, the diff covers observations and relations only — frontmatter drift is not detected #scope
- [pattern] Treat "dropped fields" as a prompt, not an order: a declared-but-unused field may be aspirational rather than dead #workflow

## SEE ALSO

- see_also [[schema-validate(3)]]
- see_also [[schema-infer(3)]]
- see_also [[bm-schema(5)]]
