# SPEC-LOCAL-PLUS-PUBLISH: Local+ Published Notes and Privacy Tiers

**Status:** Draft  
**Date:** 2026-02-14  
**Owner:** Basic Memory

## Summary

Add a paid Local+ feature that lets users publish selected notes to shareable URLs while keeping the
main knowledge base local-first. Use this as a product wedge for users who do not want full cloud
hosting but do want collaboration and distribution features.

This spec also captures a practical position on "zero knowledge" for Local+.

## Context

Basic Memory already has strong local-first primitives and optional cloud routing/sync. A recurring
request is:

- keep knowledge local by default,
- pay for selective value-add,
- share specific outputs externally.

Published Notes fits this model: explicit per-note opt-in, reversible, and easy to understand.

## Goals

1. Provide an Obsidian Publish-style sharing experience for selected notes.
2. Keep local markdown files as source of truth.
3. Make sharing compatible with current cloud/auth/billing primitives.
4. Define clear Local+ packaging that does not degrade OSS local workflows.
5. Document zero-knowledge constraints so product decisions are explicit.

## Non-Goals

1. Full hosted editing for all notes (Cloud Full remains separate).
2. Public website builder/CMS features.
3. Strict cryptographic zero-knowledge server processing for MCP/search in v1.

## Local+ Feature Catalog (Sellable)

Core Local+ candidates:

1. Published Notes (share URL, revoke, expiry, password).
2. Snapshot Time Machine (point-in-time restore for local projects).
3. Recovery Drill Reports (automated restore verification).
4. Device/API Key Governance (per-device keys, revocation, audit trail).
5. BYO Storage Orchestration (managed setup for user-owned object storage).
6. Semantic Boost Add-on (higher quality retrieval options while files remain source-of-truth).

Team-oriented add-ons:

1. Team-owned shared links and domain branding.
2. Role-based publish permissions.
3. Shared workspace policies for what can be published.

## Proposed MVP: Published Notes

### User Experience

Per note actions:

1. Publish.
2. Unpublish.
3. Copy URL.
4. Regenerate URL.
5. Set visibility and controls.

Controls:

1. Visibility: `unlisted` (default) or `public`.
2. Optional password gate.
3. Optional expiration datetime.
4. Optional "disable indexing" flag for public mode.

Behavior:

1. Source note remains local markdown.
2. Publish is explicit opt-in per note.
3. Unpublish removes public access immediately.
4. Republish creates a new URL token unless user chooses to keep current URL.

### URL Model

1. Unlisted share URL: high-entropy token path.
2. Public URL: slug path (optional, later phase).
3. Team plans can support custom domain mapping in later phase.

### Content Model

v1 published page includes:

1. Rendered markdown body.
2. Optional metadata (title, updated_at).

v1 excludes:

1. Full graph traversal expansion.
2. Related note auto-discovery on public pages.

### Sync Model

1. Local file remains canonical.
2. Publish stores a rendered snapshot plus metadata in cloud.
3. Update path:
   - manual "update published version", or
   - optional auto-update on note change (plan-gated).

## Architecture (v1)

### High-Level Flow

1. Client selects a note to publish.
2. Client sends publish request with note identifier and policy.
3. Service resolves note content (local sync artifact or explicit upload payload).
4. Service stores published artifact and returns share URL.

### Data Model

`published_notes`

1. `id` (uuid)
2. `tenant_id` or `workspace_id`
3. `project_id`
4. `entity_permalink` (or stable external_id)
5. `share_token` (hashed in DB)
6. `visibility` (`unlisted`|`public`)
7. `password_hash` (nullable)
8. `expires_at` (nullable)
9. `is_active`
10. `published_content` (rendered snapshot or reference)
11. `published_at`
12. `updated_at`

### API Shape (Draft)

1. `POST /api/published-notes`
2. `GET /api/published-notes`
3. `GET /api/published-notes/{id}`
4. `PATCH /api/published-notes/{id}`
5. `DELETE /api/published-notes/{id}` (unpublish)
6. `POST /api/published-notes/{id}/regenerate-url`
7. `GET /p/{token}` (public resolver)

### CLI Shape (Draft)

1. `bm cloud publish <identifier>`
2. `bm cloud publish list`
3. `bm cloud publish update <id>`
4. `bm cloud publish unpublish <id>`
5. `bm cloud publish rotate-url <id>`

### Security

1. Default to unlisted URLs.
2. Store only hashed share tokens.
3. Passwords hashed server-side.
4. Enforce expiration at request time.
5. Log publish/unpublish/rotate events for auditability.

## Packaging and Pricing Direction

Suggested split:

1. OSS Local: no publish URLs.
2. Local+ Solo: publish URLs + snapshots + recovery.
3. Local+ Team: solo features + team governance and branding.
4. Cloud Full: hosted app + full cloud workflows.

Key message:
"Keep everything local. Publish only what you choose."

## Rollout Plan

1. Phase 1: Unlisted publish URLs + unpublish + regenerate URL.
2. Phase 2: Password/expiry controls.
3. Phase 3: Auto-update on note change and basic analytics.
4. Phase 4: Team branding/domains/policies.

## Zero-Knowledge Position

### Strict Zero-Knowledge Definition

Strict zero-knowledge means the server cannot decrypt note content at all.

### Why This Conflicts with MCP and Search

If server cannot decrypt:

1. MCP tool execution against cloud content cannot read/write semantic content.
2. Full-text search cannot index plaintext content.
3. Semantic/vector search cannot generate or query embeddings on plaintext.
4. Server-side relation resolution and context building become severely limited.

This matches earlier findings: strict zero-knowledge materially handicaps MCP-driven behavior and
search quality.

### Viable Alternatives (Not Strict Zero-Knowledge)

1. Encryption at rest/in transit with server-side decrypt in trusted runtime.
   - Preserves MCP/search quality.
   - Not zero-knowledge cryptographically.

2. Client-side retrieval mode.
   - Keep MCP/search local; cloud is sync/share/backup relay.
   - Best for privacy-first users.
   - Requires local agent availability for advanced retrieval.

3. Limited encrypted indexing.
   - Blind indexes for exact keywords only.
   - No high-quality semantic search.
   - Usually poor UX for natural-language memory recall.

### Recommendation

For Local+:

1. Do not promise strict zero-knowledge for cloud MCP/search paths.
2. Offer a privacy-first local mode where advanced retrieval stays local.
3. Clearly label tradeoffs:
   - "Local private mode" (best privacy, best local retrieval).
   - "Cloud-assisted mode" (best cross-device/MCP consistency, trusted-runtime decrypt).

This keeps messaging honest and avoids repeating the known incompatibility.

