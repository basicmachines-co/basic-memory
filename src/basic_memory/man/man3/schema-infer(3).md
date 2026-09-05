---
title: schema-infer(3)
type: manpage
section: 3
name: schema-infer
summary: derive a Picoschema suggestion from existing notes
generated: registry
tool: schema_infer
verified: 0.21.6 mcp
---

# schema-infer(3)

## NAME

**schema-infer** — derive a Picoschema suggestion from existing notes

## SYNOPSIS

```
schema_infer(note_type, threshold=0.25, project=None, project_id=None,
             output_format="text")
```

## PARAMETERS

- **note_type** (string, required) — The note type to analyze (e.g., "person", "meeting").
- **threshold** (number, optional, default: 0.25) — Minimum frequency (0-1) for a field to be suggested as optional. Default 0.25 (25%). Fields above 95% become required.
- **project** (string | null, optional, default: None) — Project name. Optional -- server will resolve.
- **project_id** (string | null, optional, default: None) — Project external_id (UUID). Prefer this over `project` when known — it routes to the exact project regardless of name collisions across cloud workspaces. Takes precedence over `project`. Get from list_memory_projects().
- **output_format** (string, optional, default: "text")

## DESCRIPTION

Analyzes every note of a type and proposes a schema from observed usage:
observation categories and relation types with their frequencies. Fields
above 95% frequency are suggested as required; fields above `threshold`
(default 25%) as optional; the rest are listed as excluded. Relation targets
are typed by what they actually point at.

The workflow this enables: write notes freely first, infer a schema once
patterns stabilize, then [[schema-validate(3)]] keeps new notes consistent.

## MCP USAGE

Verified — inferring this manual's schema back from its own pages:

```
schema_infer(note_type="manpage", project="manual")
# → analyzed 14 notes; suggested:
#     gotcha?(array): string        (86%)
#     pattern?: string              (36%)
#     bug?: string                  (36%)
#     see_also(array): Manpage      (100% → required, typed!)
#     links_to?: Manpage            (86%)
```

## GOTCHAS

- [gotcha] Inference sees only observations and relations — frontmatter fields (the settings.frontmatter half of a schema) are not inferred #scope
- [gotcha] A relation present in 100% of notes is promoted to required, which will warn on every future note that lacks it — review before adopting verbatim #thresholds

## SEE ALSO

- see_also [[schema-validate(3)]]
- see_also [[schema-diff(3)]]
- see_also [[bm-schema(5)]]
