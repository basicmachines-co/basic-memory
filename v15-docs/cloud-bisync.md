# Cloud Bidirectional Sync (SPEC-9)

**Status**: New Feature
**PR**: #322
**Requires**: Active subscription, rclone installation

## What's New

v0.15.0 introduces **bidirectional cloud synchronization** using rclone bisync. Your local files sync automatically with the cloud, enabling multi-device workflows, backups, and collaboration.

## Quick Start

### One-Time Setup

```bash
# Install and configure cloud sync
bm cloud bisync-setup

# What it does:
# 1. Installs rclone
# 2. Gets tenant credentials
# 3. Configures rclone remote
# 4. Creates sync directory
# 5. Performs initial sync
```

### Regular Sync

```bash
# Recommended: Use standard sync command
bm sync                    # Syncs local → database
bm cloud bisync            # Syncs local ↔ cloud

# Or: Use watch mode (auto-sync every 60 seconds)
bm sync --watch
```

## How Bidirectional Sync Works

### Sync Architecture

```
Local Files          rclone bisync          Cloud Storage
~/basic-memory-      <─────────────>       s3://bucket/
cloud-sync/          (bidirectional)       tenant-id/
  ├── project-a/                              ├── project-a/
  ├── project-b/                              ├── project-b/
  └── notes/                                  └── notes/
```

### Sync Profiles

Three profiles optimize for different use cases:

| Profile | Conflicts | Max Deletes | Speed | Use Case |
|---------|-----------|-------------|-------|----------|
| **safe** | Keep both versions | 10 | Slower | Preserve all changes, manual conflict resolution |
| **balanced** | Use newer file | 25 | Medium | **Default** - auto-resolve most conflicts |
| **fast** | Use newer file | 50 | Fastest | Rapid iteration, trust newer versions |

### Conflict Resolution

**safe profile** (--conflict-resolve=none):
- Conflicting files saved as `file.conflict1`, `file.conflict2`
- Manual resolution required
- No data loss

**balanced/fast profiles** (--conflict-resolve=newer):
- Automatically uses the newer file
- Faster syncs
- Good for single-user workflows

## Commands

### bm cloud bisync-setup

One-time setup for cloud sync.

```bash
bm cloud bisync-setup

# Optional: Custom sync directory
bm cloud bisync-setup --dir ~/my-sync-folder
```

**What happens:**
1. Checks for/installs rclone
2. Generates scoped S3 credentials
3. Configures rclone remote
4. Creates local sync directory
5. Performs initial baseline sync (--resync)

**Configuration saved to:**
- `~/.basic-memory/config.json` - sync_dir path
- `~/.config/rclone/rclone.conf` - remote credentials
- `~/.basic-memory/bisync-state/{tenant_id}/` - sync state

### bm cloud bisync

Manual bidirectional sync.

```bash
# Basic sync (uses 'balanced' profile)
bm cloud bisync

# Choose sync profile
bm cloud bisync --profile safe
bm cloud bisync --profile balanced
bm cloud bisync --profile fast

# Dry run (preview changes)
bm cloud bisync --dry-run

# Force resync (rebuild baseline)
bm cloud bisync --resync

# Verbose output
bm cloud bisync --verbose
```

**Auto-registration:**
- Scans local directory for new projects
- Creates them on cloud before sync
- Ensures cloud knows about all local projects

### bm sync (Recommended)

The standard sync command now handles both local and cloud:

```bash
# One command for everything
bm sync                    # Local sync + cloud sync
bm sync --watch            # Continuous sync every 60s
```

## Sync Directory Structure

### Default Layout

```bash
~/basic-memory-cloud-sync/     # Configurable via --dir
├── project-a/                 # Auto-created local projects
│   ├── notes/
│   ├── ideas/
│   └── .bmignore              # Respected during sync
├── project-b/
│   └── documents/
└── .basic-memory/             # Metadata (ignored in sync)
```

### Important Paths

