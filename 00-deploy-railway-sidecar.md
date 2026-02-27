# Phase 00: Deploy Basic Memory Sidecar on Railway

## Goal

Get a Basic Memory instance running on Railway with a persistent volume, accessible via HTTP (SSE transport on port 8000). No sync yet — just a working remote instance.

## Prerequisites

- Railway account
- Docker (for local testing before deploying)
- The Basic Memory repo cloned locally

---

## Task List

### Phase 00-A: Local Docker Verification
- [x] Task 1: Build Docker image locally (must use `--platform linux/amd64` on Apple Silicon)
- [x] Task 2: Run container with sidecar env vars
- [x] Task 3: Verify container starts (check logs for startup sequence)
- [x] Task 4: Verify `basic-memory --version` works inside container (v0.18.5)
- [x] Task 5: Verify `basic-memory status` shows a project with no errors
- [x] Task 6: Write a test note inside the container (CLI flag is `--folder` not `--directory`)
- [x] Task 7: Read the test note back to confirm persistence
- [x] Task 8: Verify SSE endpoint responds on port 8000 (path is `/mcp` not `/sse` in FastMCP 3.x)
- [x] Task 9: Clean up local container and volumes

### Phase 00-B: Create Sidecar Docker Compose
- [x] Task 10: Create `docker-compose.sidecar.yml` with sidecar-specific config
- [x] Task 11: Test docker compose up with the sidecar compose file
- [x] Task 12: Verify sidecar compose works end-to-end (write/read/SSE all work)

### Phase 00-C: Railway Deployment (manual — requires dashboard)
- [x] Task 13: Document Railway env vars and volume config for easy copy-paste
- [x] Task 14: Commit all changes

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
BASIC_MEMORY_HOME=/app/data/shared
BASIC_MEMORY_DEFAULT_PROJECT=main
BASIC_MEMORY_CONFIG_DIR=/app/data/.config
BASIC_MEMORY_SYNC_CHANGES=true
BASIC_MEMORY_SYNC_DELAY=1000
BASIC_MEMORY_LOG_LEVEL=INFO
```

**Volume** (create in Railway dashboard):

- Mount path: `/app/data`
- This single volume persists markdown files, config, and SQLite DB
- Config stored at `/app/data/.config` via `BASIC_MEMORY_CONFIG_DIR`

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

## Learnings from Implementation

1. **Apple Silicon requires `--platform linux/amd64`**: The `sqlite_vec` binary is x86-only. Build with `docker build --platform linux/amd64` or set `platform: linux/amd64` in compose.
2. **Config lives at `~appuser/.basic-memory/`**: The Dockerfile creates `/app/.basic-memory/` but the appuser's home is `/home/appuser/`. Use `BASIC_MEMORY_CONFIG_DIR=/app/data/.config` to put config in the persistent volume.
3. **Single volume is best**: Put config, DB, and data in one volume at `/app/data`. Avoids permission issues and Railway's one-volume-per-service limit.
4. **SSE path is `/mcp` not `/sse`**: FastMCP 3.x changed the endpoint path.
5. **CLI write-note uses `--folder` not `--directory`**: The MCP tool parameter is `directory` but the CLI flag is `--folder`.
6. **`BASIC_MEMORY_DEFAULT_PROJECT` doesn't rename auto-created project**: The auto-bootstrap always creates a project named "main". The env var just sets which project is default.
7. **Semantic search disabled for sidecar**: `sqlite_vec` has ELF compatibility issues under emulation. Set `BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED=false` for local testing. Railway runs native amd64 so this can be re-enabled there.

## Confidence Level: 95%

The Dockerfile, startup sequence, and auto-initialization are well-understood from code inspection. The only uncertainty is Railway-specific volume configuration (mount path behavior, single vs multiple volumes), which may need minor adjustments during deploy. If Railway behaves differently than expected, stop and investigate before proceeding.
