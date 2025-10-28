---
title: 'SPEC-20: Simplified Project-Scoped Rclone Sync'
date: 2025-01-27
status: Draft
priority: High
goal: Simplify cloud sync by making it project-scoped, safe by design, and closer to native rclone commands
parent: SPEC-8
---

## Executive Summary

The current rclone implementation (SPEC-8) has proven too complex with multiple footguns:
- Two workflows (mount vs bisync) with different directories causing confusion
- Multiple profiles (3 for mount, 3 for bisync) creating too much choice
- Directory conflicts (`~/basic-memory-cloud/` vs `~/basic-memory-cloud-sync/`)
- Auto-discovery of folders leading to errors
- Unclear what syncs and when

This spec proposes a **radical simplification**: project-scoped sync operations that are explicit, safe, and thin wrappers around rclone commands.

## Why

### Current Problems

**Complexity:**
- Users must choose between mount and bisync workflows
- Different directories for different workflows
- Profile selection (6 total profiles) overwhelms users
- Setup requires multiple steps with potential conflicts

**Footguns:**
- Renaming local folder breaks sync (no config tracking)
- Mount directory conflicts with bisync directory
- Auto-discovered folders create phantom projects
- Uninitialized bisync state causes failures
- Unclear which files sync (all files in root directory?)

**User Confusion:**
- "What does `bm sync` actually do?"
- "Is `~/basic-memory-cloud-sync/my-folder/` a project or just a folder?"
- "Why do I have two basic-memory directories?"
- "How do I sync just one project?"

### Design Principles (Revised)

1. **Projects are independent** - Each project manages its own sync state
2. **Global cloud mode** - You're either local or cloud (no per-project flag)
3. **Explicit operations** - No auto-discovery, no magic
4. **Safe by design** - Config tracks state, not filesystem
5. **Thin rclone wrappers** - Stay close to rclone commands
6. **One good way** - Remove choices that don't matter

## What

### New Architecture

#### Core Concepts

1. **Global Cloud Mode** (existing, keep as-is)
   - `cloud_mode_enabled` in config
   - `bm cloud login` enables it, `bm cloud logout` disables it
   - When enabled, ALL Basic Memory operations hit cloud API

2. **Project-Scoped Sync** (new)
   - Each project optionally has a `local_path` for local working copy
   - Sync operations are explicit: `bm project sync --name research`
   - Projects can live anywhere on disk, not forced into sync directory

3. **Simplified rclone Config** (new)
   - Single remote named `bm-cloud` (not `basic-memory-{tenant_id}`)
   - One credential set per user
   - Config lives at `~/.config/rclone/rclone.conf`

#### Command Structure

```bash
# Setup (once)
bm cloud login                          # Authenticate, enable cloud mode
bm cloud setup                          # Install rclone, generate credentials

# Project creation with optional sync
bm project add research                 # Create cloud project (no local sync)
bm project add research --local ~/docs  # Create with local sync configured

# Or configure sync later
bm project sync-setup research ~/docs   # Configure local sync for existing project

# Project-scoped rclone operations
bm project sync --name research         # One-way sync (local → cloud)
bm project bisync --name research       # Two-way sync (local ↔ cloud)
bm project check --name research        # Verify integrity

# Advanced file operations
bm project ls --name research [path]    # List remote files
bm project copy --name research src dst # Copy files

# Batch operations
bm project sync --all                   # Sync all projects with local_sync_path
bm project bisync --all                 # Two-way sync all projects
```

#### Config Model

```json
{
  "cloud_mode": true,
  "cloud_host":  "https://cloud.basicmemory.com",

  "projects": {
    // Used in LOCAL mode only (simple name → path mapping)
    "main": "/Users/user/basic-memory"
  },

  "cloud_projects": {
    // Used in CLOUD mode for sync configuration
    "research": {
      "local_path": "~/Documents/research",
      "last_sync": "2025-01-27T10:00:00Z",
      "bisync_initialized": true
    },
    "work": {
      "local_path": "~/work",
      "last_sync": null,
      "bisync_initialized": false
    }
  }
}
```

