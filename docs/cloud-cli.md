# Basic Memory Cloud CLI Guide

The Basic Memory Cloud CLI provides seamless integration between local and cloud knowledge bases using **project-scoped synchronization**. Each project can optionally sync with the cloud, giving you fine-grained control over what syncs and where.

## Overview

The cloud CLI enables you to:
- **Authenticate cloud access** - OAuth/API key credentials are stored locally for cloud operations
- **Project-scoped sync** - Each project independently manages its sync configuration
- **Explicit operations** - Sync only what you want, when you want
- **Bidirectional sync** - Keep local and cloud in sync with rclone bisync
- **Offline access** - Work locally, sync when ready

## Prerequisites

Before using Basic Memory Cloud, you need:

- **Active Subscription**: An active Basic Memory Cloud subscription is required to access cloud features
- **Subscribe**: Visit [https://basicmemory.com/subscribe](https://basicmemory.com/subscribe) to sign up
- **Optional**: Cloud is optional. Local-first open-source usage continues without cloud.
- **OSS Discount**: Use code `{{OSS_DISCOUNT_CODE}}` for 20% off for 3 months.

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
3. **Local-only** - Project exists locally and is not routed to cloud

**Example:**

```bash
# You have 3 projects on cloud:
# - research: wants local sync at ~/Documents/research
# - work: wants local sync at ~/work-notes
# - temp: cloud-only, no local sync needed

bm project add research --cloud --local-path ~/Documents/research
bm project add work --cloud --local-path ~/work-notes
bm project add temp --cloud  # No local sync

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

### 1. Authenticate Cloud Access

Authenticate with cloud:

```bash
bm cloud login
```

**What this does:**
1. Opens browser to Basic Memory Cloud authentication page
2. Stores authentication tokens in `~/.basic-memory/basic-memory-cloud.json`
3. Validates your subscription status
4. Leaves routing behavior unchanged (auth only)

**Result:** Cloud credentials are available for cloud-routed commands.
Apply OSS discount code `{{OSS_DISCOUNT_CODE}}` during checkout to receive 20% off for 3 months.

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
bm project add research --cloud

# Create cloud project WITH local sync
bm project add research --cloud --local-path ~/Documents/research

# Or configure sync for existing project
bm project sync-setup research ~/Documents/research
```

**What happens under the covers:**

When you add a project with `--local-path`:
1. Project created on cloud at `/app/data/research`
2. Local path stored in config for that project (`local_sync_path`)
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
- `OAuth: token valid` (or missing/expired)
- `API Key: configured` (or not set)
- `Cloud instance is healthy`
- Instructions for project sync commands

## Working with Projects

### Understanding Project Commands

**Key concept:** Use regular `bm project` commands (not `bm cloud project`).

```bash
# Local route
bm project list --local
bm project add research ~/Documents/research

# Cloud route
bm project list --cloud
bm project add research --cloud
```

### Creating Projects

**Use case 1: Cloud-only project (no local sync)**

```bash
bm project add temp-notes --cloud
```

**What this does:**
- Creates project on cloud at `/app/data/temp-notes`
- No local directory created
- No sync configuration

**Result:** Project exists on cloud, accessible via MCP tools, but no local copy.

**Use case 2: Cloud project with local sync**

```bash
bm project add research --cloud --local-path ~/Documents/research
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
- Local projects always
- Cloud projects when credentials are available
- Default project marked
- Route-related metadata (for example, local/cloud presence and sync info)

Example shape (single row for dual-presence projects):

```text
Name   Path            Local Path           Cloud Path   CLI Default   MCP (stdio)
main   /basic-memory   ~/basic-memory       /basic-memory   local       local
specs  /specs          ~/dev/specs          /specs          cloud       local
```

### When a Project Exists in Both Local and Cloud

Use routing flags to disambiguate command targets:

```bash
# Force local target for this command
bm project info main --local
bm project ls --name main --local

# Force cloud target for this command
bm project info main --cloud
bm project ls --name main --cloud
```

Default behavior for no-project, no-flag commands is local.
For MCP stdio, routing is always local.

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

### Advanced: List Project Files by Route

**Use case:** Inspect local or cloud project files explicitly.

```bash
# List local project files (default target when no route flag is given)
bm project ls --name research
bm project ls --name research --local

# List cloud project files
bm project ls --name research --cloud

# List files in subdirectory
bm project ls --name research --cloud --path subfolder
```

**What happens:**
1. Resolves route from flags (or local default when no route is given)
2. Lists files for the chosen project instance
3. No files transferred

**Result:** See file listing for the target route.

## Multiple Projects

### Syncing Multiple Projects

**Use case:** You have several projects with local sync, want to sync all at once.

```bash
# Setup multiple projects
bm project add research --cloud --local-path ~/Documents/research
bm project add work --cloud --local-path ~/work-notes
bm project add personal --cloud --local-path ~/personal

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
bm project add research --cloud --local-path ~/Documents/research
bm project add work --cloud --local-path ~/work

# Cloud-only projects
bm project add archive --cloud
bm project add temp-notes --cloud

# Sync only the configured ones
bm project bisync --name research
bm project bisync --name work

# Archive and temp-notes stay cloud-only
```

**Result:** Fine-grained control over what syncs.

## Per-Project Cloud Routing (API Key)

Route individual projects through cloud using an API key. This lets you keep some projects local while others route through cloud.

### Setting Up API Key Auth

**Option A: Create a key in the web app, then save it locally:**

```bash
bm cloud set-key bmc_abc123...
```

**Option B: Create a key via CLI (requires OAuth login first):**

```bash
bm cloud login                     # One-time OAuth login
bm cloud create-key "my-laptop"    # Creates key and saves it locally
```

The API key is account-level — it grants access to all your cloud projects. It's stored in `~/.basic-memory/config.json` as `cloud_api_key`.

### Setting Project Modes

```bash
# Route a project through cloud
bm project set-cloud research

# Revert to local mode
bm project set-local research

# View project modes
bm project list
```

**What happens:**
- `set-cloud`: validates the API key exists, then sets the project mode to `cloud` in config
- `set-local`: reverts the project to local mode (removes the mode entry from config)
- MCP tools and CLI commands for that project will route to `cloud_host/proxy` with the API key as Bearer token

### How It Works

When an MCP tool or CLI command runs for a cloud-mode project:

1. `get_client(project_name="research")` checks the project's mode in config
2. If mode is `cloud`, creates an HTTP client pointed at `cloud_host/proxy` with `Authorization: Bearer bmc_...`
3. If mode is `local` (default), uses the in-process ASGI transport as usual

**Routing priority** (highest to lowest):
1. Factory injection (cloud app, tests)
2. Explicit route override (`--local` / `--cloud`)
3. Per-project cloud mode (API key)
4. Local ASGI transport (default)

Route override environment variables:
- `BASIC_MEMORY_FORCE_LOCAL=true`
- `BASIC_MEMORY_FORCE_CLOUD=true`
- `BASIC_MEMORY_EXPLICIT_ROUTING=true`

No-project, no-flag CLI commands default to local routing.

### Configuration Example

```json
{
  "projects": {
    "personal": "/Users/me/notes",
    "research": "/Users/me/research"
  },
  "project_modes": {
    "research": "cloud"
  },
  "cloud_api_key": "bmc_abc123...",
  "cloud_host": "https://cloud.basicmemory.com",
  "default_project": "personal"
}
```

In this example, `personal` stays local and `research` routes through cloud. Projects not listed in `project_modes` default to local.

### Sync Behavior

Cloud-mode projects are automatically skipped during local file sync (background sync and file watching). Their files live on the cloud instance, not locally.

## OAuth Logout

```bash
bm cloud logout
```

**What this does:**
1. Removes stored OAuth token(s)
2. Does not change per-project route configuration
3. Does not change command routing defaults

**Result:** OAuth session is cleared. API-key-based routing still works if `cloud_api_key` is configured.

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

### Cloud Authentication

```bash
bm cloud login              # Authenticate and store OAuth credentials
bm cloud logout             # Remove stored OAuth credentials
bm cloud status             # Check auth state and instance health
bm cloud promo --off        # Disable CLI cloud promo notices
```

### API Key Management

```bash
bm cloud set-key <key>      # Save a cloud API key (bmc_ prefixed)
bm cloud create-key <name>  # Create API key via cloud API (requires OAuth login)
```

### Setup

```bash
bm cloud setup              # Install rclone and configure credentials
```

### Project Management

```bash
bm project list --local                   # Local project list
bm project list --cloud                   # Cloud project list
bm project add <name> --cloud             # Create cloud project (no sync)
bm project add <name> --cloud --local-path <path> # Create with local sync
bm project sync-setup <name> <path>       # Add sync to existing project
bm project rm <name>                      # Delete project
```

### Per-Project Routing

```bash
bm project set-cloud <name>  # Route project through cloud (requires API key)
bm project set-local <name>  # Revert project to local mode
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

# List project files by route
bm project ls --name <project>          # Default target: local
bm project ls --name <project> --local
bm project ls --name <project> --cloud
bm project ls --name <project> --cloud --path <subpath>
```

## Summary

**Basic Memory Cloud uses project-scoped sync:**

1. **Authenticate cloud access** - `bm cloud login`
2. **Install rclone** - `bm cloud setup`
3. **Add projects with sync** - `bm project add research --cloud --local-path ~/Documents/research`
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
