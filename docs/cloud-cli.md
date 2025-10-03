# Basic Memory Cloud CLI Guide

The Basic Memory Cloud CLI provides seamless integration between local and cloud knowledge bases using a **cloud mode toggle**. When cloud mode is enabled, all your regular `bm` commands work transparently with the cloud instead of locally.

## Overview

The cloud CLI enables you to:
- **Toggle cloud mode** with `bm cloud login` / `bm cloud logout`
- **Use regular commands in cloud mode**: `bm project`, `bm sync`, `bm tool` all work with cloud
- **Bidirectional sync** with rclone bisync (recommended for most users)
- **Direct file access** via NFS mount (advanced users)
- **Integrity verification** with `bm cloud check`
- **Automatic project creation** from local directories

## The Cloud Mode Paradigm

Basic Memory Cloud follows the **Dropbox/iCloud model** - a single cloud space containing all your projects, not per-project connections.

**How it works:**
- One login per machine: `bm cloud login`
- One mount point: `~/basic-memory-cloud/` (all projects)
- One sync directory: `~/basic-memory-cloud-sync/` (all projects)
- Projects are folders within your cloud space
- All regular commands work in cloud mode

**Why this model:**
- ✅ Single set of credentials (not N per project)
- ✅ One rclone process (not N processes)
- ✅ Familiar pattern (like Dropbox)
- ✅ Simple operations (mount once, sync once)
- ✅ Natural scaling (add projects = add folders)

## Quick Start

### Enable Cloud Mode

Authenticate and enable cloud mode for all commands:

```bash
bm cloud login
```

This command will:
1. Open your browser to the Basic Memory Cloud authentication page
2. Prompt you to authorize the CLI application
3. Store your authentication token locally
4. **Enable cloud mode** - all CLI commands now work against cloud

### Verify Cloud Mode

Check that cloud mode is active:

```bash
bm cloud status
```

You should see: `Mode: Cloud (enabled)`

### Use Regular Commands

Now all your regular commands work with the cloud:

```bash
# List cloud projects (not local)
bm project list

# Create cloud project
bm project add "my-research"

# Use MCP tools on cloud
bm tool write-note --title "Hello" --folder "my-research" --content "Test"

# Sync with cloud
bm sync
```

### Disable Cloud Mode

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

Example output:
```
     Projects
┏━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Name             ┃ Path                     ┃
┡━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ my-research      │ /app/data/my-research    │
│ work-notes       │ /app/data/work-notes     │
└──────────────────┴──────────────────────────┘

Found 2 project(s)
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

## File Upload

### Basic Upload

Upload files or directories to a cloud project:

```bash
# Upload a directory
bm cloud upload my-project /path/to/local/files

# Upload a single file
bm cloud upload my-project /path/to/file.md
```

### Upload Options

#### Timestamp Preservation

By default, file modification times are preserved during upload:

```bash
# Preserve timestamps (default)
bm cloud upload my-project ./docs

# Don't preserve timestamps
bm cloud upload my-project ./docs --no-preserve-timestamps
```

#### Gitignore Filtering

The CLI automatically respects `.gitignore` patterns and includes smart defaults for development artifacts:

```bash
# Respect .gitignore and defaults (default behavior)
bm cloud upload my-project ./my-repo

# Upload everything, ignore .gitignore
bm cloud upload my-project ./my-repo --no-gitignore
```

**Default ignore patterns include:**
- `.git`, `.venv`, `venv`, `env`, `.env`
- `node_modules`, `__pycache__`, `.pytest_cache`
- `*.pyc`, `*.pyo`, `*.pyd`
- `.DS_Store`, `Thumbs.db`
- `.idea`, `.vscode`
- `build`, `dist`, `.tox`, `.cache`
- `.mypy_cache`, `.ruff_cache`

### Upload Examples

```bash
# Upload a local knowledge base
bm cloud upload my-research ~/Documents/research

# Upload specific documentation, preserving structure
bm cloud upload docs-project ./docs

# Upload without gitignore filtering for a complete backup
bm cloud upload backup-project ./ --no-gitignore
```

### Upload Output

During upload, you'll see progress and filtering information:

```bash
$ bm cloud upload my-project ./my-repo

Ignored 45 file(s) based on .gitignore and default patterns
Uploading 23 file(s) to project 'my-project' on https://cloud.basicmemory.com...
  ✓ README.md
  ✓ src/main.py
  ✓ src/utils.py
  ✓ docs/guide.md
  ...