**Note:** In cloud mode, the actual project list comes from the API (`GET /projects/projects`). The `cloud_projects` dict only stores local sync configuration.

#### Rclone Config

```ini
# ~/.config/rclone/rclone.conf
[basic-memory-cloud]
type = s3
provider = Other
access_key_id = {scoped_access_key}
secret_access_key = {scoped_secret_key}
endpoint = https://fly.storage.tigris.dev
region = auto
```

### What Gets Removed

- ❌ Mount commands (`bm cloud mount`, `bm cloud unmount`, `bm cloud mount-status`)
- ❌ Profile selection (both mount and bisync profiles)
- ❌ `~/basic-memory-cloud/` directory (mount point)
- ❌ `~/basic-memory-cloud-sync/` directory (forced sync location)
- ❌ Auto-discovery of folders
- ❌ Separate `bisync-setup` command
- ❌ `bisync_config` in config.json
- ❌ Convenience commands (`bm sync`, `bm bisync` without project name)
- ❌ Complex state management for global sync

### What Gets Simplified

- ✅ One setup command: `bm cloud setup`
- ✅ One rclone remote: `bm-cloud`
- ✅ One workflow: project-scoped bisync (remove mount)
- ✅ One set of defaults (balanced settings from SPEC-8)
- ✅ Clear project-to-path mapping in config
- ✅ Explicit sync operations only

### What Gets Added

- ✅ `bm project sync --name <project>` (one-way: local → cloud)
- ✅ `bm project bisync --name <project>` (two-way: local ↔ cloud)
- ✅ `bm project check --name <project>` (integrity verification)
- ✅ `bm project sync-setup <project> <local_path>` (configure sync)
- ✅ `bm project ls --name <project> [path]` (list remote files)
- ✅ `bm project copy --name <project> <src> <dst>` (copy files)
- ✅ `cloud_projects` dict in config.json

## How

### Phase 1: Project Model Updates

**1.1 Update Config Schema**

```python
# basic_memory/config.py
class Config(BaseModel):
    # ... existing fields ...
    cloud_mode: bool = False
    cloud_host: str = "https://cloud.basicmemory.com"

    # Local mode: simple name → path mapping
    projects: dict[str, str] = {}

    # Cloud mode: sync configuration per project
    cloud_projects: dict[str, CloudProjectConfig] = {}


class CloudProjectConfig(BaseModel):
    """Sync configuration for a cloud project."""
    local_path: str                        # Local working directory
    last_sync: Optional[datetime] = None   # Last successful sync
    bisync_initialized: bool = False       # Whether bisync baseline exists
```

**No database changes needed** - sync config lives in `~/.basic-memory/config.json` only.

### Phase 2: Simplified Rclone Configuration

**2.1 Simplify Remote Naming**

```python
# basic_memory/cli/commands/cloud/rclone_config.py

def configure_rclone_remote(
    access_key: str,
    secret_key: str,
    endpoint: str = "https://fly.storage.tigris.dev",
    region: str = "auto",
) -> None:
    """Configure single rclone remote named 'bm-cloud'."""

    config = load_rclone_config()

    # Single remote name (not tenant-specific)
    REMOTE_NAME = "basic-memory-cloud"

    if not config.has_section(REMOTE_NAME):
        config.add_section(REMOTE_NAME)

    config.set(REMOTE_NAME, "type", "s3")
    config.set(REMOTE_NAME, "provider", "Other")
    config.set(REMOTE_NAME, "access_key_id", access_key)
    config.set(REMOTE_NAME, "secret_access_key", secret_key)
    config.set(REMOTE_NAME, "endpoint", endpoint)
    config.set(REMOTE_NAME, "region", region)

    save_rclone_config(config)
```

**2.2 Remove Profile Complexity**

Use single set of balanced defaults (from SPEC-8 Phase 4 testing):
- `conflict_resolve`: `newer` (auto-resolve to most recent)
- `max_delete`: `25` (safety limit)
- `check_access`: `false` (skip for performance)

### Phase 3: Project-Scoped Rclone Commands

**3.1 Core Sync Operations**

