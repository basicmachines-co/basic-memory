# Basic Memory Cloud CLI Guide

The Basic Memory Cloud CLI provides seamless integration between local and cloud knowledge bases using **project-scoped synchronization**. Each project can optionally sync with the cloud, giving you fine-grained control over what syncs and where.

## Overview

The cloud CLI enables you to:
- **Authenticate cloud access** - OAuth/API key credentials are stored locally for cloud operations
- **Project-scoped sync** - Each project independently manages its sync configuration
- **Explicit operations** - Sync only what you want, when you want
- **Push/pull transfers** - Additive, git-style transfers that work on Personal and Team workspaces
- **Offline access** - Work locally, sync when ready

### Personal vs Team workspaces

`bm cloud pull` and `bm cloud push` are the standard way to move files between a local project and the cloud. They work the same way on Personal and Team workspaces:

| Command | Direction | Behavior | Personal | Team |
|---|---|---|---|---|
| `bm cloud pull` | cloud → local | **additive** — never deletes local | ✅ | ✅ |
| `bm cloud push` | local → cloud | **additive** — never deletes cloud | ✅ | ✅ |

`push` and `pull` never delete on the destination, so they are safe on a shared Team bucket. On Personal workspaces they run over rclone; on Team workspaces they run over WebDAV, where the service applies per-project access. You run the same commands either way.

