---
title: schema-validate(3)
type: manpage
section: 3
name: schema-validate
summary: validate notes against their Picoschema definitions
generated: hand
tool: schema_validate
verified: 0.21.6 mcp+cli
---

# schema-validate(3)

## RETIRED MCP TOOL

`schema_validate` was removed from the MCP surface in 1.0. It is not registered
and has no compatibility alias. Use `bm_schema(command="validate")` instead;
[[bm-schema(3)]] documents its current parameters and examples.

## CLI

The shell CLI retains its own interface:

```
bm tool validate [TARGET] [--project NAME]
# TARGET: a note type ("manpage"), a note path, or omitted for everything
```

## SEE ALSO

- see_also [[bm-schema(3)]]
