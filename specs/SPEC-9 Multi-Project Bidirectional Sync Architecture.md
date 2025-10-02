---
title: 'SPEC-9: Multi-Project Bidirectional Sync Architecture'
type: spec
permalink: specs/spec-9-multi-project-bisync
tags:
- cloud
- bisync
- architecture
- multi-project
---

# SPEC-9: Multi-Project Bidirectional Sync Architecture

## Status: ✅ Implementation Complete

**Completed Phases:**
- ✅ Phase 1: Cloud Mode Toggle & Config
- ✅ Phase 2: Bisync Updates (Multi-Project)
- ✅ Phase 3: Sync Command Dual Mode
- ✅ Phase 4: Remove Duplicate Commands & Cloud Mode Auth
- ✅ Phase 5: Mount Updates
- ✅ Phase 6: Safety & Validation
- ⏸️ Phase 7: Cloud-Side Implementation (Deferred to cloud repo)
- ✅ Phase 8.1: Testing (All test scenarios validated)
- ✅ Phase 8.2: Documentation (Core docs complete, demos pending)

**Key Achievements:**
- Unified CLI: `bm sync`, `bm project`, `bm tool` work transparently in both local and cloud modes
- Multi-project sync: Single `bm sync` operation handles all projects bidirectionally
- Cloud mode toggle: `bm cloud login` / `bm cloud logout` switches modes seamlessly
- Integrity checking: `bm cloud check` verifies file matching without data transfer
- Directory isolation: Mount and bisync use separate directories with conflict prevention
- Clean UX: No RCLONE_TEST files, clear error messages, transparent implementation

## Why

**Current State:**
SPEC-8 implemented rclone bisync for cloud file synchronization, but has several architectural limitations:
1. Syncs only a single project subdirectory (`bucket:/basic-memory`)
2. Requires separate `bm cloud` command namespace, duplicating existing CLI commands
3. Users must learn different commands for local vs cloud operations
4. RCLONE_TEST marker files clutter user directories

**Problems:**
1. **Duplicate Commands**: `bm project` vs `bm cloud project`, `bm tool` vs (no cloud equivalent)
2. **Inconsistent UX**: Same operations require different command syntax depending on mode
3. **Single Project Sync**: Users can only sync one project at a time
4. **Manual Coordination**: Creating new projects requires manual coordination between local and cloud
5. **Confusing Artifacts**: RCLONE_TEST marker files confuse users

**Goals:**
- **Unified CLI**: All existing `bm` commands work in both local and cloud mode via toggle
- **Multi-Project Sync**: Single sync operation handles all projects bidirectionally
- **Simple Mode Switch**: `bm cloud login` enables cloud mode, `logout` returns to local
- **Automatic Registration**: Projects auto-register on both local and cloud sides
- **Clean UX**: Remove unnecessary safety checks and confusing artifacts

## Cloud Access Paradigm: The Dropbox Model

**Mental Model Shift:**

Basic Memory cloud access follows the **Dropbox/iCloud paradigm** - not a per-project cloud connection model.

**What This Means:**

```
Traditional Project-Based Model (❌ Not This):
  bm cloud mount --project work      # Mount individual project
  bm cloud mount --project personal  # Mount another project
  bm cloud sync --project research   # Sync specific project
  → Multiple connections, multiple credentials, complex management

Dropbox Model (✅ This):
  bm cloud mount                     # One mount, all projects
  bm sync                            # One sync, all projects
  ~/basic-memory-cloud/              # One folder, all content
  → Single connection, organized by folders (projects)
```

**Key Principles:**

1. **Mount/Bisync = Access Methods, Not Project Tools**
   - Mount: Read-through cache to cloud (like Dropbox folder)
   - Bisync: Bidirectional sync with cloud (like Dropbox sync)
   - Both operate at **bucket level** (all projects)

2. **Projects = Organization Within Cloud Space**
   - Projects are folders within your cloud storage
   - Creating a folder creates a project (auto-discovered)
   - Projects are managed via `bm project` commands

3. **One Cloud Space Per Machine**
   - One set of IAM credentials per tenant
   - One mount point: `~/basic-memory-cloud/`
   - One bisync directory: `~/basic-memory-cloud-sync/` (default)
   - All projects accessible through this single entry point

4. **Why This Works Better**
   - **Credential Management**: One credential set, not N sets per project
   - **Resource Efficiency**: One rclone process, not N processes
   - **Familiar Pattern**: Users already understand Dropbox/iCloud
   - **Operational Simplicity**: `mount` once, `unmount` once
   - **Scales Naturally**: Add projects by creating folders, not reconfiguring cloud access

**User Journey:**

```bash
# Setup cloud access (once)
bm cloud login
bm cloud mount  # or: bm cloud setup for bisync

# Work with projects (create folders as needed)
cd ~/basic-memory-cloud/
mkdir my-new-project
echo "# Notes" > my-new-project/readme.md

# Cloud auto-discovers and registers project
# No additional cloud configuration needed
```

This paradigm shift means **mount and bisync are infrastructure concerns**, while **projects are content organization**. Users think about their knowledge, not about cloud plumbing.

