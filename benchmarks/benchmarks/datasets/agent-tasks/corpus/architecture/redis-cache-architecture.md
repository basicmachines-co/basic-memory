---
title: Redis Cache Architecture
type: note
tags: [redis, architecture, caching]
status: published
priority: high
confidence: 0.6
---

# Redis Cache Architecture

How the read-through Redis cache fronts the entity API.

## Observations

- [design] Read-through cache with a 15 minute TTL on entity payloads
- [constraint] Eviction must never outlive the session token lifetime
- [metric] Cache hit rate holds at 92 percent in staging

## Relations

- relates_to [[API Design Decisions]]
