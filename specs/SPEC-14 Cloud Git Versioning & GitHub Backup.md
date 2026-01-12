---
title: 'SPEC-14: Cloud Git Versioning & GitHub Backup'
type: spec
permalink: specs/spec-14-cloud-git-versioning
tags:
- git
- github
- backup
- versioning
- cloud
related:
- specs/spec-9-multi-project-bisync
- specs/spec-9-follow-ups-conflict-sync-and-observability
status: deferred
---

# SPEC-14: Cloud Git Versioning & GitHub Backup

**Status: DEFERRED** - Postponed until multi-user/teams feature development. Using S3 versioning (SPEC-9.1) for v1 instead.

## Why Deferred

**Original goals can be met with simpler solutions:**
- Version history → **S3 bucket versioning** (automatic, zero config)
- Offsite backup → **Tigris global replication** (built-in)
- Restore capability → **S3 version restore** (`bm cloud restore --version-id`)
- Collaboration → **Deferred to teams/multi-user feature** (not v1 requirement)

**Complexity vs value trade-off:**
- Git integration adds: committer service, puller service, webhooks, LFS, merge conflicts
- Risk: Loop detection between Git ↔ rclone bisync ↔ local edits
- S3 versioning gives 80% of value with 5% of complexity

**When to revisit:**
- Teams/multi-user features (PR-based collaboration workflow)
- User requests for commit messages and branch-based workflows
- Need for fine-grained audit trail beyond S3 object metadata

---

## Original Specification (for reference)

## Why
Early access users want **transparent version history**, easy **offsite backup**, and a familiar **restore/branching** workflow. Git/GitHub integration would provide:
- Auditable history of every change (who/when/why)
- Branches/PRs for review and collaboration
- Offsite private backup under the user's control
- Escape hatch: users can always `git clone` their knowledge base

**Note:** These goals are now addressed via S3 versioning (SPEC-9.1) for single-user use case.

## Goals
- **Transparent**: Users keep using Basic Memory; Git runs behind the scenes.
- **Private**: Push to a **private GitHub repo** that the user owns (or tenant org).
- **Reliable**: No data loss, deterministic mapping of filesystem ↔ Git.
- **Composable**: Plays nicely with SPEC‑9 bisync and upcoming conflict features (SPEC‑9 Follow‑Ups).

**Non‑Goals (for v1):**
- Fine‑grained per‑file encryption in Git history (can be layered later).
- Large media optimization beyond Git LFS defaults.

## User Stories
1. *As a user*, I connect my GitHub and choose a private backup repo.
2. *As a user*, every change I make in cloud (or via bisync) is **committed** and **pushed** automatically.
3. *As a user*, I can **restore** a file/folder/project to a prior version.
4. *As a power user*, I can **git pull/push** directly to collaborate outside the app.
5. *As an admin*, I can enforce repo ownership (tenant org) and least‑privilege scopes.

## Scope
- **In scope:** Full repo backup of `/app/data/` (all projects) with optional selective subpaths.
- **Out of scope (v1):** Partial shallow mirrors; encrypted Git; cross‑provider SCM (GitLab/Bitbucket).

## Architecture
### Topology
- **Authoritative working tree**: `/app/data/` (bucket mount) remains the source of truth (SPEC‑9).
- **Bare repo** lives alongside: `/app/git/${tenant}/knowledge.git` (server‑side).
- **Mirror remote**: `github.com/<owner>/<repo>.git` (private).

```mermaid
flowchart LR
  A[/Users & Agents/] -->|writes/edits| B[/app/data/]
  B -->|file events| C[Committer Service]
  C -->|git commit| D[(Bare Repo)]
  D -->|push| E[(GitHub Private Repo)]
  E -->|webhook (push)| F[Puller Service]
  F -->|git pull/merge| D
  D -->|checkout/merge| B
```

### Services
- **Committer Service** (daemon):
  - Watches `/app/data/` for changes (inotify/poll)
  - Batches changes (debounce e.g. 2–5s)
  - Writes `.bmmeta` (if present) into commit message trailer (see Follow‑Ups)
  - `git add -A && git commit -m "chore(sync): <summary>

BM-Meta: <json>"`
  - Periodic `git push` to GitHub mirror (configurable interval)
- **Puller Service** (webhook target):
  - Receives GitHub webhook (push) → `git fetch`
  - **Fast‑forward** merges to `main` only; reject non‑FF unless policy allows
  - Applies changes back to `/app/data/` via clean checkout
  - Emits sync events for Basic Memory indexers

### Auth & Security
- **GitHub App** (recommended): minimal scopes: `contents:read/write`, `metadata:read`, webhook.
- Tenant‑scoped installation; repo created in user account or tenant org.
- Tokens stored in KMS/secret manager; rotated automatically.
- Optional policy: allow only **FF merges** on `main`; non‑FF requires PR.

### Repo Layout
- **Monorepo** (default): one repo per tenant mirrors `/app/data/` with subfolders per project.
- Optional multi‑repo mode (later): one repo per project.