```python
# basic_memory/cli/commands/cloud/rclone_commands.py

def get_project_remote(project: Project, bucket_name: str) -> str:
    """Build rclone remote path for project.

    Returns: bm-cloud:bucket-name/app/data/research
    """
    # Strip leading slash from cloud path
    cloud_path = project.path.lstrip("/")
    return f"basic-memory-cloud:{bucket_name}/{cloud_path}"


def project_sync(
    project: Project,
    bucket_name: str,
    dry_run: bool = False,
    verbose: bool = False,
) -> bool:
    """One-way sync: local → cloud.

    Uses rclone sync to make cloud identical to local.
    """
    if not project.local_sync_path:
        raise RcloneError(f"Project {project.name} has no local_sync_path configured")

    local_path = Path(project.local_sync_path).expanduser()
    remote_path = get_project_remote(project, bucket_name)
    filter_path = get_bmignore_filter_path()

    cmd = [
        "rclone", "sync",
        str(local_path),
        remote_path,
        "--filters-file", str(filter_path),
    ]

    if verbose:
        cmd.append("--verbose")
    else:
        cmd.append("--progress")

    if dry_run:
        cmd.append("--dry-run")

    result = subprocess.run(cmd, text=True)
    return result.returncode == 0


def project_bisync(
    project: Project,
    bucket_name: str,
    dry_run: bool = False,
    resync: bool = False,
    verbose: bool = False,
) -> bool:
    """Two-way sync: local ↔ cloud.

    Uses rclone bisync with balanced defaults.
    """
    if not project.local_sync_path:
        raise RcloneError(f"Project {project.name} has no local_sync_path configured")

    local_path = Path(project.local_sync_path).expanduser()
    remote_path = get_project_remote(project, bucket_name)
    filter_path = get_bmignore_filter_path()
    state_path = get_project_bisync_state(project.name)

    # Ensure state directory exists
    state_path.mkdir(parents=True, exist_ok=True)

    cmd = [
        "rclone", "bisync",
        str(local_path),
        remote_path,
        "--create-empty-src-dirs",
        "--resilient",
        "--conflict-resolve=newer",
        "--max-delete=25",
        "--filters-file", str(filter_path),
        "--workdir", str(state_path),
    ]

    if verbose:
        cmd.append("--verbose")
    else:
        cmd.append("--progress")

    if dry_run:
        cmd.append("--dry-run")

    if resync:
        cmd.append("--resync")

    # Check if first run requires resync
    if not resync and not bisync_initialized(project.name) and not dry_run:
        raise RcloneError(
            f"First bisync for {project.name} requires --resync to establish baseline.\n"
            f"Run: bm project bisync --name {project.name} --resync"
        )

    result = subprocess.run(cmd, text=True)
    return result.returncode == 0


def project_check(
    project: Project,
    bucket_name: str,
    one_way: bool = False,
) -> bool:
    """Check integrity between local and cloud.

    Returns True if files match, False if differences found.
    """
    if not project.local_sync_path:
        raise RcloneError(f"Project {project.name} has no local_sync_path configured")

    local_path = Path(project.local_sync_path).expanduser()
    remote_path = get_project_remote(project, bucket_name)
    filter_path = get_bmignore_filter_path()

    cmd = [
        "rclone", "check",
        str(local_path),
        remote_path,
        "--filter-from", str(filter_path),
    ]

    if one_way:
        cmd.append("--one-way")

    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode == 0
```

**3.2 Advanced File Operations**

```python
def project_ls(
    project: Project,
    bucket_name: str,
    path: Optional[str] = None,
) -> list[str]:
    """List files in remote project."""
    remote_path = get_project_remote(project, bucket_name)
    if path:
        remote_path = f"{remote_path}/{path}"

    cmd = ["rclone", "ls", remote_path]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return result.stdout.splitlines()


def project_copy(
    project: Project,
    bucket_name: str,
    src: str,
    dst: str,
    dry_run: bool = False,
) -> bool:
    """Copy files within project scope."""
    # Implementation similar to sync
    pass
```

### Phase 4: CLI Integration

**4.1 Update Project Commands**