The older rclone mirror commands (`bm cloud sync`, `bm cloud bisync`, `bm cloud bisync-reset`) are **deprecated**. They still run on Personal workspaces, refuse Team workspaces, and will be removed in a future release. See [Deprecated: rclone mirror commands](#deprecated-rclone-mirror-commands-personal-only).

## Prerequisites

Before using Basic Memory Cloud, you need:

- **Active Subscription**: An active Basic Memory Cloud subscription is required to access cloud features
- **Subscribe**: Visit [https://basicmemory.com/pricing](https://basicmemory.com/pricing) to sign up
- **Optional**: Cloud is optional. Local-first open-source usage continues without cloud.
- **OSS Discount**: Use code `{{OSS_DISCOUNT_CODE}}` for 20% off for 3 months.

If you attempt to log in without an active subscription, you'll receive a "Subscription Required" error with a link to subscribe.

## Architecture: Project-Scoped Sync

### The Problem

**Old approach (SPEC-8):** All projects lived in a single `~/basic-memory-cloud-sync/` directory. This caused:
- ❌ Directory conflicts between mount and sync
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

# Now you can sync individually:
bm cloud pull --name research
bm cloud push --name research
bm cloud pull --name work
# temp stays cloud-only
```

**What happens under the covers:**
- Config stores each project's local sync path (`local_sync_path`)
- `push`/`pull` compare the local directory with the project's cloud copy and transfer only new or changed files
- Personal workspaces transfer through the rclone remote `basic-memory-cloud`; Team workspaces transfer over WebDAV
- Projects can live anywhere on your filesystem, not forced into a sync directory

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
1. Installs rclone with a supported package manager (if needed)
2. Fetches your tenant information from cloud
3. Generates scoped S3 credentials for sync
4. Configures single rclone remote: `basic-memory-cloud`

**Result:** You're ready to sync projects. No sync directories created yet - those come with project setup.

Rclone setup uses package managers such as Homebrew, MacPorts, apt, dnf, yum, pacman,
zypper, snap, winget, Chocolatey, or Scoop when available. It does not run remote
install scripts with `sudo`; if no supported package manager is found, the CLI prints
manual install instructions.

### 3. Add Projects with Sync

Create projects with optional local sync paths:

```bash
# Create cloud project without local sync
bm project add research --cloud

# Create cloud project WITH local sync
bm project add research --cloud --local-path ~/Documents/research

# Or configure sync for existing project
bm cloud sync-setup research ~/Documents/research
```

**What happens under the covers:**

When you add a project with `--local-path`:
1. Project created on cloud at `/app/data/research`
2. Local path stored in config for that project (`local_sync_path`)
3. Local directory created if it doesn't exist

**Result:** Project is ready to sync, but no files synced yet.

### 4. Pull Cloud Files

Fetch the project's cloud files into your local directory. Preview with `--dry-run` first:

```bash
# Preview what would be downloaded
bm cloud pull --name research --dry-run

# Download new and changed cloud files
bm cloud pull --name research
```

**What happens:**
1. Compares cloud and local
2. Downloads files that are new or changed on the cloud
3. Leaves local-only files untouched (never deletes local)
4. If a file differs on both sides, aborts and lists the conflicts

**Result:** Your local directory has the cloud files. There is no baseline to set up and no `--resync` step.

### 5. Push Local Changes

After editing locally, upload your changes:

```bash
bm cloud push --name research --dry-run
bm cloud push --name research
```

**What happens:**
1. Compares local and cloud
2. Uploads files that are new or changed locally
3. Leaves cloud-only files untouched (never deletes cloud)
4. If a file differs on both sides, aborts and lists the conflicts — pull first, like a rejected `git push`

**Result:** The cloud has your local changes. Day to day, run `pull` before you start and `push` when you are done.

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
- Does not transfer any files yet

**Result:** Project ready to sync. Run `bm cloud pull --name research` to fetch cloud files, and `bm cloud push --name research` to upload local changes.

**Use case 3: Add sync to existing cloud project**

```bash
# Project already exists on cloud
bm cloud sync-setup research ~/Documents/research
```

**What this does:**
- Updates existing project's sync configuration
- Creates local directory
- Does not transfer any files yet

**Result:** Existing cloud project now has a local sync path. Run `bm cloud pull --name research` to download its files.

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

Team workspace projects can have a local sync path just like Personal ones, so they are listed the same way.

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

### push / pull (additive, git-style)

`push` and `pull` are the supported transfer commands on Personal and Team workspaces. They model `git push` / `git pull`:

- **`bm cloud pull`** fetches changes from the cloud into your local directory.
- **`bm cloud push`** uploads your local changes to the cloud.

Both are **additive — they never delete on the destination**. A conflict (a file that differs on both sides) is never resolved silently: by default the command aborts and lists the conflicting files, exactly like git refusing to clobber your changes. Neither command needs a baseline or a `--resync` step. Both respect `.bmignore`.

| Option | Purpose |
|---|---|
| `--name <project>` | Project to transfer (required) |
| `--dry-run` | Preview the transfer without changing anything |
| `--on-conflict <strategy>` | How to handle files that differ on both sides (default `fail`) |
| `--workspace <workspace>` | Workspace slug, name, or tenant ID, when the project name exists in more than one workspace |
| `--verbose` | Show detailed output |

#### Pull: fetch cloud changes

```bash
# Preview first (recommended)
bm cloud pull --name research --dry-run

# Fetch new/changed cloud files into local
bm cloud pull --name research
```

**What happens:**
1. Compares cloud and local
2. Downloads files that are new or changed on the cloud
3. Leaves your local-only files untouched (never deletes local)
4. If any file differs on both sides, aborts and lists the conflicts (unless you pass `--on-conflict`)

#### Push: upload local changes

```bash
bm cloud push --name research --dry-run
bm cloud push --name research
```

**What happens:**
1. Compares local and cloud
2. Uploads files that are new or changed locally
3. Leaves cloud-only files untouched (never deletes cloud)
4. If any file differs on both sides, aborts and lists the conflicts — pull first, like a rejected `git push`

#### Resolving conflicts

When `push`/`pull` reports conflicts, re-run with `--on-conflict` to choose how differing files are handled. The value names exactly what survives, so it reads the same in both directions:

| `--on-conflict` | Behavior |
|---|---|
| `fail` *(default)* | List the conflicting files and exit without transferring anything |
| `keep-cloud` | Take the cloud version (pull: overwrite local; push: skip those files) |
| `keep-local` | Keep the local version (pull: skip those files; push: overwrite cloud) |
| `keep-both` | Keep both — write the incoming version beside the existing one as `name.conflict-<date>.md` |

```bash
# A teammate edited notes you also changed locally — pull reports a conflict:
bm cloud pull --name research
# pull aborted: 1 file(s) differ between local and cloud.
#   * notes/decisions.md
# Re-run with one of: --on-conflict keep-cloud | keep-local | keep-both

# Take the cloud copy:
bm cloud pull --name research --on-conflict keep-cloud

# Or keep both versions to merge by hand:
bm cloud pull --name research --on-conflict keep-both
```

#### Ambiguous project names

If you belong to more than one workspace and the same project name exists in several of them, pass `--workspace` to pick one:

```bash
bm cloud pull --name research --workspace acme
```

#### Limitations

`push`/`pull` are deliberately simple, conflict-aware byte transfers — not a full reconciler. Without a sync baseline:

- **Deletions are not propagated.** A note deleted on one side is not removed from the other (we cannot tell an intentional delete from a file the other side never had). This is surfaced in the command output.
- **Every divergence is treated as a conflict.** We cannot tell a teammate's edit from your stale copy, so any differing file prompts a decision rather than auto-resolving.

For conflict-aware *editing*, write through the MCP/API tools (which merge at the note level). A bidirectional reconciler with a real baseline is tracked in [issue #862](https://github.com/basicmachines-co/basic-memory/issues/862).

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

**Use case:** You have several projects with local sync and want to sync them all.

```bash
# Setup multiple projects
bm project add research --cloud --local-path ~/Documents/research
bm project add work --cloud --local-path ~/work-notes
bm project add personal --cloud --local-path ~/personal

# Fetch cloud changes for each
bm cloud pull --name research
bm cloud pull --name work
bm cloud pull --name personal

# Upload local changes for each
bm cloud push --name research
bm cloud push --name work
bm cloud push --name personal
```

Each command acts on one project. Run them per project.

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
bm cloud pull --name research
bm cloud push --name research
bm cloud pull --name work
bm cloud push --name work

# Archive and temp-notes stay cloud-only
```

**Result:** Fine-grained control over what syncs.

## Per-Project Cloud Routing (API Key)

Route individual projects through cloud using an API key. This lets you keep some projects local while others route through cloud.

### Setting Up API Key Auth

**Option A: Create a key in the web app, then save it locally:**

```bash
bm cloud api-key save bmc_abc123...
```

**Option B: Create a key via CLI (requires OAuth login first):**

```bash
bm cloud login                         # One-time OAuth login
bm cloud api-key create "my-laptop"    # Creates key and saves it locally
```

The API key is account-level — it grants access to all your cloud projects. It's stored in `~/.basic-memory/config.json` as `cloud_api_key`.
On POSIX systems, Basic Memory writes `~/.basic-memory/` as user-private (`0700`) and
`config.json` as user-read/write only (`0600`). Treat this config file as a credential
file when an API key is saved.

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
# Hidden files and directories
.*

# Basic Memory internals
*.db
*.db-shm
*.db-wal
config.json

# Version control
.git
.svn

# Python
__pycache__
*.pyc
*.pyo
*.pyd
.pytest_cache
.coverage
*.egg-info
.tox
.mypy_cache
.ruff_cache

# Virtual environments
.venv
venv
env
.env

# Node.js
node_modules

# Build artifacts
build
dist
.cache

# IDE
.idea
.vscode

# OS files
.DS_Store
Thumbs.db
desktop.ini

# Obsidian
.obsidian

# Temporary files
*.tmp
*.swp
*.swo
*~
```

**How it works:**
1. On first sync, `.bmignore` created with defaults
2. Patterns converted to rclone filter format (`.bmignore.rclone`)
3. `push`/`pull` skip matching paths on both sides (rclone filters on Personal, the same patterns over WebDAV on Team)
4. Same patterns used by all projects

During conversion, file patterns exclude the direct match and recursive contents.
For example, `config.json` becomes both `- config.json` and `- config.json/**`,
while `.*` becomes both `- .*` and `- .*/**`. Directory-only patterns keep
their trailing slash, so `cache/` becomes `- cache/` and `- cache/**`.

**Customizing:**

```bash
# Edit patterns
code ~/.basic-memory/.bmignore

# Add custom patterns
echo "*.tmp" >> ~/.basic-memory/.bmignore

# Next transfer uses updated patterns
bm cloud push --name research
```

## Troubleshooting

### Rclone Setup Cannot Install Automatically

**Problem:** `bm cloud setup` cannot find a supported package manager, or package-manager
installation fails.

**Explanation:** The CLI avoids remote privileged install scripts. It only invokes known
package managers and otherwise asks you to install rclone manually.

**Solution:** Install rclone with your OS package manager, then rerun setup:

```bash
# macOS
brew install rclone

# Debian/Ubuntu
sudo apt install rclone

# Fedora
sudo dnf install rclone

# Arch
sudo pacman -S rclone

# After rclone is on PATH
bm cloud setup
```

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

### Push or Pull Reports Conflicts

**Problem:** `push` or `pull` aborts with "file(s) differ between local and cloud"

**Explanation:** The listed files changed on both sides. The default `--on-conflict fail` stops before transferring anything so nothing is overwritten silently.

**Solution:** Re-run with the strategy you want:

```bash
bm cloud pull --name research --on-conflict keep-cloud   # take the cloud copy
bm cloud pull --name research --on-conflict keep-local   # keep your local copy
bm cloud pull --name research --on-conflict keep-both    # keep both, merge by hand
```

See [Resolving conflicts](#resolving-conflicts).

### Deleted Files Come Back

**Problem:** A note you deleted locally is still on the cloud (or reappears after `pull`).

**Explanation:** `push` and `pull` never delete on the destination, so deletions are not propagated.

**Solution:** Delete the note on the other side as well, for example through the web app or the MCP `delete_note` tool.

### Project Name Is Ambiguous

**Problem:** `push` or `pull` reports that the project "does not have an unambiguous cloud workspace" (the same project name exists in more than one workspace).

**Solution:** Name the workspace:

```bash
bm cloud pull --name research --workspace acme
```

### Project Not Configured for Sync

**Problem:** "Project research has no local_sync_path configured"

**Explanation:** Project exists on cloud but has no local sync path.

**Solution:**

```bash
bm cloud sync-setup research ~/Documents/research
bm cloud pull --name research
```

### Connection Issues

**Problem:** "Cannot connect to cloud instance"

**Solution:** Check status:

```bash
bm cloud status
```

If instance is down, wait a few minutes and retry.

## Deprecated: rclone mirror commands (Personal only)

`bm cloud sync`, `bm cloud bisync`, and `bm cloud bisync-reset` are **deprecated and will be removed in a future release**. Use `bm cloud pull` / `bm cloud push` instead.

Until removal:
- They are marked deprecated in `--help`.
- Every run prints a deprecation warning that points to `bm cloud pull --name <project>` / `bm cloud push --name <project>`.
- They still run on Personal workspaces.
- They refuse Team workspaces. As mirrors, they can delete a teammate's files on a shared bucket.

`bm cloud check` is a legacy, Personal-only command that compares against the same mirror remote. Use `bm cloud pull --dry-run` / `bm cloud push --dry-run` to preview differences instead.

This section is kept so existing users can keep working until the commands are removed.

### sync (deprecated): one-way mirror, local → cloud

```bash
bm cloud sync --name research --dry-run
bm cloud sync --name research
```

Makes the cloud identical to local with `rclone sync`. Cloud files that are missing locally are **deleted**. Preview deletions with `--dry-run`.

### bisync (deprecated): two-way mirror, local ↔ cloud

```bash
# First run: establish a baseline
bm cloud bisync --name research --resync --dry-run
bm cloud bisync --name research --resync

# Later runs
bm cloud bisync --name research
```

Syncs both directions and can delete or overwrite on both sides. Conflicts resolve as newer file wins. State lives in `~/.basic-memory/bisync-state/<project>/`.

- **"First bisync requires --resync"** - Run once with `--resync` to create the baseline. Do not use `--resync` again unless you need a new baseline.
- **"Empty prior Path1 listing. Cannot sync to an empty directory"** - Bisync cannot baseline an empty directory. Add a file (for example a `README.md`) and run `--resync` again.
- **"max delete limit (25) exceeded"** - Bisync stops when a run would delete more than 25 files. Check with `--dry-run`, then run `--resync` if the deletes are intended.
- **Corrupted state or listing files** - Clear the state with `bm cloud bisync-reset research`, then run `--resync`. This does not touch your files. Removing a project also clears its bisync state.

To move off bisync, run `bm cloud pull --name <project>` and `bm cloud push --name <project>` instead. They need no baseline or state.

### check (legacy): verify mirror integrity

```bash
bm cloud check --name research
bm cloud check --name research --one-way   # faster, one direction
```

Compares checksums between local and the Personal mirror remote. No files are transferred.

## Security

- **Authentication**: OAuth 2.1 with PKCE flow
- **Tokens**: Stored securely in `~/.basic-memory/basic-memory-cloud.json`
- **API keys**: Stored in `~/.basic-memory/config.json`, which is written with private file permissions on POSIX systems
- **Transport**: All data encrypted in transit (HTTPS)
- **Credentials**: Scoped S3 credentials (read-write to your tenant only)
- **Rclone setup**: Uses package managers or manual instructions; no remote privileged install-script fallback
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
bm cloud api-key save <key>      # Save a cloud API key (bmc_ prefixed)
bm cloud api-key create <name>   # Create API key via cloud API (requires OAuth login)
```

### Setup

```bash
bm cloud setup              # Install rclone via package manager and configure credentials
```

### Project Management

```bash
bm project list --local                   # Local project list
bm project list --cloud                   # Cloud project list
bm project add <name> --cloud             # Create cloud project (no sync)
bm project add <name> --cloud --local-path <path> # Create with local sync
bm cloud sync-setup <name> <path>       # Add sync to existing project
bm project rm <name>                      # Delete project
```

### Per-Project Routing

```bash
bm project set-cloud <name>  # Route project through cloud (requires API key)
bm project set-local <name>  # Revert project to local mode
```

### File Synchronization

```bash
# Pull: fetch cloud changes (cloud → local) - additive
bm cloud pull --name <project>
bm cloud pull --name <project> --dry-run
bm cloud pull --name <project> --on-conflict [fail|keep-local|keep-cloud|keep-both]
bm cloud pull --name <project> --workspace <workspace>

# Push: upload local changes (local → cloud) - additive
bm cloud push --name <project>
bm cloud push --name <project> --dry-run
bm cloud push --name <project> --on-conflict [fail|keep-local|keep-cloud|keep-both]
bm cloud push --name <project> --workspace <workspace>

# List project files by route
bm project ls --name <project>          # Default target: local
bm project ls --name <project> --local
bm project ls --name <project> --cloud
bm project ls --name <project> --cloud --path <subpath>
```

### Deprecated (Personal only, removed in a future release)

```bash
bm cloud sync --name <project>              # Deprecated one-way mirror
bm cloud bisync --name <project> [--resync] # Deprecated two-way mirror
bm cloud bisync-reset <project>             # Deprecated: clear bisync state
bm cloud check --name <project>             # Legacy mirror integrity check
```

## Summary

**Basic Memory Cloud uses project-scoped sync:**

1. **Authenticate cloud access** - `bm cloud login`
2. **Install rclone** - `bm cloud setup`
3. **Add projects with sync** - `bm project add research --cloud --local-path ~/Documents/research`
4. **Fetch cloud changes** - `bm cloud pull --name research`
5. **Upload your changes** - `bm cloud push --name research`
6. **Resolve conflicts explicitly** - re-run with `--on-conflict keep-cloud|keep-local|keep-both`

The same workflow applies to Personal and Team workspaces.

**Key benefits:**
- ✅ Each project independently syncs (or doesn't)
- ✅ Projects can live anywhere on disk
- ✅ Explicit sync operations (no magic)
- ✅ Push/pull never delete on the destination
- ✅ Git-style conflict aborts instead of silent overwrites
- ✅ Full offline access (work locally, sync when ready)

**Future enhancements:**
- Project list showing sync status
- Watch mode for automatic sync