## What

This spec affects:

1. **Cloud Mode Toggle** (`config.py`, `async_client.py`):
   - Add `cloud_mode` flag to `~/.basic-memory/config.json`
   - Set/unset `BASIC_MEMORY_PROXY_URL` based on cloud mode
   - `bm cloud login` enables cloud mode, `logout` disables it
   - All CLI commands respect cloud mode via existing async_client

2. **Unified CLI Commands**:
   - **Remove**: `bm cloud project` commands (duplicate of `bm project`)
   - **Enhance**: `bm sync` co-opted for bisync in cloud mode
   - **Keep**: `bm cloud login/logout/status/setup` for mode management
   - **Result**: `bm project`, `bm tool`, `bm sync` work in both modes

3. **Bisync Integration** (`bisync_commands.py`):
   - Remove `--check-access` (no RCLONE_TEST files)
   - Sync bucket root (all projects), not single subdirectory
   - Project auto-registration before sync
   - `bm sync` triggers bisync in cloud mode
   - `bm sync --watch` for continuous sync

4. **Config Structure**:
   ```json
   {
     "cloud_mode": true,
     "cloud_host": "https://cloud.basicmemory.com",
     "auth_tokens": {...},
     "bisync_config": {
       "profile": "balanced",
       "sync_dir": "~/basic-memory-cloud-sync"
     }
   }
   ```

5. **User Workflows**:
   - **Enable cloud**: `bm cloud login` → all commands work remotely
   - **Create projects**: `bm project add "name"` creates on cloud
   - **Sync files**: `bm sync` runs bisync (all projects)
   - **Use tools**: `bm tool write-note` creates notes on cloud
   - **Disable cloud**: `bm cloud logout` → back to local mode

## Implementation Tasks

### Phase 1: Cloud Mode Toggle & Config (Foundation) ✅

**1.1 Update Config Schema**
- [x] Add `cloud_mode: bool = False` to Config model
- [x] Add `bisync_config: dict` with `profile` and `sync_dir` fields
- [x] Ensure `cloud_host` field exists
- [x] Add config migration for existing users (defaults handle this)

**1.2 Update async_client.py**
- [x] Read `cloud_mode` from config (not just environment)
- [x] Set `BASIC_MEMORY_PROXY_URL` from config when `cloud_mode=true`
- [x] Priority: env var > config.cloud_host (if cloud_mode) > None (local ASGI)
- [ ] Test both local and cloud mode routing

**1.3 Update Login/Logout Commands**
- [x] `bm cloud login`: Set `cloud_mode=true` and save config
- [x] `bm cloud login`: Set `BASIC_MEMORY_PROXY_URL` environment variable
- [x] `bm cloud logout`: Set `cloud_mode=false` and save config
- [x] `bm cloud logout`: Clear `BASIC_MEMORY_PROXY_URL` environment variable
- [x] `bm cloud status`: Show current mode (local/cloud), connection status

**1.4 Skip Initialization in Cloud Mode** ✅
- [x] Update `ensure_initialization()` to check `cloud_mode` and return early
- [x] Document that `config.projects` is only used in local mode
- [x] Cloud manages its own projects via API, no local reconciliation needed

### Phase 2: Bisync Updates (Multi-Project)

**2.1 Remove RCLONE_TEST Files** ✅
- [x] Update all bisync profiles: `check_access=False`
- [x] Remove RCLONE_TEST creation from `setup_cloud_bisync()`
- [x] Remove RCLONE_TEST upload logic
- [ ] Update documentation

**2.2 Sync Bucket Root (All Projects)** ✅
- [x] Change remote path from `bucket:/basic-memory` to `bucket:/` in `build_bisync_command()`
- [x] Update `setup_cloud_bisync()` to use bucket root
- [ ] Test with multiple projects

**2.3 Project Auto-Registration (Bisync)** ✅
- [x] Add `fetch_cloud_projects()` function (GET /proxy/projects/projects)
- [x] Add `scan_local_directories()` function
- [x] Add `create_cloud_project()` function (POST /proxy/projects/projects)
- [x] Integrate into `run_bisync()`: fetch → scan → create missing → sync
- [x] Wait for API 201 response before syncing

**2.4 Bisync Directory Configuration** ✅
- [x] Add `--dir` parameter to `bm cloud bisync-setup`
- [x] Store bisync directory in config
- [x] Default to `~/basic-memory-cloud-sync/`
- [x] Add `validate_bisync_directory()` safety check
- [x] Update `get_default_mount_path()` to return fixed `~/basic-memory-cloud/`

**2.5 Sync/Status API Infrastructure** ✅ (commit d48b1dc)
- [x] Create `POST /{project}/project/sync` endpoint for background sync
- [x] Create `POST /{project}/project/status` endpoint for scan-only status
- [x] Create `SyncReportResponse` Pydantic schema
- [x] Refactor CLI `sync` command to use API endpoint
- [x] Refactor CLI `status` command to use API endpoint
- [x] Create `command_utils.py` with shared `run_sync()` function
- [x] Update `notify_container_sync()` to call `run_sync()` for each project
- [x] Update all tests to match new API-based implementation

