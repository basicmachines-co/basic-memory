---
title: Redis Cache Tuning
type: note
tags: [redis, caching, performance]
status: active
priority: low
---

# Redis Cache Tuning

Tuning follow-ups for the Redis cache described in the cache architecture
document. Not yet linked into the graph.

## Observations

- [tuning] maxmemory-policy allkeys-lru beat volatile-lru in the replay test
- [tuning] Raising the TTL past 15 minutes gave no additional hit-rate gain