| Path | Purpose |
|------|---------|
| `~/basic-memory-cloud-sync/` | Default local sync directory |
| `~/basic-memory-cloud/` | Mount point (DO NOT use for bisync) |
| `~/.basic-memory/bisync-state/{tenant_id}/` | Sync state and history |
| `~/.basic-memory/.bmignore` | Patterns to exclude from sync |

**Critical:** Bisync and mount must use **different directories**

## File Filtering with .bmignore

### Default Patterns

Basic Memory respects `.bmignore` patterns (gitignore format):

```bash
# ~/.basic-memory/.bmignore (default)
.git
.DS_Store
node_modules
*.tmp
.env
__pycache__
.pytest_cache
.ruff_cache
.vscode
.idea
```

### How It Works

1. `.bmignore` patterns converted to rclone filter format
2. Auto-regenerated when `.bmignore` changes
3. Stored as `~/.basic-memory/.bmignore.rclone`
4. Applied to all bisync operations

### Custom Patterns

Edit `~/.basic-memory/.bmignore`:

```bash
# Your custom patterns
.git
*.log
temp/
*.backup
```

Next sync will use updated filters.

## Project Management

### Auto-Registration

Bisync automatically registers new local projects:

```bash
# You create a new project locally
mkdir ~/basic-memory-cloud-sync/new-project
echo "# Hello" > ~/basic-memory-cloud-sync/new-project/README.md

# Next sync auto-creates on cloud
bm cloud bisync
# → "Found 1 new local project, creating on cloud..."
# → "✓ Created project: new-project"
```

### Project Discovery

```bash
# List cloud projects
bm cloud status

# Shows:
# - Total projects
# - Last sync time
# - Storage used
```

### Cloud Mode

To work with cloud projects via CLI:

```bash
# Set cloud API URL
export BASIC_MEMORY_API_URL=https://api.basicmemory.cloud

# Or in config.json:
{
  "api_url": "https://api.basicmemory.cloud"
}

# Now CLI tools work against cloud
bm sync --project new-project        # Syncs cloud project
bm tools continue-conversation --project new-project
```

## Sync Workflow Examples

### Daily Workflow

```bash
# Morning: Start watch mode
bm sync --watch &

# Work in your sync directory
cd ~/basic-memory-cloud-sync/work-notes
vim ideas.md

# Changes auto-sync every 60s
# Watch output shows sync progress
```

### Multi-Device Workflow

**Device A:**
```bash
# Make changes
echo "# New Idea" > ~/basic-memory-cloud-sync/ideas/innovation.md

# Sync to cloud
bm cloud bisync
# → "✓ Sync completed - 1 file uploaded"
```

**Device B:**
```bash
# Pull changes from cloud
bm cloud bisync
# → "✓ Sync completed - 1 file downloaded"

# See the new file
cat ~/basic-memory-cloud-sync/ideas/innovation.md
# → "# New Idea"
```

### Conflict Scenario

**Using balanced profile (auto-resolve):**

```bash
# Both devices edit same file
# Device A: Updated at 10:00 AM
# Device B: Updated at 10:05 AM

# Device A syncs
bm cloud bisync
# → "✓ Sync completed"

# Device B syncs
bm cloud bisync
# → "Resolving conflict: using newer version"
# → "✓ Sync completed"
# → Device B's version (10:05) wins
```

**Using safe profile (manual resolution):**

```bash
bm cloud bisync --profile safe
# → "Conflict detected: ideas.md"
# → "Saved as: ideas.md.conflict1 and ideas.md.conflict2"
# → "Please resolve manually"

# Review both versions
diff ideas.md.conflict1 ideas.md.conflict2

# Merge and cleanup
vim ideas.md  # Merge manually
rm ideas.md.conflict*
```

## Monitoring and Status

### Check Sync Status

```bash
bm cloud status
```

**Shows:**
```
Cloud Bisync Status
┏━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Property            ┃ Value                      ┃
┡━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ Status              │ ✓ Initialized              │
│ Local Directory     │ ~/basic-memory-cloud-sync  │
│ Remote              │ s3://bucket/tenant-id      │
│ Last Sync           │ 2 minutes ago              │
│ Total Projects      │ 5                          │
└─────────────────────┴────────────────────────────┘
```

