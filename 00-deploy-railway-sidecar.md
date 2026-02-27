# Phase 00: Deploy Basic Memory Sidecar on Railway

## Goal

Get a Basic Memory instance running on Railway with a persistent volume, accessible via HTTP (SSE transport on port 8000). No sync yet — just a working remote instance.

## Prerequisites

- Railway account
- Docker (for local testing before deploying)
- The Basic Memory repo cloned locally

---

## Step 1: Verify the Docker Image Locally

The existing Dockerfile works. Test it before deploying.

```bash
# Build locally
docker build -t basic-memory-sidecar .

# Run locally
docker run -d \
  --name bm-sidecar \
  -p 8000:8000 \
  -v bm-data:/app/data/basic-memory \
  -v bm-config:/app/.basic-memory \
  -e BASIC_MEMORY_DEFAULT_PROJECT=shared \
  -e BASIC_MEMORY_SYNC_CHANGES=true \
  -e BASIC_MEMORY_SYNC_DELAY=1000 \
  -e BASIC_MEMORY_LOG_LEVEL=INFO \
  basic-memory-sidecar
```

### What happens on startup (automatic, no manual steps):

1. `basic-memory mcp --transport sse --host 0.0.0.0 --port 8000` runs
2. `McpContainer.create()` reads/creates config at `/app/.basic-memory/config.json`
3. If no projects exist, auto-creates "main" project at `BASIC_MEMORY_HOME` (default `~/basic-memory`, but Dockerfile creates `/app/data/basic-memory`)
4. Database auto-created at `/app/.basic-memory/memory.db`
5. Alembic migrations run automatically
6. SyncCoordinator starts file watcher in background
7. SSE server listens on port 8000

### Verify locally:

```bash
# Check container is running
docker logs bm-sidecar

# Test MCP endpoint exists (SSE transport)
# The SSE endpoint won't respond to plain curl like a REST API,
# but you can check the container is healthy:
docker exec bm-sidecar basic-memory --version
docker exec bm-sidecar basic-memory status
```

### Verify via MCP client (optional):

If you have an MCP client that supports SSE transport, connect to `http://localhost:8000/sse` and call `list_memory_projects()`.

### Clean up local test:

```bash
docker stop bm-sidecar && docker rm bm-sidecar
docker volume rm bm-data bm-config
```

---

## Step 2: Deploy to Railway

### Option A: From Docker Image (recommended)

1. Push to a container registry (GitHub Container Registry, Docker Hub, etc.):
   ```bash
   docker tag basic-memory-sidecar ghcr.io/<your-org>/basic-memory-sidecar:latest
   docker push ghcr.io/<your-org>/basic-memory-sidecar:latest
   ```

2. In Railway:
   - Create new project
   - Add service → Docker Image → `ghcr.io/<your-org>/basic-memory-sidecar:latest`

### Option B: From Repo

1. In Railway:
   - Create new project
   - Add service → GitHub Repo → select the basic-memory fork/repo
   - Railway will detect the Dockerfile and build automatically

### Configure Railway Service

**Environment Variables** (set in Railway dashboard):

```
BASIC_MEMORY_DEFAULT_PROJECT=shared
BASIC_MEMORY_SYNC_CHANGES=true
BASIC_MEMORY_SYNC_DELAY=1000
BASIC_MEMORY_LOG_LEVEL=INFO
```

**Volume** (create in Railway dashboard):

- Mount path: `/app/data`
- This persists markdown files across deploys

**Note on config volume**: The config and SQLite DB live at `/app/.basic-memory/`. Railway allows one volume per service. Two options:
1. Mount at `/app/data` only — config/DB regenerate on each deploy (safe, DB rebuilds from files)
2. Use a custom `BASIC_MEMORY_CONFIG_DIR=/app/data/.config` to store config alongside files in the same volume

Recommended: Option 2 — keeps everything in one volume:

```
BASIC_MEMORY_CONFIG_DIR=/app/data/.config
```

**Port**: Railway auto-detects port 8000 from the Dockerfile `EXPOSE` directive.

**Health Check**: The Dockerfile includes a health check (`basic-memory --version`). Railway will use this.

---

## Step 3: Verify Railway Deployment

```bash
# Check Railway logs for startup sequence:
# - "Starting MCP server"
# - "Database initialized"
# - "SyncCoordinator started"
# - No errors

# If Railway provides a public URL, test connectivity:
# (The SSE endpoint is at /sse on the Railway URL)
curl -N https://<railway-url>/sse
# Should return SSE stream headers (content-type: text/event-stream)
```

---

## Step 4: Initialize the Project

The container auto-creates a "main" project. We want it named "shared" instead.

SSH into Railway or use `railway run`:

```bash
# Check current state
basic-memory project list

# If "main" exists but we want "shared":
basic-memory project add shared /app/data/shared
basic-memory project default shared

# Create the directory structure
mkdir -p /app/data/shared/notes
mkdir -p /app/data/shared/users
mkdir -p /app/data/shared/conversations
mkdir -p /app/data/shared/decisions
```

Or let the `BASIC_MEMORY_DEFAULT_PROJECT=shared` env var handle it — the auto-created project will be named "shared" if `BASIC_MEMORY_HOME=/app/data/shared`.

Add this env var:
```
BASIC_MEMORY_HOME=/app/data/shared
```

---

## Step 5: Write a Test Note

From inside the container (or via MCP client):

```bash
# Via CLI
basic-memory tool write-note \
  --title "Deployment Test" \
  --content "- [test] Sidecar deployment verified #deployment" \
  --directory "notes"

# Verify
basic-memory tool read-note --identifier "Deployment Test"
basic-memory status
```

---

## Verification Checklist

- [ ] Docker image builds locally without errors
- [ ] Container starts, logs show successful initialization
- [ ] `basic-memory status` shows project with 0 sync errors
- [ ] Can write and read a note via CLI inside the container
- [ ] Railway deployment starts successfully (check logs)
- [ ] Railway persistent volume is mounted at `/app/data`
- [ ] Config stored in volume (not lost on redeploy)
- [ ] Project "shared" exists with correct directory structure

## What This Phase Does NOT Cover

- No sync with local machines (Phase 01)
- No rclone or R2 setup (Phase 01)
- No chat app integration (Phase 02)
- No cron jobs or automation (Phase 01)

## Troubleshooting

**Container exits immediately**: Check logs for Python import errors. Ensure `uv sync --locked` completed during build.

**"No project found" errors**: Set `BASIC_MEMORY_HOME` and `BASIC_MEMORY_DEFAULT_PROJECT` env vars. The auto-bootstrap creates a project from `BASIC_MEMORY_HOME`.

**Database errors on startup**: Usually means the volume isn't mounted. Check that `/app/data` persists. If DB is corrupted, delete it — `bm reset` rebuilds from files.

**Port not reachable on Railway**: Ensure Railway sees the `EXPOSE 8000` in the Dockerfile. Check the service's networking settings in Railway dashboard.

---

## Confidence Level: 95%

The Dockerfile, startup sequence, and auto-initialization are well-understood from code inspection. The only uncertainty is Railway-specific volume configuration (mount path behavior, single vs multiple volumes), which may need minor adjustments during deploy. If Railway behaves differently than expected, stop and investigate before proceeding.