### Phase 3: Sync Command Dual Mode ✅

**3.1 Update `bm sync` Command** ✅
- [x] Check `config.cloud_mode` at start
- [x] If `cloud_mode=false`: Run existing local sync
- [x] If `cloud_mode=true`: Run bisync
- [x] Add `--watch` parameter for continuous sync
- [x] Add `--interval` parameter (default 60 seconds)
- [x] Error if `--watch` used in local mode with helpful message

**3.2 Watch Mode for Bisync** ✅
- [x] Implement `run_bisync_watch()` with interval loop
- [x] Add `--interval` parameter (default 60 seconds)
- [x] Handle errors gracefully, continue on failure
- [x] Show sync progress and status

**3.3 Integrity Check Command** ✅
- [x] Implement `bm cloud check` command using `rclone check`
- [x] Read-only operation that verifies file matching
- [x] Error with helpful messages if rclone/bisync not set up
- [x] Support `--one-way` flag for faster checks
- [x] Transparent about rclone implementation
- [x] Suggest `bm sync` to resolve differences

**Implementation Notes:**
- `bm sync` adapts to cloud mode automatically - users don't need separate commands
- `bm cloud bisync` kept for power users with full options (--dry-run, --resync, --profile, --verbose)
- `bm cloud check` provides integrity verification without transferring data
- Design philosophy: Simplicity for everyday use, transparency about implementation

### Phase 4: Remove Duplicate Commands & Cloud Mode Auth ✅

**4.0 Cloud Mode Authentication** ✅
- [x] Update `async_client.py` to support dual auth sources
- [x] FastMCP context auth (cloud service mode) via `inject_auth_header()`
- [x] JWT token file auth (CLI cloud mode) via `CLIAuth.get_valid_token()`
- [x] Automatic token refresh for CLI cloud mode
- [x] Remove `BASIC_MEMORY_PROXY_URL` environment variable dependency
- [x] Simplify to use only `config.cloud_mode` + `config.cloud_host`

**4.1 Delete `bm cloud project` Commands** ✅
- [x] Remove `bm cloud project list` (use `bm project list`)
- [x] Remove `bm cloud project add` (use `bm project add`)
- [x] Update `core_commands.py` to remove project_app subcommands
- [x] Keep only: `login`, `logout`, `status`, `setup`, `mount`, `unmount`, bisync commands
- [x] Remove unused imports (Table, generate_permalink, os)
- [x] Clean up environment variable references in login/logout

**4.2 CLI Command Cloud Mode Integration** ✅
- [x] Add runtime `cloud_mode_enabled` checks to all CLI commands
- [x] Update `list_projects()` to conditionally authenticate based on cloud mode
- [x] Update `remove_project()` to conditionally authenticate based on cloud mode
- [x] Update `run_sync()` to conditionally authenticate based on cloud mode
- [x] Update `get_project_info()` to conditionally authenticate based on cloud mode
- [x] Update `run_status()` to conditionally authenticate based on cloud mode
- [x] Remove auth from `set_default_project()` (local-only command, no cloud version)
- [x] Create CLI integration tests (`test-int/cli/`) to validate both local and cloud modes
- [x] Replace mock-heavy CLI tests with integration tests (deleted 5 mock test files)

**4.3 OAuth Authentication Fixes** ✅
- [x] Restore missing `SettingsConfigDict` in `BasicMemoryConfig`
- [x] Fix environment variable reading with `BASIC_MEMORY_` prefix
- [x] Fix `.env` file loading
- [x] Fix extra field handling for config files
- [x] Resolve `bm cloud login` OAuth failure ("Something went wrong" error)
- [x] Implement PKCE (Proof Key for Code Exchange) for device flow
- [x] Generate code verifier and SHA256 challenge for device authorization
- [x] Send code_verifier with token polling requests
- [x] Support both PKCE-required and PKCE-optional OAuth clients
- [x] Verify authentication flow works end-to-end with staging and production
- [x] Document WorkOS requirement: redirect URI must be configured even for device flow

**4.4 Update Documentation**
- [ ] Update `cloud-cli.md` with cloud mode toggle workflow
- [ ] Document `bm cloud login` → use normal commands
- [ ] Add examples of cloud mode usage
- [ ] Document mount vs bisync directory isolation
- [ ] Add troubleshooting section

### Phase 5: Mount Updates ✅

**5.1 Fixed Mount Directory** ✅
- [x] Change mount path to `~/basic-memory-cloud/` (fixed, no tenant ID)
- [x] Update `get_default_mount_path()` function
- [x] Remove configurability (fixed location)
- [x] Update mount commands to use new path

**5.2 Mount at Bucket Root** ✅
- [x] Ensure mount uses bucket root (not subdirectory)
- [x] Test with multiple projects
- [x] Verify all projects visible in mount

**Implementation:** Mount uses fixed `~/basic-memory-cloud/` directory and syncs entire bucket root `basic-memory-{tenant_id}:{bucket_name}` for all projects.

### Phase 6: Safety & Validation ✅

