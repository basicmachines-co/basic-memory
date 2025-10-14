# Basic Memory Cloud CLI Guide

The Basic Memory Cloud CLI provides seamless integration between local and cloud knowledge bases using a **cloud mode toggle**. When cloud mode is enabled, all your regular `bm` commands work transparently with the cloud instead of locally.

## Overview

The cloud CLI enables you to:
- **Toggle cloud mode** with `bm cloud login` / `bm cloud logout`
- **Use regular commands in cloud mode**: `bm project`, `bm sync`, `bm tool` all work with cloud
- **Upload local files** directly to cloud projects via `bm cloud upload`
- **Bidirectional sync** with rclone bisync (recommended for most users)
- **Direct file access** via rclone mount (alternative workflow)
- **Integrity verification** with `bm cloud check`
- **Automatic project creation** from local directories

## Prerequisites

Before using Basic Memory Cloud, you need:

- **Active Subscription**: An active Basic Memory Cloud subscription is required to access cloud features
- **Subscribe**: Visit [https://basicmemory.com/subscribe](https://basicmemory.com/subscribe) to sign up

If you attempt to log in without an active subscription, you'll receive a "Subscription Required" error with a link to subscribe.

## The Cloud Mode Paradigm

Basic Memory Cloud follows the **Dropbox/iCloud model** - a single cloud space containing all your projects, not per-project connections.

**How it works:**
- One login per machine: `bm cloud login`
- One sync directory: `~/basic-memory-cloud-sync/` (all projects)
- Projects are folders within your cloud space
- All regular commands work in cloud mode

**Why this model:**
- ✅ Single set of credentials (not N per project)
- ✅ One rclone process (not N processes)
- ✅ Familiar pattern (like Dropbox)
- ✅ Simple operations (setup once, sync anytime)
- ✅ Natural scaling (add projects = add folders)

## Quick Start

### 1. Enable Cloud Mode

Authenticate and enable cloud mode for all commands:

```bash
bm cloud login
```

This command will:
1. Open your browser to the Basic Memory Cloud authentication page
2. Prompt you to authorize the CLI application
3. Store your authentication token locally
4. **Enable cloud mode** - all CLI commands now work against cloud

### 2. Set Up Sync

Set up bidirectional file synchronization:

```bash
bm cloud setup
```

This will:
1. Install rclone automatically (if needed)
2. Configure sync credentials
3. Create `~/basic-memory-cloud-sync/` directory
4. Establish initial sync baseline

**Alternative:** Use `bm cloud setup --mount` to set up mount instead of sync.

### 3. Verify Setup

Check that everything is working:

```bash
bm cloud status
```

You should see:
- `Mode: Cloud (enabled)`
- `Cloud instance is healthy`
- Bisync status showing `✓ Initialized`

### 4. Start Using Cloud

Now all your regular commands work with the cloud:

```bash
# List cloud projects
bm project list

# Create cloud project
bm project add "my-research"

# Use MCP tools on cloud
bm tool write-note --title "Hello" --folder "my-research" --content "Test"

# Sync with cloud
bm sync

# Watch mode for continuous sync
bm sync --watch
```

### 5. Disable Cloud Mode

Return to local mode:

```bash
bm cloud logout
```

All commands now work locally again.

## Working with Cloud Projects

**Important:** When cloud mode is enabled, use regular `bm project` commands (not `bm cloud project`).

### Listing Projects

View all projects (cloud projects when cloud mode is enabled):

```bash
# In cloud mode - lists cloud projects
bm project list

# In local mode - lists local projects
bm project list
```

### Creating Projects

Create a new project (creates on cloud when cloud mode is enabled):

```bash
# In cloud mode - creates cloud project
bm project add my-new-project

# Create and set as default
bm project add my-new-project --default
```

### Automatic Project Creation

**New in SPEC-9:** Projects are automatically created when you create local directories!

```bash
# Create a local directory in your sync folder
mkdir ~/basic-memory-cloud-sync/new-project
echo "# Notes" > ~/basic-memory-cloud-sync/new-project/readme.md

# Sync - automatically creates cloud project
bm sync

# Verify - project now exists on cloud
bm project list
```

This Dropbox-like workflow means you don't need to manually coordinate projects between local and cloud.

### Uploading Local Files

You can directly upload local files or directories to cloud projects using `bm cloud upload`. This is useful for:
- Migrating existing local projects to the cloud
- Quickly uploading specific files or directories
- One-time bulk uploads without setting up sync

**Basic Usage:**

```bash
# Upload a directory to existing project
bm cloud upload ~/my-notes --project research

# Upload a single file
bm cloud upload important-doc.md --project research
```

**Create Project On-the-Fly:**

If the target project doesn't exist yet, use `--create-project`:

```bash
# Upload and create project in one step
bm cloud upload ~/local-project --project new-research --create-project
```

**Skip Automatic Sync:**

By default, the command syncs the project after upload to index the files. To skip this:

```bash
# Upload without triggering sync
bm cloud upload ~/bulk-data --project archives --no-sync
```

**File Filtering:**

The upload command respects `.bmignore` and `.gitignore` patterns, automatically excluding:
- Hidden files (`.git`, `.DS_Store`)
- Build artifacts (`node_modules`, `__pycache__`)
- Database files (`*.db`, `*.db-wal`)
- Environment files (`.env`)

To customize what gets uploaded, edit `~/.basic-memory/.bmignore`.

**Complete Example:**

```bash
# 1. Login to cloud
bm cloud login

# 2. Upload local project (creates project if needed)
bm cloud upload ~/Documents/research-notes --project research --create-project

# 3. Verify upload
bm project list
```

**Notes:**
- Files are uploaded directly via WebDAV (no sync setup required)
- Uploads are immediate and don't require bisync or mount
- Use this for migration or one-time uploads; use `bm sync` for ongoing synchronization

## File Synchronization

### The `bm sync` Command (Cloud Mode Aware)

The `bm sync` command automatically adapts based on cloud mode:

**In local mode:**
```bash
bm sync              # Indexes local files into database
```

**In cloud mode:**
```bash
bm sync              # Runs bisync + indexes files
bm sync --watch      # Continuous sync every 60 seconds
bm sync --interval 30  # Custom interval
```

The same command works everywhere - no need to remember different commands for local vs cloud!

## Bidirectional Sync (bisync) - Recommended

Bidirectional sync is the **recommended approach** for most users. It provides:
- ✅ Offline access to all files
- ✅ Automatic bidirectional synchronization
- ✅ Conflict detection and resolution
- ✅ Works with any editor or tool
- ✅ Background watch mode

### Setup

Set up bisync (runs automatically if you used `bm cloud setup`):

```bash
bm cloud setup
```

Or set up with custom directory:

```bash
bm cloud setup --dir ~/my-sync-folder
```

### Running Sync

Use the cloud-aware `bm sync` command:

```bash
# Manual sync
bm sync

# Watch mode (continuous sync)
bm sync --watch

# Custom interval (30 seconds)
bm sync --watch --interval 30
```

### Bisync Profiles

Bisync supports three conflict resolution strategies with different safety levels:

| Profile | Conflict Resolution | Max Deletes | Use Case |
|---------|-------------------|-------------|----------|
| **balanced** | newer | 25 | Default, recommended for most users |
| **safe** | none | 10 | Keep both versions on conflict |
| **fast** | newer | 50 | Rapid iteration, higher delete tolerance |

**Profile Details:**

- **safe**:
  - Conflict resolution: `none` (creates `.conflict` files for both versions)
  - Max delete: 10 files per sync
  - Best for: Critical data where you want manual conflict resolution

- **balanced** (default):
  - Conflict resolution: `newer` (auto-resolve to most recent file)
  - Max delete: 25 files per sync
  - Best for: General use with automatic conflict handling

- **fast**:
  - Conflict resolution: `newer` (auto-resolve to most recent file)
  - Max delete: 50 files per sync
  - Best for: Rapid development iteration with less restrictive safety checks

**How to Select a Profile:**

The default profile (`balanced`) is used automatically with `bm sync`:

```bash
# Uses balanced profile (default)
bm sync
```

For advanced control, use `bm cloud bisync` with the `--profile` flag:

```bash
# Use safe mode
bm cloud bisync --profile safe

# Use fast mode
bm cloud bisync --profile fast

# Preview changes with specific profile
bm cloud bisync --profile safe --dry-run
```

**Check Available Profiles:**

```bash
bm cloud status
```

This shows all available profiles with their settings.

**Current Limitations:**

- Profiles are hardcoded and cannot be customized
- No config file option to change default profile
- Profile settings (max_delete, conflict_resolve) cannot be modified without code changes
- Profile selection only available via `bm cloud bisync --profile` (advanced command)

### Establishing New Baseline

If you need to force a complete resync:

```bash
bm cloud bisync --resync
```

**Warning:** This overwrites the sync state. Use only when recovering from errors.

### Checking Sync Status

View current sync status:

```bash
bm cloud status
```

This shows:
- Cloud mode status
- Instance health
- Sync directory location
- Last sync time
- Available bisync profiles

### Verifying Sync Integrity

Check that local and cloud files match:

```bash
# Full integrity check
bm cloud check

# Faster one-way check
bm cloud check --one-way
```

This uses `rclone check` to verify files match without transferring data.

### Working with Bisync

Create and edit files in `~/basic-memory-cloud-sync/`:

```bash
# Create a new note
echo "# My Research" > ~/basic-memory-cloud-sync/my-project/notes.md

# Edit with your favorite editor
code ~/basic-memory-cloud-sync/my-project/

# Sync changes to cloud
bm sync
```

In watch mode, changes sync automatically:

```bash
# Start watch mode
bm sync --watch

# Edit files - they sync automatically every 60 seconds
code ~/basic-memory-cloud-sync/my-project/
```

### Filter Configuration

Bisync uses `.bmignore` patterns from `~/.basic-memory/.bmignore`:

```bash
# View current ignore patterns
cat ~/.basic-memory/.bmignore

# Edit ignore patterns
code ~/.basic-memory/.bmignore
```

Example `.bmignore`:

```gitignore
# This file is used by 'bm cloud bisync' and file sync
# Patterns use standard gitignore-style syntax

# Hidden files (files starting with dot)
- .*

# Basic Memory internal files
- memory.db/**
- memory.db-shm/**
- memory.db-wal/**
- config.json/**

# Version control
- .git/**

# Python
- __pycache__/**
- *.pyc
- .venv/**

# Node.js
- node_modules/**
```

**Key points:**
- ✅ **Global configuration** - One ignore file for all projects
- ✅ **rclone filter syntax** - Patterns with `- ` prefix
- ✅ **Automatic creation** - Created with defaults on first use
- ✅ **Shared patterns** - Same patterns used by sync service

## NFS Mount (Direct Access) - Alternative

NFS mount provides direct file system access as an alternative to bisync. Use this if you prefer mounting files like a network drive.

### Setup

Set up mount instead of bisync:

```bash
bm cloud setup --mount
```

### Mounting Files

Mount your cloud files:

```bash
# Mount with default settings
bm cloud mount

# Mount with specific profile
bm cloud mount --profile fast
```

#### Mount Profiles

- **balanced** (default): Balanced caching for general use
- **streaming**: Optimized for large files
- **fast**: Minimal verification for rapid access

### Checking Mount Status

View current mount status:

```bash
bm cloud status --mount
```

### Unmounting Files

Unmount when done:

```bash
bm cloud unmount
```

### Working with Mounted Files

Once mounted, files appear at `~/basic-memory-cloud/`:

```bash
# List cloud files
ls ~/basic-memory-cloud/

# Edit with your favorite editor
code ~/basic-memory-cloud/my-project/

# Changes are immediately synced to cloud
echo "# Notes" > ~/basic-memory-cloud/my-project/readme.md
```

**Note:** Changes are written through to cloud immediately. There's no "sync" step needed.

## Instance Management

### Health Check

Check if your cloud instance is healthy:

```bash
bm cloud status
```

This shows:
- Cloud mode enabled/disabled
- Instance health status
- Instance version
- Sync or mount status

## Troubleshooting

### Authentication Issues

**Problem**: "Authentication failed" or "Invalid token"

**Solution**: Re-authenticate:

```bash
bm cloud logout
bm cloud login
```

### Subscription Issues

**Problem**: "Subscription Required" error when logging in

**Solution**: You need an active Basic Memory Cloud subscription to use cloud features.

1. Visit the subscribe URL shown in the error message
2. Sign up for a subscription
3. Once your subscription is active, run `bm cloud login` again

**Problem**: "Subscription Required" error for existing user

**Solution**: Your subscription may have expired or been cancelled.

1. Check your subscription status at [https://basicmemory.com/account](https://basicmemory.com/account)
2. Renew your subscription if needed
3. Run `bm cloud login` again

Note: Access is immediately restored when your subscription becomes active.

### Sync Issues

**Problem**: "Bisync not initialized"

**Solution**: Run setup or initialize with resync:

```bash
bm cloud setup
# or
bm cloud bisync --resync
```

**Problem**: "Too many deletes" error

**Solution**: Bisync detected many deletions (safety check). Review changes and use a higher delete limit profile or force resync:

```bash
bm cloud bisync --profile fast  # Higher delete limit
# or
bm cloud bisync --resync        # Force baseline
```

**Problem**: Conflicts detected

**Solution**: Bisync found files changed in both locations. Check sync directory for `.conflict` files:

```bash
ls ~/basic-memory-cloud-sync/**/*.conflict
```

Resolve conflicts manually, then sync again.

### Connection Issues

**Problem**: "Cannot connect to cloud instance"

**Solution**: Check cloud status:

```bash
bm cloud status
```

If instance is down, wait a few minutes and retry. If problem persists, contact support.

### Mount Issues

**Problem**: "Mount point is busy"

**Solution**: Unmount and remount:

```bash
bm cloud unmount
bm cloud mount
```

**Problem**: "Permission denied" when accessing mounted files

**Solution**: Check mount status and remount:

```bash
bm cloud status --mount
bm cloud unmount
bm cloud mount
```

## Security

- **Authentication**: OAuth 2.1 with PKCE flow
- **Tokens**: Stored securely in `~/.basic-memory/auth/token`
- **Transport**: All data encrypted in transit (HTTPS)
- **Credentials**: Scoped S3 credentials for sync/mount (read-write access to your tenant only)
- **Isolation**: Your data is isolated from other tenants
- **Ignore patterns**: Sensitive files (`.env`, credentials) automatically excluded

## Command Reference

### Cloud Mode Management

```bash
bm cloud login                   # Authenticate and enable cloud mode
bm cloud logout                  # Disable cloud mode
bm cloud status                  # Check cloud mode and sync status
bm cloud status --mount          # Check cloud mode and mount status
```

### Setup

```bash
bm cloud setup                   # Setup bisync (default, recommended)
bm cloud setup --mount           # Setup mount (alternative)
bm cloud setup --dir ~/sync      # Custom sync directory
```

### Project Management (Cloud Mode Aware)

When cloud mode is enabled, these commands work with cloud:

```bash
bm project list                  # List projects
bm project add <name>            # Create project
bm project add <name> --default  # Create and set as default
bm project rm <name>             # Delete project
bm project set-default <name>    # Set default project
```

### File Synchronization

```bash
bm sync                          # Sync files (local or cloud depending on mode)
bm sync --watch                  # Continuous sync (cloud mode only)
bm sync --interval 30            # Custom interval for watch mode

# Advanced bisync commands
bm cloud bisync                  # Run bisync manually
bm cloud bisync --profile safe   # Use specific profile
bm cloud bisync --dry-run        # Preview changes
bm cloud bisync --resync         # Force new baseline
bm cloud bisync --watch          # Continuous sync
bm cloud bisync --verbose        # Show detailed output

# Integrity verification
bm cloud check                   # Full integrity check
bm cloud check --one-way         # Faster one-way check
```

### File Upload

```bash
# Upload files/directories to cloud projects
bm cloud upload <path> --project <name>              # Upload to existing project
bm cloud upload <path> -p <name> --create-project    # Upload and create project
bm cloud upload <path> -p <name> --no-sync           # Upload without syncing
```

### Direct File Access (Mount)

```bash
bm cloud mount                   # Mount cloud files
bm cloud mount --profile fast    # Use specific profile
bm cloud unmount                 # Unmount files
```

## Summary

Basic Memory Cloud provides two workflows:

### Recommended: Bidirectional Sync (bisync)
1. `bm cloud login` - Authenticate once
2. `bm cloud setup` - Configure sync once
3. `bm sync` - Sync anytime (or use `--watch`)
4. Work in `~/basic-memory-cloud-sync/`
5. Changes sync bidirectionally

### Alternative: Direct Mount
1. `bm cloud login` - Authenticate once
2. `bm cloud setup --mount` - Configure mount once
3. `bm cloud mount` - Mount when needed
4. Work in `~/basic-memory-cloud/`
5. Changes write through immediately

Both approaches work seamlessly with cloud mode - all your regular `bm` commands work with either workflow!