```python
# basic_memory/cli/commands/project.py

@project_app.command("add")
def add_project(
    name: str = typer.Argument(..., help="Name of the project"),
    path: str = typer.Argument(None, help="Path (required for local mode)"),
    local: str = typer.Option(None, "--local", help="Local sync path for cloud mode"),
    set_default: bool = typer.Option(False, "--default", help="Set as default"),
) -> None:
    """Add a new project.

    Cloud mode examples:
      bm project add research                    # No local sync
      bm project add research --local ~/docs     # With local sync

    Local mode example:
      bm project add research ~/Documents/research
    """
    config = ConfigManager().config

    if config.cloud_mode_enabled:
        # Cloud mode: auto-generate cloud path from name
        async def _add_project():
            async with get_client() as client:
                data = {
                    "name": name,
                    "path": generate_permalink(name),
                    "local_sync_path": local,  # Optional
                    "set_default": set_default,
                }
                response = await call_post(client, "/projects/projects", json=data)
                return ProjectStatusResponse.model_validate(response.json())
    else:
        # Local mode: path is required
        if path is None:
            console.print("[red]Error: path argument is required in local mode[/red]")
            raise typer.Exit(1)

        resolved_path = Path(os.path.abspath(os.path.expanduser(path))).as_posix()

        async def _add_project():
            async with get_client() as client:
                data = {
                    "name": name,
                    "path": resolved_path,
                    "set_default": set_default,
                }
                response = await call_post(client, "/projects/projects", json=data)
                return ProjectStatusResponse.model_validate(response.json())

    # Execute and display result
    result = asyncio.run(_add_project())
    console.print(f"[green]{result.message}[/green]")


@project_app.command("sync-setup")
def setup_project_sync(
    name: str = typer.Argument(..., help="Project name"),
    local_path: str = typer.Argument(..., help="Local sync directory"),
) -> None:
    """Configure local sync for an existing cloud project."""

    config = ConfigManager().config

    if not config.cloud_mode_enabled:
        console.print("[red]Error: sync-setup only available in cloud mode[/red]")
        raise typer.Exit(1)

    resolved_path = Path(os.path.abspath(os.path.expanduser(local_path))).as_posix()

    async def _update_project():
        async with get_client() as client:
            data = {"local_sync_path": resolved_path}
            project_permalink = generate_permalink(name)
            response = await call_patch(
                client,
                f"/projects/{project_permalink}",
                json=data,
            )
            return ProjectStatusResponse.model_validate(response.json())

    result = asyncio.run(_update_project())
    console.print(f"[green]{result.message}[/green]")
    console.print(f"\nLocal sync configured: {resolved_path}")
    console.print(f"\nTo sync: bm project bisync --name {name} --resync")
```

**4.2 New Sync Commands**

```python
# basic_memory/cli/commands/project.py

@project_app.command("sync")
def sync_project(
    name: str = typer.Option(..., "--name", help="Project name"),
    all_projects: bool = typer.Option(False, "--all", help="Sync all projects"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview changes"),
    verbose: bool = typer.Option(False, "--verbose", help="Show detailed output"),
) -> None:
    """One-way sync: local → cloud (make cloud identical to local)."""

    config = ConfigManager().config
    if not config.cloud_mode_enabled:
        console.print("[red]Error: sync only available in cloud mode[/red]")
        raise typer.Exit(1)

    # Get projects to sync
    if all_projects:
        projects = get_all_sync_projects()
    else:
        projects = [get_project_by_name(name)]

    # Get bucket name
    tenant_info = asyncio.run(get_mount_info())
    bucket_name = tenant_info.bucket_name

    # Sync each project
    for project in projects:
        if not project.local_sync_path:
            console.print(f"[yellow]Skipping {project.name}: no local_sync_path[/yellow]")
            continue

        console.print(f"[blue]Syncing {project.name}...[/blue]")
        try:
            project_sync(project, bucket_name, dry_run=dry_run, verbose=verbose)
            console.print(f"[green]✓ {project.name} synced[/green]")
        except RcloneError as e:
            console.print(f"[red]✗ {project.name} failed: {e}[/red]")


@project_app.command("bisync")
def bisync_project(
    name: str = typer.Option(..., "--name", help="Project name"),
    all_projects: bool = typer.Option(False, "--all", help="Bisync all projects"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview changes"),
    resync: bool = typer.Option(False, "--resync", help="Force new baseline"),
    verbose: bool = typer.Option(False, "--verbose", help="Show detailed output"),
) -> None:
    """Two-way sync: local ↔ cloud (bidirectional sync)."""

    # Similar to sync but calls project_bisync()
    pass


@project_app.command("check")
def check_project(
    name: str = typer.Option(..., "--name", help="Project name"),
    one_way: bool = typer.Option(False, "--one-way", help="Check one direction only"),
) -> None:
    """Verify file integrity between local and cloud."""

    # Calls project_check()
    pass


@project_app.command("ls")
def list_project_files(
    name: str = typer.Option(..., "--name", help="Project name"),
    path: str = typer.Argument(None, help="Path within project"),
) -> None:
    """List files in remote project."""

    # Calls project_ls()
    pass
```