**6.1 Directory Conflict Prevention** ✅
- [x] Implement `validate_bisync_directory()` check
- [x] Detect if bisync dir == mount dir
- [x] Detect if bisync dir is currently mounted
- [x] Show clear error messages with solutions

**6.2 State Management** ✅
- [x] Use `--workdir` for bisync state
- [x] Store state in `~/.basic-memory/bisync-state/{tenant-id}/`
- [x] Ensure state directory created before bisync

**Implementation:** `validate_bisync_directory()` prevents conflicts by checking directory equality and mount status. State managed in isolated `~/.basic-memory/bisync-state/{tenant-id}/` directory using `--workdir` flag.

### Phase 7: Cloud-Side Implementation (Deferred to Cloud Repo)

**7.1 Project Discovery Service (Cloud)** - Deferred
- [ ] Create `ProjectDiscoveryService` background job
- [ ] Scan `/app/data/` every 2 minutes
- [ ] Auto-register new directories as projects
- [ ] Log discovery events
- [ ] Handle errors gracefully

**7.2 Project API Updates (Cloud)** - Deferred
- [ ] Ensure `POST /proxy/projects/projects` creates directory synchronously
- [ ] Return 201 with project details
- [ ] Ensure directory ready immediately after creation

**Note:** Phase 7 is cloud-side work that belongs in the basic-memory-cloud repository. The CLI-side implementation (Phase 2.3 auto-registration) is complete and working - it calls the existing cloud API endpoints.

### Phase 8: Testing & Documentation

**8.1 Test Scenarios**
- [x] Test: Cloud mode toggle (login/logout)
- [x] Test: Local-first project creation (bisync)
- [x] Test: Cloud-first project creation (API)
- [x] Test: Multi-project bidirectional sync
- [x] Test: MCP tools in cloud mode
- [x] Test: Watch mode continuous sync
- [x] Test: Safety profile protection (max_delete implemented)
- [x] Test: No RCLONE_TEST files (check_access=False in all profiles)
- [x] Test: Mount/bisync directory isolation (validate_bisync_directory)
- [x] Test: Integrity check command (bm cloud check)

**8.2 Documentation**
- [x] Update cloud-cli.md with cloud mode instructions
- [x] Document Dropbox model paradigm
- [x] Update command reference with new commands
- [x] Document `bm sync` dual mode behavior
- [x] Document `bm cloud check` command
- [x] Document directory structure and fixed paths
- [ ] Update README with quick start
- [ ] Create migration guide for existing users
- [ ] Create video/GIF demos

### Success Criteria Checklist

- [x] `bm cloud login` enables cloud mode for all commands
- [x] `bm cloud logout` reverts to local mode
- [x] `bm project`, `bm tool`, `bm sync` work transparently in both modes
- [x] `bm sync` runs bisync in cloud mode, local sync in local mode
- [x] Single sync operation handles all projects bidirectionally
- [x] Local directories auto-create cloud projects via API
- [x] Cloud projects auto-sync to local directories
- [x] No RCLONE_TEST files in user directories
- [x] Bisync profiles provide safety via `max_delete` limits
- [x] `bm sync --watch` enables continuous sync
- [x] No duplicate `bm cloud project` commands (removed)
- [x] `bm cloud check` command for integrity verification
- [ ] Documentation covers cloud mode toggle and workflows
- [ ] Edge cases handled gracefully with clear errors

## How (High Level)

### Architecture Overview

**Cloud Mode Toggle:**
```
┌─────────────────────────────────────┐
│  bm cloud login                     │
│  ├─ Authenticate via OAuth          │
│  ├─ Set cloud_mode: true in config  │
│  └─ Set BASIC_MEMORY_PROXY_URL      │
└─────────────────────────────────────┘
           ↓
┌─────────────────────────────────────┐
│  All CLI commands use async_client  │
│  ├─ async_client checks proxy URL   │
│  ├─ If set: HTTP to cloud           │
│  └─ If not: Local ASGI              │
└─────────────────────────────────────┘
           ↓
┌─────────────────────────────────────┐
│  bm project add "work"              │
│  bm tool write-note ...             │
│  bm sync (triggers bisync)          │
│  → All work against cloud           │
└─────────────────────────────────────┘
```

**Storage Hierarchy:**
```
Cloud Container:                   Bucket:                      Local Sync Dir:
/app/data/ (mounted) ←→ production-tenant-{id}/ ←→ ~/basic-memory-cloud-sync/
├── basic-memory/               ├── basic-memory/               ├── basic-memory/
│   ├── notes/                  │   ├── notes/                  │   ├── notes/
│   └── concepts/               │   └── concepts/               │   └── concepts/
├── work-project/               ├── work-project/               ├── work-project/
│   └── tasks/                  │   └── tasks/                  │   └── tasks/
└── personal/                   └── personal/                   └── personal/
    └── journal/                    └── journal/                    └── journal/

Bidirectional sync via rclone bisync
```

### Sync Flow

**`bm sync` execution (in cloud mode):**

1. **Check cloud mode**
   ```python
   if not config.cloud_mode:
       # Run normal local file sync
       run_local_sync()
       return

   # Cloud mode: Run bisync
   ```

