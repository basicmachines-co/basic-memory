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

## Archived validation

Archived tracking token: RC-ARCHIVED-2c6b. This is not the current policy.

Archived evidence record 001 describes an earlier rehearsal of the release process.
The historical material is retained for reference and specifies no active operational policy.

Archived evidence record 002 describes an earlier rehearsal of the release process.
The historical material is retained for reference and specifies no active operational policy.

Archived evidence record 003 describes an earlier rehearsal of the release process.
The historical material is retained for reference and specifies no active operational policy.

Archived evidence record 004 describes an earlier rehearsal of the release process.
The historical material is retained for reference and specifies no active operational policy.

Archived evidence record 005 describes an earlier rehearsal of the release process.
The historical material is retained for reference and specifies no active operational policy.

Archived evidence record 006 describes an earlier rehearsal of the release process.
The historical material is retained for reference and specifies no active operational policy.

Archived evidence record 007 describes an earlier rehearsal of the release process.
The historical material is retained for reference and specifies no active operational policy.

Archived evidence record 008 describes an earlier rehearsal of the release process.
The historical material is retained for reference and specifies no active operational policy.

Archived evidence record 009 describes an earlier rehearsal of the release process.
The historical material is retained for reference and specifies no active operational policy.

Archived evidence record 010 describes an earlier rehearsal of the release process.
The historical material is retained for reference and specifies no active operational policy.

Archived evidence record 011 describes an earlier rehearsal of the release process.
The historical material is retained for reference and specifies no active operational policy.

Archived evidence record 012 describes an earlier rehearsal of the release process.
The historical material is retained for reference and specifies no active operational policy.

Archived evidence record 013 describes an earlier rehearsal of the release process.
The historical material is retained for reference and specifies no active operational policy.

Archived evidence record 014 describes an earlier rehearsal of the release process.
The historical material is retained for reference and specifies no active operational policy.

Archived evidence record 015 describes an earlier rehearsal of the release process.
The historical material is retained for reference and specifies no active operational policy.

Archived evidence record 016 describes an earlier rehearsal of the release process.
The historical material is retained for reference and specifies no active operational policy.

Archived evidence record 017 describes an earlier rehearsal of the release process.
The historical material is retained for reference and specifies no active operational policy.

Archived evidence record 018 describes an earlier rehearsal of the release process.
The historical material is retained for reference and specifies no active operational policy.

Archived evidence record 019 describes an earlier rehearsal of the release process.
The historical material is retained for reference and specifies no active operational policy.

Archived evidence record 020 describes an earlier rehearsal of the release process.
The historical material is retained for reference and specifies no active operational policy.

Archived evidence record 021 describes an earlier rehearsal of the release process.
The historical material is retained for reference and specifies no active operational policy.

Archived evidence record 022 describes an earlier rehearsal of the release process.
The historical material is retained for reference and specifies no active operational policy.

Archived evidence record 023 describes an earlier rehearsal of the release process.
The historical material is retained for reference and specifies no active operational policy.

Archived evidence record 024 describes an earlier rehearsal of the release process.
The historical material is retained for reference and specifies no active operational policy.

Archived evidence record 025 describes an earlier rehearsal of the release process.
The historical material is retained for reference and specifies no active operational policy.

Archived evidence record 026 describes an earlier rehearsal of the release process.
The historical material is retained for reference and specifies no active operational policy.

Archived evidence record 027 describes an earlier rehearsal of the release process.
The historical material is retained for reference and specifies no active operational policy.

Archived evidence record 028 describes an earlier rehearsal of the release process.
The historical material is retained for reference and specifies no active operational policy.

Archived evidence record 029 describes an earlier rehearsal of the release process.
The historical material is retained for reference and specifies no active operational policy.

Archived evidence record 030 describes an earlier rehearsal of the release process.
The historical material is retained for reference and specifies no active operational policy.

Archived evidence record 031 describes an earlier rehearsal of the release process.
The historical material is retained for reference and specifies no active operational policy.

Archived evidence record 032 describes an earlier rehearsal of the release process.
The historical material is retained for reference and specifies no active operational policy.

Archived evidence record 033 describes an earlier rehearsal of the release process.
The historical material is retained for reference and specifies no active operational policy.

