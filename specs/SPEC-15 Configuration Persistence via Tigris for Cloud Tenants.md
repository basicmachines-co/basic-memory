---
title: 'SPEC-15: Configuration Persistence via Tigris for Cloud Tenants'
type: spec
permalink: specs/spec-14-config-persistence-tigris
tags:
- persistence
- tigris
- multi-tenant
- infrastructure
- configuration
status: draft
---

# SPEC-15: Configuration Persistence via Tigris for Cloud Tenants

## Why

We need to persist Basic Memory configuration across Fly.io deployments without using persistent volumes or external databases.

**Current Problems:**
- `~/.basic-memory/config.json` lost on every deployment (project configuration)
- `~/.basic-memory/memory.db` lost on every deployment (search index)
- Persistent volumes break clean deployment workflow
- External databases (Turso) require per-tenant token management

**The Insight:**
The SQLite database is just an **index cache** of the markdown files. It can be rebuilt in seconds from the source markdown files in Tigris. Only the small `config.json` file needs true persistence.

**Solution:**
- Store `config.json` in Tigris bucket (persistent, small file)
- Rebuild `memory.db` on startup from markdown files (fast, ephemeral)
- No persistent volumes, no external databases, no token management

## What

Store Basic Memory configuration in the Tigris bucket and rebuild the database index on tenant machine startup.

**Affected Components:**
- `basic-memory/src/basic_memory/config.py` - Add configurable config directory

**Architecture:**

```bash
# Tigris Bucket (persistent, mounted at /app/data)
/app/data/
  ├── .basic-memory/
  │   └── config.json          # ← Project configuration (persistent, accessed via BASIC_MEMORY_CONFIG_DIR)
  └── basic-memory/             # ← Markdown files (persistent, BASIC_MEMORY_HOME)
      ├── project1/
      └── project2/

# Fly Machine (ephemeral)
/app/.basic-memory/
  └── memory.db                # ← Rebuilt on startup (fast local disk)
```

## How (High Level)

### 1. Add Configurable Config Directory to Basic Memory

Currently `ConfigManager` hardcodes `~/.basic-memory/config.json`. Add environment variable to override:

```python
# basic-memory/src/basic_memory/config.py

class ConfigManager:
    """Manages Basic Memory configuration."""

    def __init__(self) -> None:
        """Initialize the configuration manager."""
        home = os.getenv("HOME", Path.home())
        if isinstance(home, str):
            home = Path(home)

        # Allow override via environment variable
        if config_dir := os.getenv("BASIC_MEMORY_CONFIG_DIR"):
            self.config_dir = Path(config_dir)
        else:
            self.config_dir = home / DATA_DIR_NAME

        self.config_file = self.config_dir / CONFIG_FILE_NAME

        # Ensure config directory exists
        self.config_dir.mkdir(parents=True, exist_ok=True)
```

### 2. Rebuild Database on Startup

Basic Memory already has the sync functionality. Just ensure it runs on startup:

```python
# apps/api/src/basic_memory_cloud_api/main.py

@app.on_event("startup")
async def startup_sync():
    """Rebuild database index from Tigris markdown files."""
    logger.info("Starting database rebuild from Tigris")

    # Initialize file sync (rebuilds index from markdown files)
    app_config = ConfigManager().config
    await initialize_file_sync(app_config)

    logger.info("Database rebuild complete")
```

### 3. Environment Configuration

```bash
# Machine environment variables
BASIC_MEMORY_CONFIG_DIR=/app/data/.basic-memory  # Config read/written directly to Tigris
# memory.db stays in default location: /app/.basic-memory/memory.db (local ephemeral disk)
```

## Implementation Task List

### Phase 1: Basic Memory Changes ✅
- [x] Add `BASIC_MEMORY_CONFIG_DIR` environment variable support to `ConfigManager.__init__()`
- [x] Test config loading from custom directory
- [x] Update tests to verify custom config dir works

### Phase 2: Tigris Bucket Structure ✅
- [x] Ensure `.basic-memory/` directory exists in Tigris bucket on tenant creation
  - ✅ ConfigManager auto-creates on first run, no explicit provisioning needed
- [x] Initialize `config.json` in Tigris on first tenant deployment
  - ✅ ConfigManager creates config.json automatically in BASIC_MEMORY_CONFIG_DIR
- [x] Verify TigrisFS handles hidden directories correctly
  - ✅ TigrisFS supports hidden directories (verified in SPEC-8)

