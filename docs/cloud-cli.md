# Basic Memory Cloud CLI Guide

The Basic Memory Cloud CLI provides seamless integration between local and cloud knowledge bases using **project-scoped synchronization**. Each project can optionally sync with the cloud, giving you fine-grained control over what syncs and where.

## Overview

The cloud CLI enables you to:
- **Toggle cloud mode** - All regular `bm` commands work with cloud when enabled
- **Project-scoped sync** - Each project independently manages its sync configuration
- **Explicit operations** - Sync only what you want, when you want
- **Bidirectional sync** - Keep local and cloud in sync with rclone bisync
- **Offline access** - Work locally, sync when ready

## Prerequisites

Before using Basic Memory Cloud, you need:

- **Active Subscription**: An active Basic Memory Cloud subscription is required to access cloud features
- **Subscribe**: Visit [https://basicmemory.com/subscribe](https://basicmemory.com/subscribe) to sign up

If you attempt to log in without an active subscription, you'll receive a "Subscription Required" error with a link to subscribe.

## Architecture: Project-Scoped Sync

### The Problem

**Old approach (SPEC-8):** All projects lived in a single `~/basic-memory-cloud-sync/` directory. This caused:
- ❌ Directory conflicts between mount and bisync
- ❌ Auto-discovery creating phantom projects
- ❌ Confusion about what syncs and when
- ❌ All-or-nothing sync (couldn't sync just one project)

**New approach (SPEC-20):** Each project independently configures sync.

### How It Works

**Projects can exist in three states:**

1. **Cloud-only** - Project exists on cloud, no local copy
2. **Cloud + Local (synced)** - Project has a local working directory that syncs
3. **Local-only** - Project exists locally (when cloud mode is disabled)

**Example:**

```bash
# You have 3 projects on cloud:
# - research: wants local sync at ~/Documents/research
# - work: wants local sync at ~/work-notes
# - temp: cloud-only, no local sync needed

bm project add research --local-path ~/Documents/research
bm project add work --local-path ~/work-notes
bm project add temp  # No local sync

# Now you can sync individually (after initial --resync):
bm project bisync --name research
bm project bisync --name work
# temp stays cloud-only
```

**What happens under the covers:**
- Config stores `cloud_projects` dict mapping project names to local paths
- Each project gets its own bisync state in `~/.basic-memory/bisync-state/{project}/`
- Rclone syncs using single remote: `basic-memory-cloud`
- Projects can live anywhere on your filesystem, not forced into sync directory

## Quick Start

### 1. Enable Cloud Mode

Authenticate and enable cloud mode:

```bash
bm cloud login
```

**What this does:**
1. Opens browser to Basic Memory Cloud authentication page
2. Stores authentication token in `~/.basic-memory/auth/token`
3. **Enables cloud mode** - all CLI commands now work against cloud
4. Validates your subscription status

**Result:** All `bm project`, `bm tools` commands now work with cloud.

### 2. Set Up Sync

Install rclone and configure credentials:

```bash
bm cloud setup
```

**What this does:**
1. Installs rclone automatically (if needed)
2. Fetches your tenant information from cloud
3. Generates scoped S3 credentials for sync
4. Configures single rclone remote: `basic-memory-cloud`

**Result:** You're ready to sync projects. No sync directories created yet - those come with project setup.

### 3. Add Projects with Sync

Create projects with optional local sync paths:

```bash
# Create cloud project without local sync
bm project add research

# Create cloud project WITH local sync
bm project add research --local-path ~/Documents/research

# Or configure sync for existing project
bm project sync-setup research ~/Documents/research
```

**What happens under the covers:**

When you add a project with `--local-path`:
1. Project created on cloud at `/app/data/research`
2. Local path stored in config: `cloud_projects.research.local_path = "~/Documents/research"`
3. Local directory created if it doesn't exist
4. Bisync state directory created at `~/.basic-memory/bisync-state/research/`

**Result:** Project is ready to sync, but no files synced yet.

### 4. Sync Your Project

Establish the initial sync baseline. **Best practice:** Always preview with `--dry-run` first:

```bash
# Step 1: Preview the initial sync (recommended)
bm project bisync --name research --resync --dry-run

# Step 2: If all looks good, run the actual sync
bm project bisync --name research --resync
```

**What happens under the covers:**
1. Rclone reads from `~/Documents/research` (local)
2. Connects to `basic-memory-cloud:bucket-name/app/data/research` (remote)
3. Creates bisync state files in `~/.basic-memory/bisync-state/research/`
4. Syncs files bidirectionally with settings:
   - `conflict_resolve=newer` (most recent wins)
   - `max_delete=25` (safety limit)
   - Respects `.bmignore` patterns

**Result:** Local and cloud are in sync. Baseline established.

**Why `--resync`?** This is an rclone requirement for the first bisync run. It establishes the initial state that future syncs will compare against. After the first sync, never use `--resync` unless you need to force a new baseline.

See: https://rclone.org/bisync/#resync
```
--resync
This will effectively make both Path1 and Path2 filesystems contain a matching superset of all files. By default, Path2 files that do not exist in Path1 will be copied to Path1, and the process will then copy the Path1 tree to Path2.
```

### 5. Subsequent Syncs

After the first sync, just run bisync without `--resync`:

```bash
bm project bisync --name research
```

**What happens:**
1. Rclone compares local and cloud states
2. Syncs changes in both directions
3. Auto-resolves conflicts (newer file wins)
4. Updates `last_sync` timestamp in config

**Result:** Changes flow both ways - edit locally or in cloud, both stay in sync.

### 6. Verify Setup

Check status:

```bash
bm cloud status
```

You should see:
- `Mode: Cloud (enabled)`
- `Cloud instance is healthy`
- Instructions for project sync commands

## Working with Projects

### Understanding Project Commands

**Key concept:** When cloud mode is enabled, use regular `bm project` commands (not `bm cloud project`).

```bash
# In cloud mode:
bm project list              # Lists cloud projects
bm project add research      # Creates cloud project

# In local mode:
bm project list              # Lists local projects
bm project add research ~/Documents/research  # Creates local project
```

### Creating Projects

**Use case 1: Cloud-only project (no local sync)**

```bash
bm project add temp-notes
```

**What this does:**
- Creates project on cloud at `/app/data/temp-notes`
- No local directory created
- No sync configuration

**Result:** Project exists on cloud, accessible via MCP tools, but no local copy.

**Use case 2: Cloud project with local sync**

```bash
bm project add research --local-path ~/Documents/research
```

**What this does:**
- Creates project on cloud at `/app/data/research`
- Creates local directory `~/Documents/research`
- Stores sync config in `~/.basic-memory/config.json`
- Prepares for bisync (but doesn't sync yet)

**Result:** Project ready to sync. Run `bm project bisync --name research --resync` to establish baseline.

**Use case 3: Add sync to existing cloud project**

```bash
# Project already exists on cloud
bm project sync-setup research ~/Documents/research
```

**What this does:**
- Updates existing project's sync configuration
- Creates local directory
- Prepares for bisync

**Result:** Existing cloud project now has local sync path. Run bisync to pull files down.

### Listing Projects

View all projects:

```bash
bm project list
```

**What you see:**
- All projects in cloud (when cloud mode enabled)
- Default project marked
- Project paths shown

**Future:** Will show sync status (synced/not synced, last sync time).

## File Synchronization

### Understanding the Sync Commands

**There are three sync-related commands:**

1. `bm project sync` - One-way: local → cloud (make cloud match local)
2. `bm project bisync` - Two-way: local ↔ cloud (recommended)
3. `bm project check` - Verify files match (no changes)

### One-Way Sync: Local → Cloud

**Use case:** You made changes locally and want to push to cloud (overwrite cloud).

```bash
bm project sync --name research
```

**What happens:**
1. Reads files from `~/Documents/research` (local)
2. Uses rclone sync to make cloud identical to local
3. Respects `.bmignore` patterns
4. Shows progress bar

**Result:** Cloud now matches local exactly. Any cloud-only changes are overwritten.

**When to use:**
- You know local is the source of truth
- You want to force cloud to match local
- You don't care about cloud changes

### Two-Way Sync: Local ↔ Cloud (Recommended)

**Use case:** You edit files both locally and in cloud UI, want both to stay in sync.

```bash
# First time - establish baseline
bm project bisync --name research --resync

# Subsequent syncs
bm project bisync --name research
```

**What happens:**
1. Compares local and cloud states using bisync metadata
2. Syncs changes in both directions
3. Auto-resolves conflicts (newer file wins)
4. Detects excessive deletes and fails safely (max 25 files)

**Conflict resolution example:**

```bash
# Edit locally
echo "Local change" > ~/Documents/research/notes.md

# Edit same file in cloud UI
# Cloud now has: "Cloud change"

# Run bisync
bm project bisync --name research

# Result: Newer file wins (based on modification time)
# If cloud was more recent, cloud version kept
# If local was more recent, local version kept
```

**When to use:**
- Default workflow for most users
- You edit in multiple places
- You want automatic conflict resolution

### Verify Sync Integrity

**Use case:** Check if local and cloud match without making changes.

```bash
bm project check --name research
```

**What happens:**
1. Compares file checksums between local and cloud
2. Reports differences
3. No files transferred

**Result:** Shows which files differ. Run bisync to sync them.

```bash
# One-way check (faster)
bm project check --name research --one-way
```

### Preview Changes (Dry Run)

**Use case:** See what would change without actually syncing.

```bash
bm project bisync --name research --dry-run
```

**What happens:**
1. Runs bisync logic
2. Shows what would be transferred/deleted
3. No actual changes made

**Result:** Safe preview of sync operations.

### Advanced: List Remote Files

**Use case:** See what files exist on cloud without syncing.

```bash
# List all files in project
bm project ls --name research

# List files in subdirectory
bm project ls --name research --path subfolder
```

**What happens:**
1. Connects to cloud via rclone
2. Lists files in remote project path
3. No files transferred

**Result:** See cloud file listing.

## Multiple Projects

### Syncing Multiple Projects

**Use case:** You have several projects with local sync, want to sync all at once.

```bash
# Setup multiple projects
bm project add research --local-path ~/Documents/research
bm project add work --local-path ~/work-notes
bm project add personal --local-path ~/personal

# Establish baselines
bm project bisync --name research --resync
bm project bisync --name work --resync
bm project bisync --name personal --resync

# Daily workflow: sync everything
bm project bisync --name research
bm project bisync --name work
bm project bisync --name personal
```

**Future:** `--all` flag will sync all configured projects:

```bash
bm project bisync --all  # Coming soon
```

### Mixed Usage

**Use case:** Some projects sync, some stay cloud-only.

```bash
# Projects with sync
bm project add research --local-path ~/Documents/research
bm project add work --local-path ~/work

# Cloud-only projects
bm project add archive
bm project add temp-notes

# Sync only the configured ones
bm project bisync --name research
bm project bisync --name work

# Archive and temp-notes stay cloud-only
```

**Result:** Fine-grained control over what syncs.

## Disable Cloud Mode

Return to local mode:

```bash
bm cloud logout
```

**What this does:**
1. Disables cloud mode in config
2. All commands now work locally
3. Auth token remains (can re-enable with login)

**Result:** All `bm` commands work with local projects again.

## Filter Configuration

### Understanding .bmignore

**The problem:** You don't want to sync everything (e.g., `.git`, `node_modules`, database files).

**The solution:** `.bmignore` file with gitignore-style patterns.

**Location:** `~/.basic-memory/.bmignore`

**Default patterns:**

```gitignore
# Version control
.git/**

# Python
__pycache__/**
*.pyc
.venv/**
venv/**

# Node.js
node_modules/**

# Basic Memory internals
memory.db/**
memory.db-shm/**
memory.db-wal/**
config.json/**
watch-status.json/**
.bmignore.rclone/**

# OS files
.DS_Store/**
Thumbs.db/**

# Environment files
.env/**
.env.local/**
```

**How it works:**
1. On first sync, `.bmignore` created with defaults
2. Patterns converted to rclone filter format (`.bmignore.rclone`)
3. Rclone uses filters during sync
4. Same patterns used by all projects

**Customizing:**

```bash
# Edit patterns
code ~/.basic-memory/.bmignore

# Add custom patterns
echo "*.tmp/**" >> ~/.basic-memory/.bmignore

# Next sync uses updated patterns
bm project bisync --name research
```

## Troubleshooting

### Authentication Issues

**Problem:** "Authentication failed" or "Invalid token"

**Solution:** Re-authenticate:

```bash
bm cloud logout
bm cloud login
```

### Subscription Issues

**Problem:** "Subscription Required" error

**Solution:**
1. Visit subscribe URL shown in error
2. Sign up for subscription
3. Run `bm cloud login` again

**Note:** Access is immediate when subscription becomes active.

### Bisync Initialization

**Problem:** "First bisync requires --resync"

**Explanation:** Bisync needs a baseline state before it can sync changes.

**Solution:**

```bash
bm project bisync --name research --resync
```

**What this does:**
- Establishes initial sync state
- Creates baseline in `~/.basic-memory/bisync-state/research/`
- Syncs all files bidirectionally

**Result:** Future syncs work without `--resync`.

### Empty Directory Issues

**Problem:** "Empty prior Path1 listing. Cannot sync to an empty directory"

**Explanation:** Rclone bisync doesn't work well with completely empty directories. It needs at least one file to establish a baseline.

**Solution:** Add at least one file before running `--resync`:

```bash
# Create a placeholder file
echo "# Research Notes" > ~/Documents/research/README.md

# Now run bisync
bm project bisync --name research --resync
```

**Why this happens:** Bisync creates listing files that track the state of each side. When both directories are completely empty, these listing files are considered invalid by rclone.

**Best practice:** Always have at least one file (like a README.md) in your project directory before setting up sync.

### Bisync State Corruption

**Problem:** Bisync fails with errors about corrupted state or listing files

**Explanation:** Sometimes bisync state can become inconsistent (e.g., after mixing dry-run and actual runs, or after manual file operations).

**Solution:** Clear bisync state and re-establish baseline:

```bash
# Clear bisync state
bm project bisync-reset research

# Re-establish baseline
bm project bisync --name research --resync
```

**What this does:**
- Removes all bisync metadata from `~/.basic-memory/bisync-state/research/`
- Forces fresh baseline on next `--resync`
- Safe operation (doesn't touch your files)

**Note:** This command also runs automatically when you remove a project to clean up state directories.

### Too Many Deletes

**Problem:** "Error: max delete limit (25) exceeded"

**Explanation:** Bisync detected you're about to delete more than 25 files. This is a safety check to prevent accidents.

**Solution 1:** Review what you're deleting, then force resync:

```bash
# Check what would be deleted
bm project bisync --name research --dry-run

# If correct, establish new baseline
bm project bisync --name research --resync
```

**Solution 2:** Use one-way sync if you know local is correct:

```bash
bm project sync --name research
```

### Project Not Configured for Sync

**Problem:** "Project research has no local_sync_path configured"

**Explanation:** Project exists on cloud but has no local sync path.

**Solution:**

```bash
bm project sync-setup research ~/Documents/research
bm project bisync --name research --resync
```

### Connection Issues

**Problem:** "Cannot connect to cloud instance"

**Solution:** Check status:

```bash
bm cloud status
```

If instance is down, wait a few minutes and retry.

## Security

- **Authentication**: OAuth 2.1 with PKCE flow
- **Tokens**: Stored securely in `~/.basic-memory/basic-memory-cloud.json`
- **Transport**: All data encrypted in transit (HTTPS)
- **Credentials**: Scoped S3 credentials (read-write to your tenant only)
- **Isolation**: Your data isolated from other tenants
- **Ignore patterns**: Sensitive files automatically excluded via `.bmignore`

## Command Reference

### Cloud Mode Management

```bash
bm cloud login              # Authenticate and enable cloud mode
bm cloud logout             # Disable cloud mode
bm cloud status             # Check cloud mode and instance health
```

### Setup

```bash
bm cloud setup              # Install rclone and configure credentials
```

### Project Management

When cloud mode is enabled:

```bash
bm project list                           # List cloud projects
bm project add <name>                     # Create cloud project (no sync)
bm project add <name> --local-path <path> # Create with local sync
bm project sync-setup <name> <path>       # Add sync to existing project
bm project rm <name>                      # Delete project
```

### File Synchronization

```bash
# One-way sync (local → cloud)
bm project sync --name <project>
bm project sync --name <project> --dry-run
bm project sync --name <project> --verbose

# Two-way sync (local ↔ cloud) - Recommended
bm project bisync --name <project>          # After first --resync
bm project bisync --name <project> --resync # First time / force baseline
bm project bisync --name <project> --dry-run
bm project bisync --name <project> --verbose

# Integrity check
bm project check --name <project>
bm project check --name <project> --one-way

# List remote files
bm project ls --name <project>
bm project ls --name <project> --path <subpath>
```

## Summary

**Basic Memory Cloud uses project-scoped sync:**

1. **Enable cloud mode** - `bm cloud login`
2. **Install rclone** - `bm cloud setup`
3. **Add projects with sync** - `bm project add research --local-path ~/Documents/research`
4. **Preview first sync** - `bm project bisync --name research --resync --dry-run`
5. **Establish baseline** - `bm project bisync --name research --resync`
6. **Daily workflow** - `bm project bisync --name research`

**Key benefits:**
- ✅ Each project independently syncs (or doesn't)
- ✅ Projects can live anywhere on disk
- ✅ Explicit sync operations (no magic)
- ✅ Safe by design (max delete limits, conflict resolution)
- ✅ Full offline access (work locally, sync when ready)

**Future enhancements:**
- `--all` flag to sync all configured projects
- Project list showing sync status
- Watch mode for automatic sync