### Verify Integrity

```bash
bm cloud check
```

Compares local and cloud file hashes to detect:
- Corrupted files
- Missing files
- Sync drift

## Troubleshooting

### "First bisync requires --resync"

**Problem:** Initial sync not established

```bash
$ bm cloud bisync
Error: First bisync requires --resync to establish baseline
```

**Solution:**
```bash
bm cloud bisync --resync
```

### "Cannot use mount directory for bisync"

**Problem:** Trying to use mounted directory for sync

```bash
$ bm cloud bisync --dir ~/basic-memory-cloud
Error: Cannot use ~/basic-memory-cloud for bisync - it's the mount directory!
```

**Solution:** Use different directory
```bash
bm cloud bisync --dir ~/basic-memory-cloud-sync
```

### Sync Conflicts

**Problem:** Files modified on both sides

**Safe profile (manual):**
```bash
# Find conflict files
find ~/basic-memory-cloud-sync -name "*.conflict*"

# Review and merge
vimdiff file.conflict1 file.conflict2

# Keep desired version
mv file.conflict1 file
rm file.conflict2
```

**Balanced profile (auto):**
```bash
# Already resolved to newer version
# Check git history if needed
cd ~/basic-memory-cloud-sync
git log file.md
```

### Deleted Too Many Files

**Problem:** Exceeds max_delete threshold

```bash
$ bm cloud bisync
Error: Deletion exceeds safety limit (26 > 25)
```

**Solution:** Review deletions, then force if intentional
```bash
# Preview what would be deleted
bm cloud bisync --dry-run

# If intentional, use higher threshold profile
bm cloud bisync --profile fast  # max_delete=50

# Or resync to establish new baseline
bm cloud bisync --resync
```

### rclone Not Found

**Problem:** rclone not installed

```bash
$ bm cloud bisync
Error: rclone not found
```

**Solution:**
```bash
# Run setup again
bm cloud bisync-setup
# → Installs rclone automatically
```

## Configuration

### Bisync Config

Edit `~/.basic-memory/config.json`:

```json
{
  "bisync_config": {
    "sync_dir": "~/basic-memory-cloud-sync",
    "default_profile": "balanced",
    "auto_sync_interval": 60
  }
}
```

### rclone Config

Located at `~/.config/rclone/rclone.conf`:

```ini
[basic-memory-{tenant_id}]
type = s3
provider = AWS
env_auth = false
access_key_id = AKIA...
secret_access_key = ***
region = us-east-1
endpoint = https://fly.storage.tigris.dev
```

**Security:** This file contains credentials - keep private (mode 600)

## Performance Tips

1. **Use balanced profile**: Best trade-off for most users
2. **Enable watch mode**: `bm sync --watch` for auto-sync
3. **Optimize .bmignore**: Exclude build artifacts and temp files
4. **Batch changes**: Group related edits before sync
5. **Use fast profile**: For rapid iteration on solo projects

## Migration from WebDAV

If upgrading from v0.14.x WebDAV:

1. **Backup existing setup**
   ```bash
   cp -r ~/basic-memory ~/basic-memory.backup
   ```

2. **Run bisync setup**
   ```bash
   bm cloud bisync-setup
   ```

3. **Copy projects to sync directory**
   ```bash
   cp -r ~/basic-memory/* ~/basic-memory-cloud-sync/
   ```

4. **Initial sync**
   ```bash
   bm cloud bisync --resync
   ```

5. **Remove old WebDAV config** (if applicable)

## Security

- **Scoped credentials**: S3 credentials only access your tenant
- **Encrypted transport**: All traffic over HTTPS/TLS
- **No plain text secrets**: Credentials stored securely in rclone config
- **File permissions**: Config files restricted to user (600)
- **.bmignore**: Prevents syncing sensitive files

## See Also

- SPEC-9: Multi-Project Bidirectional Sync Architecture
- `cloud-authentication.md` - Required for cloud access
- `cloud-mount.md` - Alternative: mount cloud storage
- `env-file-removal.md` - Why .env files aren't synced
- `gitignore-integration.md` - File filtering patterns
