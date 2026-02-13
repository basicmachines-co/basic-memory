---
title: Person
type: schema
entity: Person
version: 1
schema:
  name: string, full name
  role?: string, job title or position
  works_at?: Organization, employer
  email?: string, contact email
settings:
  validation: warn
---

# Person

A v1 Person schema used for drift detection testing. Usage has diverged
from this definition over time.
