---
title: Person
type: schema
entity: Person
version: 1
schema:
  name: string, full name
  role?: string, job title or position
  works_at?: Organization, employer
  expertise?(array): string, areas of knowledge
  email?: string, contact email
settings:
  validation: warn
---

# Person

A human individual in the knowledge graph. Represents people such as engineers,
scientists, authors, founders, or any notable individual tracked in the knowledge base.

## Observations
- [purpose] Defines the canonical structure for person notes
- [convention] The name field is always required for identification

## Relations
- used_by [[Paul Graham]]
- used_by [[Rich Hickey]]