### Phase 3: Deployment Integration ✅
- [x] Set `BASIC_MEMORY_CONFIG_DIR` environment variable in machine deployment
  - ✅ Added to BasicMemoryMachineConfigBuilder in fly_schemas.py
- [x] Ensure database rebuild runs on machine startup via initialization sync
  - ✅ sync_worker.py runs initialize_file_sync every 30s (already implemented)
- [x] Handle first-time tenant setup (no config exists yet)
  - ✅ ConfigManager creates config.json on first initialization
- [ ] Test deployment workflow with config persistence

### Phase 4: Testing
- [x] Unit tests for config directory override
- [-] Integration test: deploy → write config → redeploy → verify config persists
- [ ] Integration test: deploy → add project → redeploy → verify project in config
- [ ] Performance test: measure db rebuild time on startup

### Phase 5: Documentation
- [ ] Document config persistence architecture
- [ ] Update deployment runbook
- [ ] Document startup sequence and timing

## How to Evaluate

### Success Criteria

1. **Config Persistence**
   - [ ] config.json persists across deployments
   - [ ] Projects list maintained across restarts
   - [ ] No manual configuration needed after redeploy

2. **Database Rebuild**
   - [ ] memory.db rebuilt on startup in < 30 seconds
   - [ ] All entities indexed correctly
   - [ ] Search functionality works after rebuild

3. **Performance**
   - [ ] SQLite queries remain fast (local disk)
   - [ ] Config reads acceptable (symlink to Tigris)
   - [ ] No noticeable performance degradation

4. **Deployment Workflow**
   - [ ] Clean deployments without volumes
   - [ ] No new external dependencies
   - [ ] No secret management needed

### Testing Procedure

1. **Config Persistence Test**
   ```bash
   # Deploy tenant
   POST /tenants → tenant_id

   # Add a project
   basic-memory project add "test-project" ~/test

   # Verify config has project
   cat /app/data/.basic-memory/config.json

   # Redeploy machine
   fly deploy --app basic-memory-{tenant_id}

   # Verify project still exists
   basic-memory project list
   ```

2. **Database Rebuild Test**
   ```bash
   # Create notes
   basic-memory write "Test Note" --content "..."

   # Redeploy (db lost)
   fly deploy --app basic-memory-{tenant_id}

   # Wait for startup sync
   sleep 10

   # Verify note is indexed
   basic-memory search "Test Note"
   ```

3. **Performance Benchmark**
   ```bash
   # Time the startup sync
   time basic-memory sync

   # Should be < 30 seconds for typical tenant
   ```

## Benefits Over Alternatives

**vs. Persistent Volumes:**
- ✅ Clean deployment workflow
- ✅ No volume migration needed
- ✅ Simpler infrastructure

**vs. Turso (External Database):**
- ✅ No per-tenant token management
- ✅ No external service dependencies
- ✅ No additional costs
- ✅ Simpler architecture

**vs. SQLite on FUSE:**
- ✅ Fast local SQLite performance
- ✅ Only slow reads for small config file
- ✅ Database queries remain fast

## Implementation Assignment

**Primary Agent:** `python-developer`
- Add `BASIC_MEMORY_CONFIG_DIR` environment variable to ConfigManager
- Update deployment workflow to set environment variable
- Ensure startup sync runs correctly

**Review Agent:** `system-architect`
- Validate architecture simplicity
- Review performance implications
- Assess startup timing

## Dependencies

- **Internal:** TigrisFS must be working and stable
- **Internal:** Basic Memory sync must be reliable
- **Internal:** SPEC-8 (TigrisFS Integration) must be complete

## Open Questions

1. Should we add a health check that waits for db rebuild to complete?
2. Do we need to handle very large knowledge bases (>10k entities) differently?
3. Should we add metrics for startup sync duration?

## References

- Basic Memory sync: `basic-memory/src/basic_memory/services/initialization.py`
- Config management: `basic-memory/src/basic_memory/config.py`
- TigrisFS integration: SPEC-8

---

**Status Updates:**

- 2025-10-08: Pivoted from Turso to Tigris-based config persistence
- 2025-10-08: Phase 1 complete - BASIC_MEMORY_CONFIG_DIR support added (PR #343)
- 2025-10-08: Phases 2-3 complete - Added BASIC_MEMORY_CONFIG_DIR to machine config
  - Config now persists to /app/data/.basic-memory/config.json in Tigris bucket
  - Database rebuild already working via sync_worker.py
  - Ready for deployment testing (Phase 4)