Successfully uploaded 23 file(s)!
```

## Local File Access

Basic Memory Cloud provides two approaches for working with your cloud files locally:

1. **Bidirectional Sync (bisync)** - Recommended for most users, especially Obsidian
2. **NFS Mount** - Direct file access for advanced users

### Choosing Your Approach

| Use Case | Recommended Solution | Why |
|----------|---------------------|-----|
| **Obsidian users** | `bisync` | File watcher support for live preview |
| **CLI/vim/emacs users** | `mount` | Direct file access, lower latency |
| **Offline work** | `bisync` | Can work offline, sync when connected |
| **Real-time collaboration** | `mount` | Immediate visibility of changes |
| **Multiple machines** | `bisync` | Better conflict handling |
| **Single machine** | `mount` | Simpler, more transparent |
| **Development work** | Either | Both work well, user preference |
| **Large files** | `mount` | Streaming access vs full download |

## File Synchronization

### The `bm sync` Command (Cloud Mode Aware)

**New in SPEC-9:** The `bm sync` command adapts to cloud mode automatically!

```bash
# In local mode - syncs filesystem to local database
bm sync

# In cloud mode - runs bidirectional sync with cloud + database sync
bm sync

# Watch mode (cloud only) - continuous sync
bm sync --watch

# Custom interval
bm sync --watch --interval 30
```

When cloud mode is enabled, `bm sync` runs rclone bisync to synchronize all your projects bidirectionally, then updates the cloud database.

## Bidirectional Sync (bisync)

The bisync approach uses rclone's proven bidirectional synchronization to keep your local files in sync with the cloud. This is the **recommended approach** for most users, especially those using Obsidian or other applications that rely on file watchers.

**Key Feature:** Syncs **all projects** in a single operation (bucket-level sync, like Dropbox).

### Bisync Setup

Before using bisync, you need to set up the bidirectional sync system:

```bash
bm cloud bisync-setup
```

This command will:
1. Check if rclone is installed (and install it if needed)
2. Retrieve your tenant information from the cloud
3. Generate secure, scoped credentials for your tenant
4. Configure rclone with your tenant's storage settings
5. Create local sync directory at `~/basic-memory-cloud-sync/` (fixed location)
6. Establish initial sync baseline with the cloud

You can optionally specify a custom sync directory:
```bash
bm cloud bisync-setup --dir ~/my-knowledge-base
```

### Running Bisync

Once set up, you can run bidirectional sync manually or in watch mode:

```bash
# Recommended: Use bm sync (adapts to cloud mode)
bm sync
bm sync --watch
bm sync --watch --interval 30

# Or use bm cloud bisync directly (power users)
bm cloud bisync                     # Manual sync
bm cloud bisync --dry-run          # Preview changes
bm cloud bisync --watch             # Continuous sync every 60s
bm cloud bisync --watch --interval 30  # Custom interval
bm cloud bisync --profile safe      # Keep conflicts as separate files
bm cloud bisync --profile fast      # Skip verification for speed
bm cloud bisync --verbose           # Show detailed file-by-file output
```

**Note:** Both `bm sync` and `bm cloud bisync` do the same thing in cloud mode. Use `bm sync` for simplicity, or `bm cloud bisync` when you need specific options like `--dry-run` or custom profiles.

### Bisync Profiles

Different profiles provide different conflict resolution and safety strategies:

- **safe**: Keep both versions on conflict (creates `.conflict-*` files)
  - Conflict resolution: `none` (keeps both)
  - Max delete: 10 files (prevents mass deletion)
  - Best for: Important documents, collaborative editing

- **balanced**: Auto-resolve to newer file (recommended)
  - Conflict resolution: `newer` (most recent wins)
  - Max delete: 25 files
  - Best for: General use, single-user editing

- **fast**: Skip verification for rapid iteration
  - Conflict resolution: `newer`
  - Max delete: 50 files
  - Best for: Development, frequent changes

### Establishing New Baseline

If you need to force a complete resync (after resolving conflicts or major changes):

```bash
bm cloud bisync --resync
```

⚠️ **Warning:** This will establish a new baseline. Make sure your local and cloud files are in the state you want before running.

### Bisync Status

Check the current sync status and configuration:

```bash
bm cloud bisync-status
```

Example output:
```
                        Cloud Bisync Status
