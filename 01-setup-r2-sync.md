# Phase 01: Set Up Cloudflare R2 Sync Between Local and Railway

## Goal

Establish bidirectional file sync between your local Basic Memory and the Railway sidecar, using Cloudflare R2 as the sync hub. After this phase, changes on either side propagate to the other via R2.

## Prerequisites

- Phase 00 complete: Railway sidecar running with persistent volume
- Cloudflare account (free tier is sufficient)
- rclone installed locally (`brew install rclone` on macOS)
- Local Basic Memory project with markdown files

---

## Step 1: Create Cloudflare R2 Bucket

1. Go to Cloudflare dashboard → R2 Object Storage
2. Create a bucket:
   - Name: `basic-memory-sync` (or your preference)
   - Location: Auto (or nearest region)
3. Create an API token:
   - R2 dashboard → Manage R2 API Tokens → Create API Token
   - Permissions: Object Read & Write
   - Scope: Apply to specific bucket → `basic-memory-sync`
4. Save these values:
   - **Account ID** (from Cloudflare dashboard URL or overview page)
   - **Access Key ID** (from the API token creation)
   - **Secret Access Key** (from the API token creation)

### Cost verification

R2 free tier includes:
- 10 GB storage/month
- 1 million Class B (read) operations/month
- 10 million Class A (write) operations/month
- **$0 egress always**

For markdown files from a few users, this is effectively free indefinitely.

---

## Step 2: Configure rclone Locally

```bash
# Check rclone is installed
rclone --version

# If not installed:
# macOS: brew install rclone
# Linux: sudo apt install rclone  (or curl https://rclone.org/install.sh | sudo bash)
```

Create the rclone remote configuration:

```bash
rclone config

# Interactive setup:
# n) New remote
# name> r2
# Storage> s3
# provider> Cloudflare
# access_key_id> <your R2 Access Key ID>
# secret_access_key> <your R2 Secret Access Key>
# endpoint> https://<ACCOUNT_ID>.r2.cloudflarestorage.com
# (accept defaults for everything else)
```

Or write the config directly:

```bash
cat >> ~/.config/rclone/rclone.conf << 'EOF'
[r2]
type = s3
provider = Cloudflare
access_key_id = <YOUR_ACCESS_KEY_ID>
secret_access_key = <YOUR_SECRET_ACCESS_KEY>
endpoint = https://<ACCOUNT_ID>.r2.cloudflarestorage.com
acl = private
no_check_bucket = true
EOF
```

### Verify local rclone connectivity:

```bash
# List bucket contents (should be empty)
rclone ls r2:basic-memory-sync

# Write a test file
echo "test" | rclone rcat r2:basic-memory-sync/test.txt

# Read it back
rclone cat r2:basic-memory-sync/test.txt

# Clean up
rclone delete r2:basic-memory-sync/test.txt
```

---

## Step 3: Install rclone on Railway Sidecar

The existing Dockerfile does NOT include rclone. Two options:

### Option A: Modify the Dockerfile (recommended if you control the image)

Add to the Dockerfile after the system deps:

```dockerfile
# Install rclone for R2 sync
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl unzip \
    && curl -O https://downloads.rclone.org/current/rclone-current-linux-amd64.zip \
    && unzip rclone-current-linux-amd64.zip \
    && cp rclone-*-linux-amd64/rclone /usr/local/bin/ \
    && rm -rf rclone-* \
    && apt-get purge -y curl unzip \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*
```

### Option B: Install at runtime (if you can't modify the image)

In Railway, add a start command that installs rclone before starting Basic Memory:

```bash
# Railway custom start command:
apt-get update && apt-get install -y rclone && basic-memory mcp --transport sse --host 0.0.0.0 --port 8000
```

This is slower (installs on every deploy) but doesn't require a custom image.

### Configure rclone on Railway

Set Railway environment variables for rclone config (avoids needing a config file):

```
RCLONE_CONFIG_R2_TYPE=s3
RCLONE_CONFIG_R2_PROVIDER=Cloudflare
RCLONE_CONFIG_R2_ACCESS_KEY_ID=<your R2 Access Key ID>
RCLONE_CONFIG_R2_SECRET_ACCESS_KEY=<your R2 Secret Access Key>
RCLONE_CONFIG_R2_ENDPOINT=https://<ACCOUNT_ID>.r2.cloudflarestorage.com
RCLONE_CONFIG_R2_ACL=private
RCLONE_CONFIG_R2_NO_CHECK_BUCKET=true
```

rclone reads `RCLONE_CONFIG_<REMOTE>_<KEY>` env vars automatically — no config file needed.

### Verify rclone on Railway:

```bash
# SSH into Railway or use railway run:
rclone ls r2:basic-memory-sync
# Should show empty or test files from Step 2
```

