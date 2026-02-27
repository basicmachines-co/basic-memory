# Deployment Plan: Basic Memory Sidecar with Local Sync

## Goal

Deploy Basic Memory as a sidecar container on Railway. Local Basic Memory instances sync markdown files via object storage (Cloudflare R2). Either local or remote works independently — sync keeps them converged.

```
Local Machine                    Cloudflare R2              Railway
┌─────────────┐                 ┌─────────────┐           ┌─────────────────┐
│ Basic Memory │──rclone bisync─│ R2 Bucket   │─rclone───│ Basic Memory    │
│ (local files)│                │ (sync hub)  │  bisync   │ Sidecar         │
│              │                └─────────────┘           │ (SSE on :8000)  │
│ Works offline│                                          │                 │
│ Full MCP     │                                          │ Chat App/Agent  │
└─────────────┘                                          └─────────────────┘
```

---

## Phase 1: Remote Sidecar on Railway

### 1.1 Prepare the Docker Image

Basic Memory already has a Dockerfile. Key settings:

```dockerfile
# Existing Dockerfile runs:
# basic-memory mcp --transport sse --host 0.0.0.0 --port 8000
# Exposes port 8000, non-root user, Python 3.12-slim
```

Additions needed for sync support:

```dockerfile
# Install rclone for bisync
RUN curl https://rclone.org/install.sh | bash
```

### 1.2 Deploy to Railway

1. Create a new Railway project
2. Add a service from the Basic Memory repo (or Docker image)
3. Configure a **persistent volume** mounted at `/app/data` (markdown files)
4. Set environment variables:

```env
BASIC_MEMORY_SYNC_CHANGES=true
BASIC_MEMORY_SYNC_DELAY=1000
```

5. Expose port 8000 (internal or public with auth, depending on chat app setup)
6. Verify: `curl https://<railway-url>/health` or similar

### 1.3 Initialize the Project on the Sidecar

SSH into the Railway container or run via Railway CLI:

```bash
# Create the default project
basic-memory project add "shared" /app/data
basic-memory project default "shared"

# Verify
basic-memory status
```

---

## Phase 2: Object Storage Sync Hub (Cloudflare R2)

### Why R2

- S3-compatible (rclone works out of the box)
- Free egress (no cost for syncing down)
- 10 GB free tier (plenty for markdown files)
- No proprietary lock-in

### 2.1 Create the R2 Bucket

1. Cloudflare dashboard → R2 → Create bucket
2. Name: `basic-memory-sync` (or similar)
3. Create an API token with read/write access
4. Note: Account ID, Access Key ID, Secret Access Key

### 2.2 Configure rclone on the Remote Sidecar

Create rclone config on the Railway container:

```ini
# ~/.config/rclone/rclone.conf
[r2]
type = s3
provider = Cloudflare
access_key_id = <R2_ACCESS_KEY>
secret_access_key = <R2_SECRET_KEY>
endpoint = https://<ACCOUNT_ID>.r2.cloudflarestorage.com
acl = private
```

Test connectivity:

```bash
rclone ls r2:basic-memory-sync
```

### 2.3 Configure rclone on Local Machine

Same rclone config locally:

```bash
rclone config
# Add remote "r2" with same credentials
# Test: rclone ls r2:basic-memory-sync
```

---

## Phase 3: Bidirectional Sync

### 3.1 Establish Baseline (First Sync)

Pick one side as the source of truth for initial sync. If starting fresh, local is the source:

```bash
# From local machine — push local files to R2
rclone sync ~/basic-memory/shared r2:basic-memory-sync/shared \
  --exclude ".basic-memory/**" \
  --exclude ".obsidian/**" \
  --exclude "*.pyc"

# From Railway sidecar — pull R2 files to container
rclone sync r2:basic-memory-sync/shared /app/data \
  --exclude ".basic-memory/**"

# Rebuild the DB index on the sidecar
basic-memory reset
```

### 3.2 Establish Bisync Baseline

rclone bisync requires a one-time `--resync` to establish tracking state:

```bash
# On local machine
rclone bisync ~/basic-memory/shared r2:basic-memory-sync/shared \
  --resync \
  --exclude ".basic-memory/**" \
  --exclude ".obsidian/**" \
  --create-empty-src-dirs

# On Railway sidecar
rclone bisync /app/data r2:basic-memory-sync/shared \
  --resync \
  --exclude ".basic-memory/**" \
  --create-empty-src-dirs
```

### 3.3 Ongoing Sync

After baseline, regular bisync (no `--resync`):

```bash
# Local → R2 → Remote (two-step)

# Step 1: Local bisync with R2
rclone bisync ~/basic-memory/shared r2:basic-memory-sync/shared \
  --exclude ".basic-memory/**" \
  --exclude ".obsidian/**"

# Step 2: Remote bisync with R2
rclone bisync /app/data r2:basic-memory-sync/shared \
  --exclude ".basic-memory/**"
```

### 3.4 Automate Sync

**On the Railway sidecar** — cron job or supervisor process:

```bash
# Sync every 5 minutes
*/5 * * * * rclone bisync /app/data r2:basic-memory-sync/shared \
  --exclude ".basic-memory/**" 2>&1 | logger -t bm-sync
```