2. **Fetch cloud projects**
   ```python
   # GET /proxy/projects/projects (via async_client)
   cloud_projects = fetch_cloud_projects()
   cloud_project_names = {p["name"] for p in cloud_projects["projects"]}
   ```

3. **Scan local sync directory**
   ```python
   sync_dir = config.bisync_config["sync_dir"]  # ~/basic-memory-cloud-sync
   local_dirs = [d.name for d in sync_dir.iterdir()
                 if d.is_dir() and not d.name.startswith('.')]
   ```

4. **Create missing cloud projects**
   ```python
   for dir_name in local_dirs:
       if dir_name not in cloud_project_names:
           # POST /proxy/projects/projects (via async_client)
           create_cloud_project(name=dir_name)
           # Blocks until 201 response
   ```

5. **Run bisync on bucket root**
   ```bash
   rclone bisync \
     ~/basic-memory-cloud-sync \
     basic-memory-{tenant}:{bucket} \
     --filters-file ~/.basic-memory/.bmignore.rclone \
     --conflict-resolve=newer \
     --max-delete=25
   # Syncs ALL project subdirectories bidirectionally
   ```

6. **Notify cloud to refresh** (commit d48b1dc)
   ```python
   # After rclone bisync completes, sync each project's database
   for project in cloud_projects:
       # POST /{project}/project/sync (via async_client)
       # Triggers background sync for this project
       await run_sync(project=project_name)
   ```

### Key Changes

**1. Cloud Mode via Config**

**Config changes:**
```python
class Config:
    cloud_mode: bool = False
    cloud_host: str = "https://cloud.basicmemory.com"
    bisync_config: dict = {
        "profile": "balanced",
        "sync_dir": "~/basic-memory-cloud-sync"
    }
```

**async_client.py behavior:**
```python
def create_client() -> AsyncClient:
    # Check config first, then environment
    config = ConfigManager().config
    proxy_url = os.getenv("BASIC_MEMORY_PROXY_URL") or \
                (config.cloud_host if config.cloud_mode else None)

    if proxy_url:
        return AsyncClient(base_url=proxy_url)  # HTTP to cloud
    else:
        return AsyncClient(transport=ASGITransport(...))  # Local ASGI
```

**2. Login/Logout Sets Cloud Mode**

```python
# bm cloud login
async def login():
    # Existing OAuth flow...
    success = await auth.login()
    if success:
        config.cloud_mode = True
        config.save()
        os.environ["BASIC_MEMORY_PROXY_URL"] = config.cloud_host
```

```python
# bm cloud logout
def logout():
    config.cloud_mode = False
    config.save()
    os.environ.pop("BASIC_MEMORY_PROXY_URL", None)
```

**3. Remove Duplicate Commands**

**Delete:**
- `bm cloud project list` → use `bm project list`
- `bm cloud project add` → use `bm project add`

**Keep:**
- `bm cloud login` - Enable cloud mode
- `bm cloud logout` - Disable cloud mode
- `bm cloud status` - Show current mode & connection
- `bm cloud setup` - Initial bisync setup
- `bm cloud bisync` - Power-user command with full options
- `bm cloud check` - Verify file integrity between local and cloud

**4. Sync Command Dual Mode**

```python
# bm sync
def sync_command(watch: bool = False, profile: str = "balanced"):
    config = ConfigManager().config

    if config.cloud_mode:
        # Run bisync for cloud sync
        run_bisync(profile=profile, watch=watch)
    else:
        # Run local file sync
        run_local_sync()
```

**5. Remove RCLONE_TEST Files**

```python
# All profiles: check_access=False
BISYNC_PROFILES = {
    "safe": RcloneBisyncProfile(check_access=False, max_delete=10),
    "balanced": RcloneBisyncProfile(check_access=False, max_delete=25),
    "fast": RcloneBisyncProfile(check_access=False, max_delete=50),
}
```

**6. Sync Bucket Root (All Projects)**

```python
# Sync entire bucket, not subdirectory
rclone_remote = f"basic-memory-{tenant_id}:{bucket_name}"
```

## How to Evaluate

### Test Scenarios

**1. Cloud Mode Toggle**
```bash
# Start in local mode
bm project list
# → Shows local projects

# Enable cloud mode
bm cloud login
# → Authenticates, sets cloud_mode=true

bm project list
# → Now shows cloud projects (same command!)

# Disable cloud mode
bm cloud logout

bm project list
# → Back to local projects
```

**Expected:** ✅ Single command works in both modes

**2. Local-First Project Creation (Cloud Mode)**
```bash
# Enable cloud mode
bm cloud login

# Create new project locally in sync dir
mkdir ~/basic-memory-cloud-sync/my-research
echo "# Research Notes" > ~/basic-memory-cloud-sync/my-research/index.md

# Run sync (triggers bisync in cloud mode)
bm sync

# Verify:
# - Cloud project created automatically via API
# - Files synced to bucket:/my-research/
# - Cloud database updated
# - `bm project list` shows new project
```

**Expected:** ✅ Project visible in cloud project list