┏━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Property           ┃ Value                                     ┃
┡━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ Tenant ID          │ 63cd4020-2e31-5c53-bdbc-07182b129183      │
│ Local Directory    │ ~/basic-memory-63cd4020-2e31-...          │
│ Status             │ ✓ Initialized                             │
│ Last Sync          │ 2025-09-28 15:30:45                       │
└────────────────────┴───────────────────────────────────────────┘

Available bisync profiles:
  safe: Safe mode with conflict preservation (keeps both versions)
  balanced: Balanced mode - auto-resolve to newer file (recommended)
  fast: Fast mode for rapid iteration (skip verification)
```

### Verifying Sync Integrity

**New in SPEC-9:** Check that files match between local and cloud without transferring data:

```bash
# Full integrity check
bm cloud check

# Faster one-way check (only checks for missing files)
bm cloud check --one-way
```

Example output:
```
Checking file integrity between local and cloud
Local:  /Users/you/basic-memory-cloud-sync
Remote: basic-memory-{tenant-id}:{bucket}

✓ All files match between local and cloud
```

If differences are found:
```
⚠ Differences found:
ERROR : file1.md: File not in Destination
ERROR : file2.md: sizes differ (1234 vs 5678)

To sync differences, run: bm sync
```

**Use cases:**
- Verify sync completed successfully
- Check for drift before making major changes
- Troubleshoot sync issues

### Working with Bisync

Once bisync is set up and running, your local directory (`~/basic-memory-cloud-sync/`) stays in sync with the cloud:

```bash
# Navigate to your synced directory
cd ~/basic-memory-cloud-sync

# All your projects are here
ls
# my-research/  work-notes/  personal/

# Edit files with any application
code my-notes.md
obsidian .
vim research/paper.md

# Manual sync when you want to push/pull changes
bm sync

# Or run watch mode in a terminal
bm sync --watch
```

**Key Benefits:**
- ✅ Syncs ALL projects in one operation (bucket-level)
- ✅ Works perfectly with Obsidian and other file-watching applications
- ✅ Can work offline, sync when connected
- ✅ Built-in conflict detection and resolution
- ✅ No custom code to maintain (uses proven rclone bisync)
- ✅ Safe max-delete protection
- ✅ Automatic project creation from local directories

### Filter Configuration

Both `upload` and `bisync` use the same ignore patterns from `~/.basic-memory/.bmignore`:

```
# Basic Memory Ignore Patterns
# This file is used by both 'bm cloud upload' and 'bm cloud bisync'
# Patterns use standard gitignore-style syntax

# Version control
.git
.svn

# Python
__pycache__
*.pyc
*.pyo
*.pyd
.venv
venv
env

# Node.js
node_modules

# IDE
.idea
.vscode

# OS files
.DS_Store
Thumbs.db

# Obsidian
.obsidian

# Temporary files
*.tmp
*.swp
*~
```

**Key Features:**
- ✅ **Single source of truth** - One file controls filtering for both operations
- ✅ **Auto-created** - File is created with sensible defaults on first use
- ✅ **Gitignore syntax** - Uses familiar `.gitignore`-style patterns
- ✅ **Customizable** - Edit the file to add your own patterns
- ✅ **Per-project overrides** - Local `.gitignore` files are also respected by `upload`

**For bisync:** The patterns are automatically converted to rclone filter format (saved as `~/.basic-memory/.bmignore.rclone`).

## NFS Mount (Direct Access)

The mount approach provides direct file access through an NFS mount. This is best for users who need real-time file access and are comfortable with network filesystem limitations.

### Mount Setup

Before mounting files, you need to set up the local access system:

```bash
bm cloud setup
```

This command will:
1. Check if rclone is installed (and install it if needed)
2. Retrieve your tenant information from the cloud
3. Generate secure, scoped credentials for your tenant
4. Configure rclone with your tenant's storage settings
5. Mount your files with the balanced profile

### Mounting Files

Mount your cloud files to a local directory (fixed location: `~/basic-memory-cloud/`):

```bash
# Mount with default (balanced) profile
bm cloud mount

# Mount with specific profile
bm cloud mount --profile fast
bm cloud mount --profile balanced
bm cloud mount --profile safe
```

**Note:** The mount location is fixed at `~/basic-memory-cloud/` to prevent conflicts with bisync. This is different from bisync which uses `~/basic-memory-cloud-sync/` by default.

#### Mount Profiles

Different profiles optimize for different use cases:

- **fast**: Ultra-fast development (5s sync, higher bandwidth)
  - Cache time: 5s, Poll interval: 3s
  - Best for: Active development, frequent file changes

- **balanced**: Fast development (10-15s sync, recommended)
  - Cache time: 10s, Poll interval: 5s
  - Best for: General use, good balance of speed and reliability

- **safe**: Conflict-aware mount with backup (15s+ sync)
  - Cache time: 15s, Poll interval: 10s
  - Includes conflict detection and backup functionality
  - Best for: Collaborative editing, important documents

### Mount Status

Check the current mount status:

```bash
bm cloud mount-status
```

Example output:
```
                               Cloud Mount Status