After each sync, Basic Memory's file watcher (`BASIC_MEMORY_SYNC_CHANGES=true`) detects changes and reindexes automatically.

**On local machine** — same cron, or on-demand:

```bash
# Manual sync when you want to push/pull
rclone bisync ~/basic-memory/shared r2:basic-memory-sync/shared \
  --exclude ".basic-memory/**" \
  --exclude ".obsidian/**"
```

Or use a launchd plist (macOS) / systemd timer (Linux) for automatic periodic sync.

---

## Phase 4: Chat App Integration

### 4.1 Connect Agent to Sidecar

The chat app/agent connects to the sidecar via HTTP on port 8000 (SSE transport). Within Railway, this is internal networking — no public exposure needed.

```
Chat App Service → http://basic-memory-sidecar.railway.internal:8000
```

The agent uses MCP tools (`write_note`, `search_notes`, `build_context`, etc.) over this connection.

### 4.2 Knowledge Organization

```
/app/data/
├── notes/              # Shared knowledge (all users benefit)
│   ├── product.md
│   ├── troubleshooting.md
│   └── decisions.md
├── conversations/      # Conversation summaries
│   └── 2026-02-26-alice-onboarding.md
└── users/              # Per-user preferences (rare)
    ├── alice.md
    └── bob.md
```

The agent:
- Reads `users/{name}.md` at conversation start for preferences
- Writes shared knowledge to `notes/` by default
- Records conversation summaries to `conversations/`

### 4.3 Agent System Prompt Guidance

```
You have access to Basic Memory via MCP tools.

- Search before creating: always check if knowledge exists before writing new notes
- Write shared knowledge to "notes/" directory
- Read user preferences from "users/{username}.md" at conversation start
- Record important decisions and discoveries to "notes/" or "decisions/"
- Use observations: - [category] content #tags (context)
- Use relations: - relates_to [[Other Note]] to build the knowledge graph
```

---

## Phase 5: Local Development & Switching

### 5.1 Work Locally (Offline)

Local Basic Memory works independently with its own SQLite DB:

```bash
# Local MCP server (stdio transport, used by Claude Desktop etc.)
basic-memory mcp

# Everything works offline — search, write, read, build_context
```

### 5.2 Sync Before/After Local Work

```bash
# Pull latest from R2 before starting
rclone bisync ~/basic-memory/shared r2:basic-memory-sync/shared \
  --exclude ".basic-memory/**" --exclude ".obsidian/**"

# Work locally...

# Push changes when done
rclone bisync ~/basic-memory/shared r2:basic-memory-sync/shared \
  --exclude ".basic-memory/**" --exclude ".obsidian/**"
```

### 5.3 Conflict Handling

rclone bisync handles conflicts by renaming:
- If both sides changed the same file, the remote version gets a `.conflict` suffix
- Review and merge manually (rare with a few users)
- Avoid by convention: shared notes are append-mostly, user notes are single-owner

---

## Phase 6: Backups

### 6.1 R2 IS the Backup

With bisync running, R2 always has a copy of all files. Three copies exist:
1. Local machine filesystem
2. Cloudflare R2 bucket
3. Railway persistent volume

### 6.2 Git Snapshots (Optional, Recommended)

On the Railway sidecar, periodically commit to a git repo:

```bash
# Cron: daily git snapshot
cd /app/data && git add -A && git commit -m "snapshot $(date +%Y-%m-%d)" || true
git push origin main
```

This gives you versioned history of all knowledge changes.

### 6.3 Recovery Scenarios

| Scenario | Recovery |
|----------|----------|
| Railway volume lost | `rclone sync r2:basic-memory-sync/shared /app/data` then `bm reset` |
| R2 bucket lost | `rclone sync /local/path r2:basic-memory-sync/shared` (restore from local) |
| Local machine lost | `rclone sync r2:basic-memory-sync/shared ~/basic-memory/shared` (restore from R2) |
| DB corrupted (either side) | `bm reset` — rebuilds from markdown files |
| File conflict | Check `.conflict` files, merge manually, re-sync |

---

## Checklist

### Setup (One-Time)
- [ ] Create Cloudflare R2 bucket and API token
- [ ] Deploy Basic Memory container to Railway with persistent volume
- [ ] Configure rclone on Railway sidecar (R2 credentials)
- [ ] Configure rclone on local machine (same R2 credentials)
- [ ] Initialize Basic Memory project on sidecar (`bm project add`)
- [ ] Run initial sync: local → R2 → remote
- [ ] Establish bisync baseline (`--resync`) on both sides
- [ ] Set up sync cron on Railway sidecar
- [ ] Verify: write a note locally, sync, confirm it appears on sidecar
- [ ] Verify: write a note via agent on sidecar, sync, confirm it appears locally

### Chat App Integration
- [ ] Connect chat app to sidecar on Railway internal network
- [ ] Configure agent system prompt with Basic Memory guidance
- [ ] Create directory structure (`notes/`, `users/`, `conversations/`)
- [ ] Test: agent writes a note, verify it syncs to local

### Ongoing
- [ ] Set up local sync automation (launchd/systemd or manual habit)
- [ ] Optional: git snapshot cron on sidecar
- [ ] Optional: R2 lifecycle rules for old versions
- [ ] Monitor Railway volume usage