**4.3 Update Cloud Setup**

```python
# basic_memory/cli/commands/cloud/core_commands.py

@cloud_app.command("setup")
def cloud_setup() -> None:
    """Set up cloud sync (install rclone and configure credentials)."""

    console.print("[bold blue]Basic Memory Cloud Setup[/bold blue]")
    console.print("Installing rclone and configuring credentials...\n")

    try:
        # Step 1: Install rclone
        console.print("[blue]Step 1: Installing rclone...[/blue]")
        install_rclone()

        # Step 2: Get tenant info
        console.print("\n[blue]Step 2: Getting tenant information...[/blue]")
        tenant_info = asyncio.run(get_mount_info())
        tenant_id = tenant_info.tenant_id

        console.print(f"[green]✓ Tenant: {tenant_id}[/green]")

        # Step 3: Generate credentials
        console.print("\n[blue]Step 3: Generating sync credentials...[/blue]")
        creds = asyncio.run(generate_mount_credentials(tenant_id))

        console.print("[green]✓ Generated credentials[/green]")

        # Step 4: Configure rclone (single remote: bm-cloud)
        console.print("\n[blue]Step 4: Configuring rclone...[/blue]")
        configure_rclone_remote(
            access_key=creds.access_key,
            secret_key=creds.secret_key,
        )

        console.print("\n[bold green]✓ Cloud setup completed![/bold green]")
        console.print("\nNext steps:")
        console.print("  1. Create projects with local sync:")
        console.print("     bm project add research --local ~/Documents/research")
        console.print("\n  2. Or configure sync for existing projects:")
        console.print("     bm project sync-setup research ~/Documents/research")
        console.print("\n  3. Start syncing:")
        console.print("     bm project bisync --name research --resync")

    except Exception as e:
        console.print(f"\n[red]Setup failed: {e}[/red]")
        raise typer.Exit(1)
```

### Phase 5: Cleanup

**5.1 Remove Deprecated Commands**

```python
# Remove from cloud commands:
- cloud mount
- cloud unmount
- cloud mount-status
- bisync-setup
- Individual bisync command (moved to project bisync)

# Remove from root commands:
- bm sync (without project specification)
- bm bisync (without project specification)
```

**5.2 Remove Deprecated Code**

```python
# Files to remove:
- mount_commands.py (entire file)

# Functions to remove from rclone_config.py:
- MOUNT_PROFILES
- get_default_mount_path()
- build_mount_command()
- is_path_mounted()
- get_rclone_processes()
- kill_rclone_process()
- unmount_path()
- cleanup_orphaned_rclone_processes()

# Functions to remove from bisync_commands.py:
- BISYNC_PROFILES (use single default)
- setup_cloud_bisync() (replaced by cloud setup)
- run_bisync_watch() (can add back to project bisync if needed)
- show_bisync_status() (replaced by project list showing sync status)
```

**5.3 Update Configuration Schema**

```python
# Remove from config.json:
- bisync_config (no longer needed)

# The projects array is the source of truth for sync configuration
```

