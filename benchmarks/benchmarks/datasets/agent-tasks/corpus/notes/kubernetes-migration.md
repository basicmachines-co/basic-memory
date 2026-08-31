---
title: Kubernetes Migration
type: note
tags: [infra, kubernetes]
status: active
priority: high
confidence: 0.7
---

# Kubernetes Migration

Move the API workloads from the legacy VMs onto the managed cluster.

## Observations

- [plan] Stateless services move first; the index workers follow
- [risk] Node-local disk assumptions in the sync watcher

## Relations

- relates_to [[2026-08-27 Infra Notes]]