**3. Cloud-First Project Creation**
```bash
# In cloud mode
bm project add "work-notes"
# → Creates project on cloud (via async_client HTTP)

# Run sync
bm sync

# Verify:
# - Local directory ~/basic-memory-cloud-sync/work-notes/ created
# - Files sync bidirectionally
# - Can use `bm tool write-note` to add content remotely
```

**Expected:** ✅ Project accessible via all CLI commands

**4. Multi-Project Bidirectional Sync**
```bash
# Setup: 3 projects in cloud mode
# Modify files in all 3 locally and remotely

bm sync

# Verify:
# - All 3 projects sync simultaneously
# - Changes propagate correctly
# - No cross-project interference
```

**Expected:** ✅ All projects in sync state

**5. MCP Tools Work in Cloud Mode**
```bash
# In cloud mode
bm tool write-note \
  --title "Meeting Notes" \
  --folder "work-notes" \
  --content "Discussion points..."

# Verify:
# - Note created on cloud (via async_client HTTP)
# - Next `bm sync` pulls note to local
# - Note appears in ~/basic-memory-cloud-sync/work-notes/
```

**Expected:** ✅ Tools work transparently in cloud mode

**6. Watch Mode Continuous Sync**
```bash
# In cloud mode
bm sync --watch

# While running:
# - Create local folder → auto-creates cloud project
# - Edit files locally → syncs to cloud
# - Edit files remotely → syncs to local
# - Create project via API → appears locally

# Verify:
# - Continuous bidirectional sync
# - New projects handled automatically
# - No manual intervention needed
```

**Expected:** ✅ Seamless continuous sync

**7. Safety Profile Protection**
```bash
# Create project with 15 files locally
# Delete project from cloud (simulate error)

bm sync --profile safe

# Verify:
# - Bisync detects 15 pending deletions
# - Exceeds max_delete=10 limit
# - Aborts with clear error
# - No files deleted locally
```

**Expected:** ✅ Safety limit prevents data loss

**8. No RCLONE_TEST Files**
```bash
# After setup and multiple syncs
ls -la ~/basic-memory-cloud-sync/

# Verify:
# - No RCLONE_TEST files
# - No .rclone state files (in ~/.basic-memory/bisync-state/)
# - Clean directory structure
```

**Expected:** ✅ User directory stays clean

### Success Criteria

- [x] `bm cloud login` enables cloud mode for all commands
- [x] `bm cloud logout` reverts to local mode
- [x] `bm project`, `bm tool`, `bm sync` work in both modes transparently
- [x] `bm sync` runs bisync in cloud mode, local sync in local mode
- [x] Single sync operation handles all projects bidirectionally
- [x] Local directories auto-create cloud projects via API
- [x] Cloud projects auto-sync to local directories
- [x] No RCLONE_TEST files in user directories
- [x] Bisync profiles provide safety via `max_delete` limits
- [x] `bm sync --watch` enables continuous sync
- [x] No duplicate `bm cloud project` commands (removed)
- [x] `bm cloud check` command for integrity verification
- [ ] Documentation covers cloud mode toggle and workflows
- [ ] Edge cases handled gracefully with clear errors

## Notes

### API Contract

**Cloud must provide:**

1. **Project Management APIs:**
   - `GET /proxy/projects/projects` - List all projects
   - `POST /proxy/projects/projects` - Create project synchronously
   - `POST /proxy/sync` - Trigger cache refresh

2. **Project Discovery Service (Background):**
   - **Purpose**: Auto-register projects created via mount, direct bucket uploads, or any non-API method
   - **Interval**: Every 2 minutes
   - **Behavior**:
     - Scan `/app/data/` for directories
     - Register any directory not already in project database
     - Log discovery events
   - **Implementation**:
     ```python
     class ProjectDiscoveryService:
         """Background service to auto-discover projects from filesystem."""

         async def run(self):
             """Scan /app/data/ and register new project directories."""
             data_path = Path("/app/data")

             for dir_path in data_path.iterdir():
                 # Skip hidden and special directories
                 if not dir_path.is_dir() or dir_path.name.startswith('.'):
                     continue

                 project_name = dir_path.name

                 # Check if project already registered
                 project = await self.project_repo.get_by_name(project_name)
                 if not project:
                     # Auto-register new project
                     await self.project_repo.create(
                         name=project_name,
                         path=str(dir_path)
                     )
                     logger.info(f"Auto-discovered project: {project_name}")
     ```

**Project Creation (API-based):**
- API creates `/app/data/{project-name}/` directory
- Registers project in database
- Returns 201 with project details
- Directory ready for bisync immediately

**Project Creation (Discovery-based):**
- User creates folder via mount: `~/basic-memory-cloud/new-project/`
- Files appear in `/app/data/new-project/` (mounted bucket)
- Discovery service finds directory on next scan (within 2 minutes)
- Auto-registers as project
- User sees project in `bm project list` after discovery

**Why Both Methods:**
- **API**: Immediate registration when using bisync (client-side scan + API call)
- **Discovery**: Delayed registration when using mount (no API call hook)
- **Result**: Projects created ANY way (API, mount, bisync, WebDAV) eventually registered
- **Trade-off**: 2-minute delay for mount-created projects is acceptable