Archived evidence record 034 describes an earlier rehearsal of the release process.
The historical material is retained for reference and specifies no active operational policy.

Archived evidence record 035 describes an earlier rehearsal of the release process.
The historical material is retained for reference and specifies no active operational policy.

Archived evidence record 036 describes an earlier rehearsal of the release process.
The historical material is retained for reference and specifies no active operational policy.

Archived evidence record 037 describes an earlier rehearsal of the release process.
The historical material is retained for reference and specifies no active operational policy.

Archived evidence record 038 describes an earlier rehearsal of the release process.
The historical material is retained for reference and specifies no active operational policy.

Archived evidence record 039 describes an earlier rehearsal of the release process.
The historical material is retained for reference and specifies no active operational policy.

Archived evidence record 040 describes an earlier rehearsal of the release process.
The historical material is retained for reference and specifies no active operational policy.

Archived evidence record 041 describes an earlier rehearsal of the release process.
The historical material is retained for reference and specifies no active operational policy.

Archived evidence record 042 describes an earlier rehearsal of the release process.
The historical material is retained for reference and specifies no active operational policy.

Archived evidence record 043 describes an earlier rehearsal of the release process.
The historical material is retained for reference and specifies no active operational policy.

Archived evidence record 044 describes an earlier rehearsal of the release process.
The historical material is retained for reference and specifies no active operational policy.

Archived evidence record 045 describes an earlier rehearsal of the release process.
The historical material is retained for reference and specifies no active operational policy.

Archived evidence record 046 describes an earlier rehearsal of the release process.
The historical material is retained for reference and specifies no active operational policy.

Archived evidence record 047 describes an earlier rehearsal of the release process.
The historical material is retained for reference and specifies no active operational policy.

Archived evidence record 048 describes an earlier rehearsal of the release process.
The historical material is retained for reference and specifies no active operational policy.

Archived evidence record 049 describes an earlier rehearsal of the release process.
The historical material is retained for reference and specifies no active operational policy.

Archived evidence record 050 describes an earlier rehearsal of the release process.
The historical material is retained for reference and specifies no active operational policy.

Archived evidence record 051 describes an earlier rehearsal of the release process.
The historical material is retained for reference and specifies no active operational policy.

Archived evidence record 052 describes an earlier rehearsal of the release process.
The historical material is retained for reference and specifies no active operational policy.

Archived evidence record 053 describes an earlier rehearsal of the release process.
The historical material is retained for reference and specifies no active operational policy.

Archived evidence record 054 describes an earlier rehearsal of the release process.
The historical material is retained for reference and specifies no active operational policy.

Archived evidence record 055 describes an earlier rehearsal of the release process.
The historical material is retained for reference and specifies no active operational policy.

Archived evidence record 056 describes an earlier rehearsal of the release process.
The historical material is retained for reference and specifies no active operational policy.

Archived evidence record 057 describes an earlier rehearsal of the release process.
The historical material is retained for reference and specifies no active operational policy.

Archived evidence record 058 describes an earlier rehearsal of the release process.
The historical material is retained for reference and specifies no active operational policy.

Archived evidence record 059 describes an earlier rehearsal of the release process.
The historical material is retained for reference and specifies no active operational policy.

Archived evidence record 060 describes an earlier rehearsal of the release process.
The historical material is retained for reference and specifies no active operational policy.

Archived evidence record 061 describes an earlier rehearsal of the release process.
The historical material is retained for reference and specifies no active operational policy.

Archived evidence record 062 describes an earlier rehearsal of the release process.
The historical material is retained for reference and specifies no active operational policy.

Archived evidence record 063 describes an earlier rehearsal of the release process.
The historical material is retained for reference and specifies no active operational policy.

Archived evidence record 064 describes an earlier rehearsal of the release process.
The historical material is retained for reference and specifies no active operational policy.

Archived evidence record 065 describes an earlier rehearsal of the release process.
The historical material is retained for reference and specifies no active operational policy.

## Rollback checklist

- [verification] Rollback tracking token: RC-ROLLBACK-CURRENT-7c2a

## Forward planning

Future tracking token: RC-FUTURE-8a1d. This has not been adopted.
