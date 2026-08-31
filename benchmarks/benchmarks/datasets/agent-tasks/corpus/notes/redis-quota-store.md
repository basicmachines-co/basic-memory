---
title: Redis Quota Store
type: note
tags: [redis, quotas]
---

# Redis Quota Store

Counter storage backing the rate limiter.

## Observations

- [constraint] Quota counters must fit a single Redis hash slot BMEVAL-quota-fa11
- [design] A Lua script increments and expires each counter atomically

## Relations

- relates_to [[Redis Cache Architecture]]