### Mount vs Bisync Directory Isolation

**Critical Safety Requirement**: Mount and bisync MUST use different directories to prevent conflicts.

**The Dropbox Model Applied:**

Both mount and bisync operate at **bucket level** (all projects), following the Dropbox/iCloud paradigm:

```
~/basic-memory-cloud/          # Mount: Read-through cache (like Dropbox folder)
├── work-notes/
├── personal/
└── research/

~/basic-memory-cloud-sync/         # Bisync: Bidirectional sync (like Dropbox sync folder)
├── work-notes/
├── personal/
└── research/
```

**Mount Directory (Fixed):**
```bash
# Fixed location, not configurable
~/basic-memory-cloud/
```
- **Scope**: Entire bucket (all projects)
- **Method**: NFS mount via `rclone nfsmount`
- **Behavior**: Read-through cache to cloud bucket
- **Credentials**: One IAM credential set per tenant
- **Process**: One rclone mount process
- **Use Case**: Quick access, browsing, light editing
- **Known Issue**: Obsidian compatibility problems with NFS
- **Not Configurable**: Fixed location prevents user error

**Why Fixed Location:**
- One mount point per machine (like `/Users/you/Dropbox`)
- Prevents credential proliferation (one credential set, not N)
- Prevents multiple mount processes (resource efficiency)
- Familiar pattern users already understand
- Simple operations: `mount` once, `unmount` once

**Bisync Directory (User Configurable):**
```bash
# Default location
~/basic-memory-cloud-sync/

# User can override
bm cloud setup --dir ~/my-knowledge-base
```
- **Scope**: Entire bucket (all projects)
- **Method**: Bidirectional sync via `rclone bisync`
- **Behavior**: Full local copy with periodic sync
- **Credentials**: Same IAM credential set as mount
- **Use Case**: Full offline access, reliable editing, Obsidian support
- **Configurable**: Users may want specific locations (external drive, existing folder structure)

**Why User Configurable:**
- Users have preferences for where local copies live
- May want sync folder on external drive
- May want to integrate with existing folder structure
- Default works for most, option available for power users

**Conflict Prevention:**
```python
def validate_bisync_directory(bisync_dir: Path):
    """Ensure bisync directory doesn't conflict with mount."""
    mount_dir = Path.home() / "basic-memory-cloud"

    if bisync_dir.resolve() == mount_dir.resolve():
        raise BisyncError(
            f"Cannot use {bisync_dir} for bisync - it's the mount directory!\n"
            f"Mount and bisync must use different directories.\n\n"
            f"Options:\n"
            f"  1. Use default: ~/basic-memory-cloud-sync/\n"
            f"  2. Specify different directory: --dir ~/my-sync-folder"
        )

    # Check if mount is active at this location
    result = subprocess.run(["mount"], capture_output=True, text=True)
    if str(bisync_dir) in result.stdout and "rclone" in result.stdout:
        raise BisyncError(
            f"{bisync_dir} is currently mounted via 'bm cloud mount'\n"
            f"Cannot use mounted directory for bisync.\n\n"
            f"Either:\n"
            f"  1. Unmount first: bm cloud unmount\n"
            f"  2. Use different directory for bisync"
        )
```

**Why This Matters:**
- Mounting and syncing the SAME directory would create infinite loops
- rclone mount → bisync detects changes → syncs to bucket → mount sees changes → triggers bisync → ∞
- Separate directories = clean separation of concerns
- Mount is read-heavy caching layer, bisync is write-heavy bidirectional sync

### Future Enhancements

**Phase 2 (Not in this spec):**
- **Near Real-Time Sync**: Integrate `watch_service.py` with cloud mode
  - Watch service detects local changes (already battle-tested)
  - Queue changes in memory
  - Use `rclone copy` for individual file sync (near instant)
  - Example: `rclone copyto ~/sync/project/file.md tenant:{bucket}/project/file.md`
  - Fallback to full `rclone bisync` every N seconds for bidirectional changes
  - Provides near real-time sync without polling overhead
- Per-project bisync profiles (different safety levels per project)
- Selective project sync (exclude specific projects from sync)
- Project deletion workflow (cascade to cloud/local)
- Conflict resolution UI/CLI

**Phase 3:**
- Project sharing between tenants
- Incremental backup/restore
- Sync statistics and bandwidth monitoring
- Mobile app integration with cloud mode

### Related Specs

- **SPEC-8**: TigrisFS Integration - Original bisync implementation
- **SPEC-6**: Explicit Project Parameter Architecture - Multi-project foundations
- **SPEC-5**: CLI Cloud Upload via WebDAV - Cloud file operations

### Implementation Notes

**Architectural Simplifications:**
- **Unified CLI**: Eliminated duplicate commands by using mode toggle
- **Single Entry Point**: All commands route through `async_client` which handles mode
- **Config-Driven**: Cloud mode stored in persistent config, not just environment
- **Transparent Routing**: Existing commands work without modification in cloud mode

