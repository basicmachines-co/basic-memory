# Deployment Architecture: Basic Memory as a Sidecar

## Overview

Basic Memory runs as a single sidecar container alongside a chat app/agent on a server (e.g., Railway). Multiple local users can sync their files to the remote instance. The architecture is intentionally simple.

## Architecture

```
Local User A                       Railway / Server
┌─────────────┐                   ┌─────────────────────────┐
│ Basic Memory │──bisync─────────▶│                         │
│ (local)      │                  │   Chat App / Agent      │
└─────────────┘                   │         │               │
                                  │         │ HTTP (:8000)  │
Local User B                      │         ▼               │
┌─────────────┐                   │   Basic Memory Sidecar  │
│ Basic Memory │──bisync─────────▶│   (single instance,     │
│ (local)      │                  │    single project,      │
└─────────────┘                   │    SQLite + WAL)        │
                                  └─────────────────────────┘
```

## Why a Sidecar

- ~5ms overhead vs in-process, negligible compared to ~2000ms LLM API calls
- Independent deployability — update Basic Memory without touching the app
- Clean separation of concerns
- Same FastAPI + ASGI transport the system already uses

## Single Project, Shared Knowledge

No need for per-user projects. One project, one shared knowledge base.

### Shared Knowledge (the default)

All users contribute to and benefit from the same pool:

```
notes/product-knowledge.md
notes/troubleshooting.md
decisions/architecture.md
```

Every useful insight from any user interaction goes here. Knowledge compounds across all users.

### Per-User Preferences (rare, directory-scoped)

```
users/alice.md  → - [preference] Prefers detailed explanations
users/bob.md    → - [preference] Wants concise answers #communication
```

The agent reads user context with `read_note("users/alice")` when needed, writes shared knowledge everywhere else. No project juggling required.

## Concurrency

- SQLite WAL mode handles concurrent reads without blocking
- Writes are serialized but fast — a few users will never notice
- Different users writing different files means no contention in practice
- If contention ever becomes real (dozens of concurrent writers), switch to Postgres backend (already supported, zero code changes)

## Persistence & Backups

### Markdown files are the source of truth

The database is a disposable index. Delete it, run `bm reset`, it rebuilds from files. Protect the files, not the DB.

### Railway volumes

- Persistent across deploys and restarts (network-attached storage)
- Single-node, no replication — hardware failure could lose data (rare)
- No built-in volume snapshots

### Backup strategy (layered)

1. **Local sync as primary backup** — each local user has a copy of the files via `bm project bisync`. If the remote volume dies, files exist locally. This is the best safety net.
2. **Periodic file backup** — cron job in the container that tars markdown files to S3/R2/Tigris. Cheap and reliable.
3. **Git as version control** — commit markdown files to a repo periodically. Get history + offsite backup. Diffs are meaningful since files are plain markdown.

### Recovery

- Volume lost? Restore from local sync or object storage backup.
- DB corrupted? Delete it, `bm reset` rebuilds from files.
- Files corrupted? Restore from git history or local copies.

## Scaling Thresholds

| Concern | When to worry | Solution |
|---------|--------------|----------|
| Write contention | Dozens of concurrent writers to same project | Switch to Postgres backend |
| Search latency | Heavy vector search under many simultaneous queries | Switch to Postgres + pgvector |
| Storage | Thousands of large files | Volume size increase, standard ops |
| Availability | Need zero-downtime guarantees | Multiple instances + Postgres + shared object storage |

For a few users: none of these apply. Start with SQLite, revisit if you hit actual problems.