### File Handling
- Honor `.gitignore` generated from `.bmignore.rclone` + BM defaults (cache, temp, state).
- **Git LFS** for large binaries (images, media) — auto track by extension/size threshold.
- Normalize newline + Unicode (aligns with Follow‑Ups).

### Conflict Model
- **Primary concurrency**: SPEC‑9 Follow‑Ups (`.bmmeta`, conflict copies) stays the first line of defense.
- **Git merges** are a **secondary** mechanism:
  - Server only auto‑merges **text** conflicts when trivial (FF or clean 3‑way).
  - Otherwise, create `name (conflict from <branch>, <ts>).md` and surface via events.

### Data Flow vs Bisync
- Bisync (rclone) continues between local sync dir ↔ bucket.
- Git sits **cloud‑side** between bucket and GitHub.
- On **pull** from GitHub → files written to `/app/data/` → picked up by indexers & eventually by bisync back to users.

## CLI & UX
New commands (cloud mode):
- `bm cloud git connect` — Launch GitHub App installation; create private repo; store installation id.
- `bm cloud git status` — Show connected repo, last push time, last webhook delivery, pending commits.
- `bm cloud git push` — Manual push (rarely needed).
- `bm cloud git pull` — Manual pull/FF (admin only by default).
- `bm cloud snapshot -m "message"` — Create a tagged point‑in‑time snapshot (git tag).
- `bm restore <path> --to <commit|tag>` — Restore file/folder/project to prior version.

Settings:
- `bm config set git.autoPushInterval=5s`
- `bm config set git.lfs.sizeThreshold=10MB`
- `bm config set git.allowNonFF=false`

## Migration & Backfill
- On connect, if repo empty: initial commit of entire `/app/data/`.
- If repo has content: require **one‑time import** path (clone to staging, reconcile, choose direction).

## Edge Cases
- Massive deletes: gated by SPEC‑9 `max_delete` **and** Git pre‑push hook checks.
- Case changes and rename detection: rely on git rename heuristics + Follow‑Ups move hints.
- Secrets: default ignore common secret patterns; allow custom deny list.

## Telemetry & Observability
- Emit `git_commit`, `git_push`, `git_pull`, `git_conflict` events with correlation IDs.
- `bm sync --report` extended with Git stats (commit count, delta bytes, push latency).

## Phased Plan
### Phase 0 — Prototype (1 sprint)
- Server: bare repo init + simple committer (batch every 10s) + manual GitHub token.
- CLI: `bm cloud git connect --token <PAT>` (dev‑only)
- Success: edits in `/app/data/` appear in GitHub within 30s.

### Phase 1 — GitHub App & Webhooks (1–2 sprints)
- Switch to GitHub App installs; create private repo; store installation id.
- Committer hardened (debounce 2–5s, backoff, retries).
- Puller service with webhook → FF merge → checkout to `/app/data/`.
- LFS auto‑track + `.gitignore` generation.
- CLI surfaces status + logs.

### Phase 2 — Restore & Snapshots (1 sprint)
- `bm restore` for file/folder/project with dry‑run.
- `bm cloud snapshot` tags + list/inspect.
- Policy: PR‑only non‑FF, admin override.

### Phase 3 — Selective & Multi‑Repo (nice‑to‑have)
- Include/exclude projects; optional per‑project repos.
- Advanced policies (branch protections, required reviews).

## Acceptance Criteria
- Changes to `/app/data/` are committed and pushed automatically within configurable interval (default ≤5s).
- GitHub webhook pull results in updated files in `/app/data/` (FF‑only by default).
- LFS configured and functioning; large files don't bloat history.
- `bm cloud git status` shows connected repo and last push/pull times.
- `bm restore` restores a file/folder to a prior commit with a clear audit trail.
- End‑to‑end works alongside SPEC‑9 bisync without loops or data loss.

## Risks & Mitigations
- **Loop risk (Git ↔ Bisync)**: Writes to `/app/data/` → bisync → local → user edits → back again. *Mitigation*: Debounce, commit squashing, idempotent `.bmmeta` versioning, and watch exclusion windows during pull.
- **Repo bloat**: Lots of binary churn. *Mitigation*: default LFS, size threshold, optional media‑only repo later.
- **Security**: Token leakage. *Mitigation*: GitHub App with short‑lived tokens, KMS storage, scoped permissions.
- **Merge complexity**: Non‑trivial conflicts. *Mitigation*: prefer FF; otherwise conflict copies + events; require PR for non‑FF.

## Open Questions
- Do we default to **monorepo** per tenant, or offer project‑per‑repo at connect time?
- Should `restore` write to a branch and open a PR, or directly modify `main`?
- How do we expose Git history in UI (timeline view) without users dropping to CLI?

## Appendix: Sample Config
```json
{
  "git": {
    "enabled": true,
    "repo": "https://github.com/<owner>/<repo>.git",
    "autoPushInterval": "5s",
    "allowNonFF": false,
    "lfs": { "sizeThreshold": 10485760 }
  }
}
```
