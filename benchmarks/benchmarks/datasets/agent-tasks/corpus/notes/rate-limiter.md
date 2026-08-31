---
title: Rate Limiter
type: note
tags: [api, limits]
---

# Rate Limiter

Sliding-window limiter in front of the public API.

## Observations

- [design] Sliding window keyed by API token, evaluated at the edge

## Relations

- depends_on [[Redis Quota Store]]