┏━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Property         ┃ Value                                                     ┃
┡━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ Tenant ID        │ 63cd4020-2e31-5c53-bdbc-07182b129183                      │
│ Mount Path       │ ~/basic-memory-cloud (fixed location)                     │
│ Status           │ ✓ Mounted                                                 │
│ rclone Processes │ 1 running                                                 │
└──────────────────┴───────────────────────────────────────────────────────────┘

Available mount profiles:
  fast: Ultra-fast development (5s sync, higher bandwidth)
  balanced: Fast development (10-15s sync, recommended)
  safe: Conflict-aware mount with backup
```

### Unmounting Files

Unmount your cloud files and clean up processes:

```bash
bm cloud unmount
```

This command will:
1. Unmount the filesystem
2. Kill any running rclone processes
3. Clean up temporary files

### Working with Mounted Files

Once mounted, your cloud files appear as a regular directory on your local system. You can:

- Edit files with any text editor or IDE
- Create new files and directories
- Move and rename files
- Use command-line tools like `grep`, `find`, etc.

#### Example Workflow

```bash
# Enable cloud mode
bm cloud login

# Set up local rclone
bm cloud setup

# Mount your files (all projects)
bm cloud mount --profile balanced

# Navigate to your mounted files
cd ~/basic-memory-cloud

# All your projects are here
ls
# my-research/  work-notes/  personal/

# Edit files with your preferred editor
code my-research/notes.md
vim work-notes/tasks.md

# Changes are automatically synced to the cloud
# Check sync status
bm cloud mount-status

# When done, unmount
bm cloud unmount
```

**Key Differences from Bisync:**
- Mount: Direct file access, changes stream to cloud
- Bisync: Local copy, explicit sync operations
- Mount: Fixed location `~/basic-memory-cloud/`
- Bisync: Default location `~/basic-memory-cloud-sync/` (configurable)
- Both: Operate at bucket level (all projects)

### Technical Details

- **Protocol**: Uses rclone with NFS mount (no FUSE dependencies)
- **Storage**: Files are stored in Tigris object storage (S3-compatible)
- **Sync**: Bidirectional synchronization with configurable cache settings
- **Security**: Uses scoped, time-limited credentials for your tenant only
- **Compatibility**: Works on macOS, Linux, and Windows

## Instance Management

### Health Check

Check if a cloud instance is healthy and get version information:

```bash
bm cloud status
```

Example output:
```
Cloud instance is healthy
  Status: ok
  Version: 0.14.4
  Timestamp: 2024-01-15T10:30:00Z
```

## WebDAV Protocol

File uploads use the WebDAV protocol for efficient, resumable file transfers. The CLI handles:

- Directory structure preservation
- File metadata preservation (timestamps)
- Error handling and retry logic
- Progress reporting

### WebDAV Endpoints

Files are uploaded to: `{host_url}/{project}/webdav/{file_path}`

Example:
- Host: `https://cloud.basicmemory.com`
- Project: `my-research`
- File: `docs/notes.md`
- WebDAV URL: `https://cloud.basicmemory.com/proxy/my-research/webdav/docs/notes.md`

### Authentication Configuration

By default, the CLI uses production authentication settings. For development or custom deployments, you can override these settings.

#### Production vs Development

- **Production** (default): Uses `client_01K4DGBWAZWP83N3H8VVEMRX6W` and `https://eloquent-lotus-05.authkit.app`
- **Development**: Uses `client_01K6JW0F9QY5DZ2834GQTXX5JN` and `https://exciting-aquarium-32-staging.authkit.app`

#### Configuration File

You can also set the values in `~/.basic-memory/config.json`:

development
```json
{
  "cloud_host": "https://development.cloud.basicmemory.com", 
  "cloud_client_id": "client_01K6JW0F9QY5DZ2834GQTXX5JN",
  "cloud_domain": "https://exciting-aquarium-32-staging.authkit.app",
}
```

## Troubleshooting

### Authentication Issues

**Problem**: "Not authenticated" errors
**Solution**: Re-run the login command:
```bash
bm cloud login
```

