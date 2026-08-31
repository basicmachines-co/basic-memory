---
title: OAuth Token Design
type: note
tags: [oauth, security, architecture]
status: draft
priority: high
confidence: 0.85
review:
  status: pending
---

# OAuth Token Design

Token lifetimes and rotation for the OAuth flow. Still in review.

## Observations

- [design] Access tokens are 15-minute JWTs; refresh tokens rotate on use
- [security] Refresh token reuse triggers whole-family revocation

## Relations

- relates_to [[API Design Decisions]]