---

## Step 4: Create Sync Filter File

Create a filter file that both sides use to exclude non-markdown files:

```bash
# On local machine, create the filter file
cat > ~/.config/rclone/bm-sync-filter.txt << 'EOF'
# Exclude database and config (each side maintains its own)
- .basic-memory/**
- .obsidian/**
- .git/**
- **/.DS_Store
- **/*.pyc
- **/__pycache__/**
- **/*.tmp
- **/*.swp
- **/*.swo
EOF
```

On Railway, store the same filter content. Either:
- Bake it into the Docker image
- Store in the persistent volume at `/app/data/.sync-filter.txt`
- Or pass excludes as env var / command flags

---

## Step 5: Initial Sync — Local to R2

Push your local markdown files to R2 first (establishes R2 as the shared baseline):

```bash
# Dry run first — see what would be synced
rclone sync ~/basic-memory r2:basic-memory-sync/shared \
  --filter-from ~/.config/rclone/bm-sync-filter.txt \
  --dry-run -v

# If the output looks correct, do the real sync
rclone sync ~/basic-memory r2:basic-memory-sync/shared \
  --filter-from ~/.config/rclone/bm-sync-filter.txt \
  -v
```

Verify:

```bash
rclone ls r2:basic-memory-sync/shared
# Should show your markdown files
```

---

## Step 6: Initial Sync — R2 to Railway

Pull files from R2 into the Railway container:

```bash
# On Railway (via SSH or railway run):
rclone sync r2:basic-memory-sync/shared /app/data/shared \
  --filter-from /app/data/.sync-filter.txt \
  -v

# Rebuild the database index from the synced files
basic-memory reset
basic-memory status
# Should show files synced, 0 errors
```

---

## Step 7: Establish Bisync Baseline

rclone bisync requires a one-time `--resync` to create tracking state.

### On local machine:

```bash
# Establish bisync baseline
rclone bisync ~/basic-memory r2:basic-memory-sync/shared \
  --filter-from ~/.config/rclone/bm-sync-filter.txt \
  --create-empty-src-dirs \
  --resync \
  -v

# Verify: check bisync state was created
ls ~/.cache/rclone/bisync/
# Should show state files
```

### On Railway:

```bash
rclone bisync /app/data/shared r2:basic-memory-sync/shared \
  --filter-from /app/data/.sync-filter.txt \
  --create-empty-src-dirs \
  --resync \
  -v
```

---

## Step 8: Test Bidirectional Sync

### Test 1: Local → R2 → Railway

```bash
# On local machine: create a test note
echo "# Test Note\n\n- [test] Created locally" > ~/basic-memory/notes/sync-test-local.md

# Sync local to R2
rclone bisync ~/basic-memory r2:basic-memory-sync/shared \
  --filter-from ~/.config/rclone/bm-sync-filter.txt \
  -v

# Verify on R2
rclone cat r2:basic-memory-sync/shared/notes/sync-test-local.md

# On Railway: sync R2 to container
rclone bisync /app/data/shared r2:basic-memory-sync/shared \
  --filter-from /app/data/.sync-filter.txt \
  -v

# Verify on Railway
cat /app/data/shared/notes/sync-test-local.md
basic-memory tool read-note --identifier "sync-test-local"
```

### Test 2: Railway → R2 → Local

```bash
# On Railway: create a test note via Basic Memory
basic-memory tool write-note \
  --title "Sync Test Remote" \
  --content "- [test] Created on Railway sidecar" \
  --directory "notes"

# Sync Railway to R2
rclone bisync /app/data/shared r2:basic-memory-sync/shared \
  --filter-from /app/data/.sync-filter.txt \
  -v

# On local: sync R2 to local
rclone bisync ~/basic-memory r2:basic-memory-sync/shared \
  --filter-from ~/.config/rclone/bm-sync-filter.txt \
  -v

# Verify locally
cat ~/basic-memory/notes/sync-test-remote.md
```

### Clean up test files:

```bash
rm ~/basic-memory/notes/sync-test-local.md
# Re-sync to propagate deletion
```

---

## Step 9: Automate Sync

### On Railway: cron via supervisor or entrypoint script

Create a sync script on the Railway volume:

```bash
cat > /app/data/.sync.sh << 'SCRIPT'
#!/bin/bash
while true; do
  sleep 300  # 5 minutes
  rclone bisync /app/data/shared r2:basic-memory-sync/shared \
    --filter-from /app/data/.sync-filter.txt \
    --resilient \
    --conflict-resolve newer \
    2>&1 | logger -t bm-r2-sync
done
SCRIPT
chmod +x /app/data/.sync.sh
```

Modify the Docker entrypoint to run both the sync loop and the MCP server:

