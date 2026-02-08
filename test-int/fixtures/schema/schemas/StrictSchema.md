---
title: StrictSchema
type: schema
entity: StrictEntity
version: 1
schema:
  name: string, entity name
  category: string, required classification
  priority?: integer, priority level
settings:
  validation: strict
---

# StrictSchema

A schema with strict validation mode enabled. Notes validated against this schema
will produce errors instead of warnings for missing required fields. Useful for
CI/CD enforcement where structural compliance is mandatory.

## Observations
- [purpose] Demonstrates strict validation mode for required field enforcement
- [mode] Strict validation blocks sync on missing required fields
