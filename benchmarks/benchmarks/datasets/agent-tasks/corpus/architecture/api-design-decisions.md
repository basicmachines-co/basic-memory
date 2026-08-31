---
title: API Design Decisions
type: note
tags: [api, architecture]
status: published
priority: high
confidence: 0.3
---

# API Design Decisions

Running record of decisions that shape the public API.

## Observations

- [decision] All list endpoints paginate with opaque cursors
- [decision] Cache invalidation is event-driven, not TTL-only

## Relations

- depends_on [[Redis Cache Architecture]]