```dockerfile
# Option: use a custom entrypoint
COPY entrypoint.sh /app/entrypoint.sh
CMD ["/app/entrypoint.sh"]
```

```bash
#!/bin/bash
# entrypoint.sh

# Start R2 sync loop in background (if rclone is configured)
if command -v rclone &>/dev/null && [ -n "$RCLONE_CONFIG_R2_TYPE" ]; then
  echo "Starting R2 sync loop (every 5 minutes)..."
  while true; do
    sleep 300
    rclone bisync /app/data/shared r2:basic-memory-sync/shared \
      --exclude ".basic-memory/**" \
      --exclude ".obsidian/**" \
      --resilient \
      --conflict-resolve newer \
      2>&1 | head -20
  done &
fi

# Start Basic Memory MCP server (foreground)
exec basic-memory mcp --transport sse --host 0.0.0.0 --port 8000
```

### On local machine: launchd (macOS) or manual

For manual sync (simplest):

```bash
# Add to ~/.zshrc or create an alias
alias bm-sync='rclone bisync ~/basic-memory r2:basic-memory-sync/shared \
  --filter-from ~/.config/rclone/bm-sync-filter.txt \
  --resilient --conflict-resolve newer -v'
```

Then just run `bm-sync` before and after working locally.

For automated (macOS launchd):

```xml
<!-- ~/Library/LaunchAgents/com.basicmemory.sync.plist -->
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.basicmemory.sync</string>
    <key>ProgramArguments</key>
    <array>
        <string>/opt/homebrew/bin/rclone</string>
        <string>bisync</string>
        <string>/Users/tyler/basic-memory</string>
        <string>r2:basic-memory-sync/shared</string>
        <string>--filter-from</string>
        <string>/Users/tyler/.config/rclone/bm-sync-filter.txt</string>
        <string>--resilient</string>
        <string>--conflict-resolve</string>
        <string>newer</string>
    </array>
    <key>StartInterval</key>
    <integer>300</integer>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/bm-sync.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/bm-sync.log</string>
</dict>
</plist>
```

```bash
launchctl load ~/Library/LaunchAgents/com.basicmemory.sync.plist
```

---

## Verification Checklist

- [ ] R2 bucket created on Cloudflare
- [ ] rclone configured locally with R2 credentials
- [ ] rclone connectivity verified (ls, cat, rcat work)
- [ ] rclone installed/available on Railway sidecar
- [ ] rclone configured on Railway (via env vars)
- [ ] Filter file created on both sides
- [ ] Initial sync: local files pushed to R2
- [ ] Initial sync: R2 files pulled to Railway
- [ ] Bisync baseline established on both sides (--resync)
- [ ] Test: local note appears on Railway after sync
- [ ] Test: Railway note appears locally after sync
- [ ] Basic Memory file watcher detects synced files (check `bm status`)
- [ ] Automated sync running on Railway (cron/loop)
- [ ] Local sync working (manual alias or launchd)

## Conflict Behavior

- **One side changed**: File copies to the other side (normal)
- **Both sides changed same file**: rclone keeps both, loser gets `.conflict1` suffix
- **File deleted on one side**: Deletion propagates to the other side
- **`--conflict-resolve newer`**: Most recent timestamp wins, loser renamed

With a few users and 5-minute sync intervals, conflicts should be extremely rare. If one occurs, check for `.conflict` files and merge manually.

## Troubleshooting

**"bisync not found"**: rclone version too old. Need v1.58+ for bisync, v1.64+ for `--create-empty-src-dirs`. Update rclone.

**"Failed to bisync: must use --resync"**: Bisync state is corrupted or missing. Run with `--resync` to re-establish baseline (safe, just recalculates state).

**"Access Denied" on R2**: Check API token permissions (needs Object Read & Write), check bucket name matches, check account ID in endpoint URL.

**Files sync but Basic Memory doesn't see them**: The file watcher (`BASIC_MEMORY_SYNC_CHANGES=true`) should detect changes. If not, run `basic-memory reset` to force reindex. Check that files land in the correct project directory.

**R2 shows files but Railway is empty**: Verify the rclone remote path matches. `r2:basic-memory-sync/shared` must map to the Railway mount at `/app/data/shared`.

---

## Confidence Level: 90%

rclone bisync with R2 is well-documented and standard. The main uncertainties:
1. Railway's ability to run background processes alongside the main CMD (the entrypoint.sh approach should work but needs testing)
2. rclone env var config (`RCLONE_CONFIG_R2_*`) — well-documented but worth verifying on Railway specifically
3. File watcher picking up rclone-synced files — should work since watchfiles uses OS-level notifications, but bulk file drops might need a delay

If any of these don't work as expected: stop, investigate, try alternative approaches before proceeding.
