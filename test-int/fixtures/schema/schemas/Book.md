---
title: Book
type: schema
entity: Book
version: 1
schema:
  title: string, book title
  author?: string, author name
  published_year?: integer, year of publication
  genre?(enum): [fiction, nonfiction, technical]
settings:
  validation: warn
---

# Book

A book or publication tracked in the knowledge base. The genre field uses
an enumeration to constrain values to fiction, nonfiction, or technical.

## Observations
- [purpose] Tracks books with structured metadata including genre classification
- [convention] published_year should be a four-digit year