**Complexity Trade-offs:**
- Removed: Separate `bm cloud project` command namespace
- Removed: Complex state detection for new projects
- Removed: RCLONE_TEST marker file management
- Added: Simple cloud_mode flag and config integration
- Added: Simple project list comparison before sync
- Relied on: Existing bisync profile safety mechanisms
- Result: Significantly simpler, more maintainable code

**User Experience:**
- **Mental Model**: "Toggle cloud mode, use normal commands"
- **No Learning Curve**: Same commands work locally and in cloud
- **Minimal Config**: Just login/logout to switch modes
- **Safety**: Profile system gives users control over safety/speed trade-offs
- **"Just Works"**: Create folders anywhere, they sync automatically

**Migration Path:**
- Existing `bm cloud project` users: Use `bm project` instead
- Existing `bm cloud bisync` becomes `bm sync` in cloud mode
- Config automatically migrates on first `bm cloud login`


## Testing


Initial Setup (One Time)

1. Login to cloud and enable cloud mode:
bm cloud login
# → Authenticates via OAuth
# → Sets cloud_mode=true in config
# → Sets BASIC_MEMORY_PROXY_URL environment variable
# → All CLI commands now route to cloud

2. Check cloud mode status:
bm cloud status
# → Shows: Mode: Cloud (enabled)
# → Shows: Host: https://cloud.basicmemory.com
# → Checks cloud health

3. Set up bidirectional sync:
bm cloud bisync-setup
# Or with custom directory:
bm cloud bisync-setup --dir ~/my-sync-folder

# This will:
# → Install rclone (if not already installed)
# → Get tenant info (tenant_id, bucket_name)
# → Generate scoped IAM credentials
# → Configure rclone with credentials
# → Create sync directory (default: ~/basic-memory-cloud-sync/)
# → Validate no conflict with mount directory
# → Run initial --resync to establish baseline

Normal Usage

4. Create local project and sync:
# Create a local project directory
mkdir ~/basic-memory-cloud-sync/my-research
echo "# Research Notes" > ~/basic-memory-cloud-sync/my-research/readme.md

# Run sync
bm cloud bisync

# Auto-magic happens:
# → Checks for new local directories
# → Finds "my-research" not in cloud
# → Creates project on cloud via POST /proxy/projects/projects
# → Runs bidirectional sync (all projects)
# → Syncs to bucket root (all projects synced together)

5. Watch mode for continuous sync:
bm cloud bisync --watch
# Or with custom interval:
bm cloud bisync --watch --interval 30

# → Syncs every 60 seconds (or custom interval)
# → Auto-registers new projects on each run
# → Press Ctrl+C to stop

6. Check bisync status:
bm cloud bisync-status
# → Shows tenant ID
# → Shows sync directory path
# → Shows initialization status
# → Shows last sync time
# → Lists available profiles (safe/balanced/fast)

7. Manual sync with different profiles:
# Safe mode (max 10 deletes, preserves conflicts)
bm cloud bisync --profile safe

# Balanced mode (max 25 deletes, auto-resolve to newer) - default
bm cloud bisync --profile balanced

# Fast mode (max 50 deletes, skip verification)
bm cloud bisync --profile fast

8. Dry run to preview changes:
bm cloud bisync --dry-run
# → Shows what would be synced without making changes

9. Force resync (if needed):
bm cloud bisync --resync
# → Establishes new baseline
# → Use if sync state is corrupted

10. Check file integrity:
bm cloud check
# → Verifies all files match between local and cloud
# → Read-only operation (no data transfer)
# → Shows differences if any found

# Faster one-way check
bm cloud check --one-way
# → Only checks for missing files on destination

Verify Cloud Mode Integration

11. Test that all commands work in cloud mode:
# List cloud projects (not local)
bm project list

# Create project on cloud
bm project add "work-notes"

# Use MCP tools against cloud
bm tool write-note --title "Test" --folder "my-research" --content "Hello"

# All of these work against cloud because cloud_mode=true

12. Switch back to local mode:
bm cloud logout
# → Sets cloud_mode=false
# → Clears BASIC_MEMORY_PROXY_URL
# → All commands now work locally again

Expected Directory Structure

~/basic-memory-cloud-sync/          # Your local sync directory
├── my-research/                    # Auto-created cloud project
│   ├── readme.md
│   └── notes.md
├── work-notes/                     # Another project
│   └── tasks.md
└── personal/                       # Another project
  └── journal.md

# All sync bidirectionally with:
bucket:/                            # Cloud bucket root
├── my-research/
├── work-notes/
└── personal/

Key Points to Test

1. ✅ Cloud mode toggle works (login/logout)
2. ✅ Bisync setup validates directory (no conflict with mount)
3. ✅ Local directories auto-create cloud projects
4. ✅ All projects sync together (bucket root)
5. ✅ No RCLONE_TEST files created
6. ✅ Changes sync bidirectionally
7. ✅ Watch mode continuous sync works
8. ✅ Profile safety limits work (max_delete)
9. ✅ `bm sync` adapts to cloud mode automatically
10. ✅ `bm cloud check` verifies file integrity without side effects