### Phase 6: Documentation Updates

**6.1 Update CLI Documentation**

```markdown
# docs/cloud-cli.md

## Project-Scoped Cloud Sync

Basic Memory cloud sync is project-scoped - each project can optionally be configured with a local working directory that syncs with the cloud.

### Setup (One Time)

1. Authenticate and enable cloud mode:
   ```bash
   bm cloud login
   ```

2. Install rclone and configure credentials:
   ```bash
   bm cloud setup
   ```

### Create Projects with Sync

Create a cloud project with optional local sync:

```bash
# Create project without local sync
bm project add research

# Create project with local sync
bm project add research --local ~/Documents/research
```

Or configure sync for existing (remote) project:

```bash
bm project sync-setup research ~/Documents/research
```

### Syncing Projects

**Two-way sync (recommended):**
```bash
# First time - establish baseline
bm project bisync --name research --resync

# Subsequent syncs
bm project bisync --name research

# Sync all projects with local_sync_path configured
bm project bisync --all
```

**One-way sync (local → cloud):**
```bash
bm project sync --name research
```

**Verify integrity:**
```bash
bm project check --name research
```

### Advanced Operations

**List remote files:**
```bash
bm project ls --name research
bm project ls --name research subfolder
```

**Preview changes before syncing:**
```bash
bm project bisync --name research --dry-run
```

**Verbose output for debugging:**
```bash
bm project bisync --name research --verbose
```

### Project Management

**List projects (shows sync status):**
```bash
bm project list
```

**Update sync path:**
```bash
bm project sync-setup research ~/new/path
```

**Remove project:**
```bash
bm project remove research
```
```

**6.2 Update SPEC-8**

Add to SPEC-8's "Implementation Notes" section:

```markdown
## Superseded by SPEC-20

The initial implementation in SPEC-8 proved too complex with multiple footguns:
- Mount vs bisync workflow confusion
- Multiple profiles creating decision paralysis
- Directory conflicts and auto-discovery errors

SPEC-20 supersedes the sync implementation with a simplified project-scoped approach while keeping the core Tigris infrastructure from SPEC-8.
```

## How to Evaluate

### Success Criteria

**1. Simplified Setup**
- [ ] `bm cloud setup` completes in one command
- [ ] Creates single rclone remote named `bm-cloud`
- [ ] No profile selection required
- [ ] Clear next steps printed after setup

**2. Clear Project Model**
- [ ] Projects can be created with or without local sync
- [ ] `bm project list` shows sync status for each project
- [ ] `local_sync_path` stored in project config
- [ ] Renaming local folder doesn't break sync (config is source of truth)

**3. Working Sync Operations**
- [ ] `bm project sync --name <project>` performs one-way sync
- [ ] `bm project bisync --name <project>` performs two-way sync
- [ ] `bm project check --name <project>` verifies integrity
- [ ] `--all` flag syncs all configured projects
- [ ] `--dry-run` shows changes without applying
- [ ] First bisync requires `--resync` with clear error message

**4. Safety**
- [ ] Cannot sync project without `local_sync_path` configured
- [ ] Bisync state is per-project (not global)
- [ ] `.bmignore` patterns respected
- [ ] Max delete safety (25 files) prevents accidents
- [ ] Clear error messages for all failure modes

**5. Clean Removal**
- [ ] Mount commands removed
- [ ] Profile selection removed
- [ ] Global sync directory removed (`~/basic-memory-cloud-sync/`)
- [ ] Auto-discovery removed
- [ ] Convenience commands (`bm sync`) removed

**6. Documentation**
- [ ] Updated cloud-cli.md with new workflow
- [ ] Clear examples for common operations
- [ ] Migration guide for existing users
- [ ] Troubleshooting section

### Test Scenarios

**Scenario 1: New User Setup**
```bash
# Start fresh
bm cloud login
bm cloud setup
bm project add research --local ~/docs/research
bm project bisync --name research --resync
# Edit files locally
bm project bisync --name research
# Verify changes synced
```

**Scenario 2: Multiple Projects**
```bash
bm project add work --local ~/work
bm project add personal --local ~/personal
bm project bisync --all --resync
# Edit files in both projects
bm project bisync --all
```