**Problem**: Wrong environment (dev vs prod)
**Solution**: Check and set the correct environment variables or config

### Upload Issues

**Problem**: "No files found to upload"
**Solution**: Check gitignore filtering or use `--no-gitignore`:
```bash
bm cloud upload my-project ./path --no-gitignore
```

**Problem**: Upload timeouts
**Solution**: The CLI uses a 5-minute timeout for large uploads. For very large files, consider breaking them into smaller chunks.

### Connection Issues

**Problem**: "API request failed" errors
**Solution**:
1. Verify the cloud instance is running: `bm cloud status`
2. Check your internet connection

### Mount Issues

**Problem**: "rclone not found" during setup
**Solution**: The setup command will attempt to install rclone automatically. If this fails:
- **macOS**: `brew install rclone`
- **Linux**: `sudo snap install rclone` or `sudo apt install rclone`
- **Windows**: `winget install Rclone.Rclone`

**Problem**: Mount fails with permission errors
**Solution**:
- Ensure you have proper permissions for the mount directory
- On Linux, you may need to add your user to the `fuse` group
- Try unmounting any existing mounts: `bm cloud unmount`

**Problem**: Files not syncing or appearing outdated
**Solution**:
1. Check mount status: `bm cloud mount-status`
2. Try remounting with a faster profile: `bm cloud mount --profile fast`
3. Unmount and remount: `bm cloud unmount && bm cloud mount`

**Problem**: Multiple mount processes running
**Solution**: Clean up orphaned processes:
```bash
bm cloud unmount  # This will clean up all processes
bm cloud mount    # Fresh mount
```

## Security

- All communication uses HTTPS
- OAuth 2.1 with PKCE provides secure authentication
- Tokens automatically refresh when needed
- Tokens are stored locally in `~/.basic-memory/basic-memory-cloud.json`

## Command Reference

### Cloud Mode Management
```bash
# Enable cloud mode (all commands work with cloud)
bm cloud login

# Disable cloud mode (return to local)
bm cloud logout

# Check cloud mode and instance health
bm cloud status
```

### Project Management (Cloud Mode Aware)
```bash
# When cloud mode enabled - works with cloud projects
# When cloud mode disabled - works with local projects
bm project list                    # List projects
bm project add <name> [--default]  # Create project
```

### File Synchronization
```bash
# Recommended: Cloud-mode aware sync command
bm sync                            # Sync once (local or cloud depending on mode)
bm sync --watch                    # Continuous sync (cloud mode only)
bm sync --watch --interval 30      # Custom interval

# Power users: Direct bisync commands
bm cloud bisync-setup [--dir ~/path]    # Set up bisync
bm cloud bisync                         # Run manual sync
bm cloud bisync --dry-run               # Preview changes
bm cloud bisync --resync                # Force new baseline
bm cloud bisync --watch                 # Continuous sync (60s)
bm cloud bisync --watch --interval 30   # Custom interval
bm cloud bisync --profile safe|balanced|fast  # Use specific profile
bm cloud bisync --verbose               # Show detailed output
bm cloud bisync-status                  # Check sync status

# Integrity verification
bm cloud check                     # Verify files match (no data transfer)
bm cloud check --one-way          # Faster check (missing files only)
```

### File Upload (Legacy)
```bash
# Upload files via WebDAV (alternative to bisync)
bm cloud upload <project> <path> [--no-preserve-timestamps] [--no-gitignore]
```

### Direct File Access (NFS Mount)
```bash
bm cloud setup                             # Set up mount with rclone
bm cloud mount [--profile fast|balanced|safe]  # Mount to ~/basic-memory-cloud/
bm cloud mount-status                      # Check mount status
bm cloud unmount                           # Unmount cloud files
```

## Summary

**Recommended Workflow:**
1. `bm cloud login` - Enable cloud mode
2. `bm cloud bisync-setup` - Set up sync (one time)
3. `bm sync --watch` - Keep files in sync
4. Use regular `bm project`, `bm tool` commands - they work with cloud now
5. `bm cloud check` - Verify integrity when needed
6. `bm cloud logout` - Return to local mode when done

**Directory Structure:**
- `~/basic-memory-cloud/` - NFS mount point (fixed, all projects)
- `~/basic-memory-cloud-sync/` - Bisync directory (default, all projects)
- Projects are folders within these directories
- Both operate at bucket level (Dropbox model)

For more information about Basic Memory Cloud, visit the [official documentation](https://memory.basicmachines.co).
