---
title: Design Review 2024-03-01
type: Person
schema:
  topic: string, review subject
  outcome?: string, decision made
  reviewer?(array): string, who reviewed
tags: [edge-case, inline-schema]
---

# Design Review 2024-03-01

Has BOTH an inline schema dict AND type: Person. The inline schema should win.

## Observations
- [topic] API versioning strategy
- [outcome] Adopt URL-based versioning
- [reviewer] Alice
- [reviewer] Bob
- [reviewer] Carol