**Scenario 3: Project Without Sync**
```bash
bm project add temp-notes
# Try to sync (should fail gracefully)
bm project bisync --name temp-notes
# Should see: "Project temp-notes has no local_sync_path configured"
```

**Scenario 4: Integrity Check**
```bash
bm project bisync --name research
# Manually edit file in cloud UI
bm project check --name research
# Should report differences
bm project bisync --name research
# Should sync changes back to local
```

**Scenario 5: Safety Features**
```bash
# Delete 30 files locally
bm project sync --name research
# Should fail with max delete error
# User reviews and confirms
bm project sync --name research  # After confirming
```

### Performance Targets

- Setup completes in < 30 seconds
- Single project sync < 5 seconds for small changes
- Bisync initialization (--resync) < 10 seconds for typical project
- Batch sync (--all) processes N projects in N*5 seconds

### Breaking Changes

This is a **breaking change** from SPEC-8 implementation:

**Migration Required:**
- Users must run `bm cloud setup` again
- Existing `~/basic-memory-cloud-sync/` directory abandoned
- Projects must be configured with `local_sync_path`
- Mount users must switch to bisync workflow

**Migration Guide:**
```bash
# 1. Note current project locations
bm project list

# 2. Re-run setup
bm cloud setup

# 3. Configure sync for each project
bm project sync-setup research ~/Documents/research
bm project sync-setup work ~/work

# 4. Establish baselines
bm project bisync --all --resync

# 5. Old directory can be deleted
rm -rf ~/basic-memory-cloud-sync/
```

## Dependencies

- **SPEC-8**: TigrisFS Integration (bucket provisioning, credentials)
- Python 3.12+
- rclone 1.64.0+
- Typer (CLI framework)
- Rich (console output)

## Risks

**Risk 1: User Confusion from Breaking Changes**
- Mitigation: Clear migration guide, version bump (0.16.0)
- Mitigation: Detect old config and print migration instructions

**Risk 2: Per-Project Bisync State Complexity**
- Mitigation: Use rclone's `--workdir` to isolate state per project
- Mitigation: Store in `~/.basic-memory/bisync-state/{project_name}/`

**Risk 3: Batch Operations Performance**
- Mitigation: Run syncs sequentially with progress indicators
- Mitigation: Add `--parallel` flag in future if needed

**Risk 4: Lost Features (Mount)**
- Mitigation: Document mount as experimental/advanced feature
- Mitigation: Can restore if users request it

## Open Questions

1. **Should we keep mount as experimental command?**
   - Lean toward: Remove entirely, focus on bisync
   - Alternative: Keep as `bm project mount --name <project>` (advanced)

   - Answer: remove

2. **Batch sync order?**
   - Alphabetical by project name?
   - By last modified time?
   - Let user specify order?
   - answer: project order from api or config

3. **Credential refresh?**
   - Auto-detect expired credentials and re-run credential generation?
   - Or require manual `bm cloud setup` again?
   - answer: manual setup is fine

4. **Watch mode for projects?**
   - `bm project bisync --name research --watch`?
   - Or removed entirely (users can use OS tools)?
   - answer: remove for now: we can add it back later if it's useful

5. **Project path validation?**
   - Ensure `local_path` exists before allowing bisync?
   - Or let rclone error naturally?
   - answer: create if needed, exists is ok

## Implementation Checklist

### Phase 1: Config Schema (1-2 days) ✅
- [x] Add `CloudProjectConfig` model to `basic_memory/config.py`
- [x] Add `cloud_projects: dict[str, CloudProjectConfig]` to Config model
- [x] Test config loading/saving with new schema
- [x] Handle migration from old config format

### Phase 2: Rclone Config Simplification (1 day)
- [ ] Update `configure_rclone_remote()` to use `basic-memory-cloud` as remote name
- [ ] Remove `add_tenant_to_rclone_config()` (replaced by configure_rclone_remote)
- [ ] Remove tenant_id from remote naming
- [ ] Test rclone config generation

### Phase 3: Project-Scoped Rclone Commands (2-3 days)
- [ ] Create `src/basic_memory/cli/commands/cloud/rclone_commands.py`
- [ ] Implement `get_project_remote(project, bucket_name)`
- [ ] Implement `project_sync()` (one-way: local → cloud)
- [ ] Implement `project_bisync()` (two-way: local ↔ cloud)
- [ ] Implement `project_check()` (integrity verification)
- [ ] Implement `project_ls()` (list remote files)
- [ ] Add helper: `get_project_bisync_state(project_name)`
- [ ] Add helper: `bisync_initialized(project_name)`
- [ ] Write unit tests for rclone commands

### Phase 4: CLI Integration (2-3 days)
- [ ] Update `project.py`: Add `--local` flag to `project add` command
- [ ] Update `project.py`: Create `project sync-setup` command
- [ ] Create `project.py`: Add `project sync` command
- [ ] Create `project.py`: Add `project bisync` command
- [ ] Create `project.py`: Add `project check` command
- [ ] Create `project.py`: Add `project ls` command
- [ ] Update `project list` to show sync status
- [ ] Update `cloud/core_commands.py`: Simplify `cloud setup` command
- [ ] Add helper functions: `get_all_sync_projects()`, `get_project_by_name()`
- [ ] Write integration tests for new commands

### Phase 5: Cleanup (1 day)
- [ ] Remove `mount_commands.py` (entire file)
- [ ] Remove mount-related functions from `rclone_config.py`:
  - [ ] `MOUNT_PROFILES`
  - [ ] `get_default_mount_path()`
  - [ ] `build_mount_command()`
  - [ ] `is_path_mounted()`
  - [ ] `get_rclone_processes()`
  - [ ] `kill_rclone_process()`
  - [ ] `unmount_path()`
  - [ ] `cleanup_orphaned_rclone_processes()`
- [ ] Remove from `bisync_commands.py`:
  - [ ] `BISYNC_PROFILES` (use single default)
  - [ ] `setup_cloud_bisync()`
  - [ ] `run_bisync_watch()`
  - [ ] `show_bisync_status()`
- [ ] Remove `bisync_config` from config schema
- [ ] Remove deprecated cloud commands:
  - [ ] `cloud mount`
  - [ ] `cloud unmount`
  - [ ] `cloud mount-status`
  - [ ] `cloud bisync-setup`
- [ ] Remove convenience commands:
  - [ ] Root-level `bm sync` (without project)
  - [ ] Root-level `bm bisync` (without project)
- [ ] Update tests to remove references to deprecated functionality

### Phase 6: Documentation (1 day)
- [ ] Update `docs/cloud-cli.md` with new workflow
- [ ] Add migration guide for existing users
- [ ] Update command reference
- [ ] Add troubleshooting section
- [ ] Update SPEC-8 with "Superseded by SPEC-20" note
- [ ] Add examples for common workflows

### Testing & Validation (1 day)
- [ ] Test Scenario 1: New user setup
- [ ] Test Scenario 2: Multiple projects
- [ ] Test Scenario 3: Project without sync
- [ ] Test Scenario 4: Integrity check
- [ ] Test Scenario 5: Safety features (max delete)
- [ ] Verify performance targets (setup < 30s, sync < 5s)
- [ ] Test migration from SPEC-8 implementation

## Implementation Timeline

**Total: ~10-12 days**
- Phase 1 (Config Schema): 1-2 days
- Phase 2 (Rclone Config): 1 day
- Phase 3 (Rclone Commands): 2-3 days
- Phase 4 (CLI Integration): 2-3 days
- Phase 5 (Cleanup): 1 day
- Phase 6 (Documentation): 1 day
- Testing & Validation: 1 day

## Future Enhancements (Out of Scope)

- **Per-project rclone profiles**: Allow advanced users to override defaults
- **Conflict resolution UI**: Interactive conflict resolution for bisync
- **Sync scheduling**: Automatic periodic sync without watch mode
- **Sync analytics**: Track sync frequency, data transferred, etc.
- **Multi-machine coordination**: Detect and warn about concurrent edits from different machines
