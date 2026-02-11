# Per-Project Local/Cloud Routing

## Context

basic-memory's cloud/local mode is currently a global toggle (`cloud_mode: bool`). When enabled, ALL projects route through the cloud proxy via OAuth. This is too coarse — users should be able to keep some projects local and route others through cloud.

The cloud API already supports API key auth (`bmc_`-prefixed keys, `POST /api/keys` to create, `HybridTokenVerifier` routes them automatically). API keys are per-tenant (account-level), not per-project — there are no per-project permissions in the cloud yet.

**Goal**: Users can set each project to `local` or `cloud` mode. Local projects use the existing ASGI in-process transport. Cloud projects use the cloud API with a single account-level API key. No OAuth dance needed for cloud project access.

## UX Flow

**Option A — Create key in web app:**
1. User creates API key in cloud web app (already supported)
2. Copies the key
3. Runs `bm cloud set-key bmc_abc123...` → saves to config.json

**Option B — Create key via CLI:**
1. User is already logged in via OAuth (`bm cloud login`)
2. Runs `bm cloud create-key "my-laptop"` → calls `POST /api/keys` with JWT auth → gets key back → saves to config.json
3. OAuth login is no longer needed for day-to-day use — the API key handles auth

**Setting project mode:**
```bash
bm project set-cloud research      # route "research" project through cloud
bm project set-local research      # revert to local
bm project list                    # shows mode column (local/cloud)
```

## Implementation Plan

### Step 1: Config model changes

**File: `src/basic_memory/config.py`**

- Add `ProjectMode` enum: `LOCAL = "local"`, `CLOUD = "cloud"`
- Add `ProjectConfigEntry` Pydantic model: `path: str`, `mode: ProjectMode = LOCAL`
- Evolve `BasicMemoryConfig.projects` from `Dict[str, str]` to `Dict[str, ProjectConfigEntry]`
- Add `model_validator(mode="before")` to auto-migrate old `{"name": "/path"}` format to `{"name": {"path": "/path", "mode": "local"}}`
- Add `cloud_api_key: Optional[str] = None` field to `BasicMemoryConfig` (account-level, not per-project)
- Update `ProjectConfig` dataclass to carry `mode` from config entry
- Add helpers: `get_project_entry(name)`, `get_project_mode(name)`
- Keep global `cloud_mode` as deprecated fallback
- Update all code that reads `config.projects` as `Dict[str, str]` to handle `ProjectConfigEntry`

### Step 2: Client routing

**File: `src/basic_memory/mcp/async_client.py`**

- Add optional `project_name: Optional[str] = None` parameter to `get_client()`
- Routing logic (priority order):
  1. Factory injection (`_client_factory`) — unchanged
  2. Force-local (`_force_local_mode()`) — unchanged
  3. **New**: If `project_name` provided and project's mode is `CLOUD` → HTTP client with `cloud_api_key` as Bearer token, hitting `cloud_host/proxy`
  4. Global `cloud_mode_enabled` fallback — existing OAuth flow (deprecated)
  5. Default: local ASGI transport
- Error if cloud project but no `cloud_api_key` in config — actionable message pointing to `bm cloud set-key` or `bm cloud create-key`

### Step 3: Project-aware client helper

**File: `src/basic_memory/mcp/project_context.py`**

- Add `get_project_client(project, context)` async context manager
- Combines `resolve_project_parameter()` (config-only, no network) + `get_client(project_name=resolved)` + `get_active_project(client, resolved, context)`
- Returns `(client, active_project)` tuple
- Solves bootstrap problem: resolve project name first, create correct client, then validate

### Step 4: Simplify ProjectResolver

**File: `src/basic_memory/project_resolver.py`**

- Remove global `cloud_mode` parameter — routing mode is orthogonal to project resolution
- Resolution becomes purely: constrained env var → explicit param → default project
- Update `resolve_project_parameter()` in `project_context.py` to drop `cloud_mode` param

### Step 5: Update MCP tools

**Files: `src/basic_memory/mcp/tools/*.py` (~15 files)**

Mechanical change per tool:
```python
# Before
async with get_client() as client:
    active_project = await get_active_project(client, project, context)

# After
async with get_project_client(project, context) as (client, active_project):
```

Special handling for `recent_activity.py` discovery mode: iterate projects, create per-project client for each.

### Step 6: Sync coordinator

**Files: `src/basic_memory/sync/coordinator.py`, `src/basic_memory/mcp/container.py`**

- Filter file watchers to local-mode projects only
- Cloud projects skip sync

### Step 7: CLI commands

**File: `src/basic_memory/cli/commands/cloud/core_commands.py`**

- `bm cloud set-key <api-key>` — saves API key to config.json
- `bm cloud create-key <name>` — calls `POST {cloud_host}/api/keys` using existing JWT auth (from `make_api_request`), saves returned key to config. Uses existing `api_client.py:make_api_request()` for the authenticated call.

**File: `src/basic_memory/cli/commands/project.py`**

- `bm project set-cloud <name>` — sets project mode to cloud (validates API key exists in config)
- `bm project set-local <name>` — reverts project to local mode
- Extend `bm project list` / `bm project info` to show mode column

### Step 8: RuntimeMode simplification

**File: `src/basic_memory/runtime.py`**

- `resolve_runtime_mode()` drops `cloud_mode_enabled` parameter
- Simplifies to: TEST if test env, otherwise LOCAL
- `RuntimeMode.CLOUD` kept for backward compat but not used in global resolution

### Step 9: Tests

- Config: migration from old format, round-trip serialization, `get_project_mode()`
- `get_client()`: local project → ASGI, cloud project → HTTP+API key, missing key → error
- `get_project_client()`: resolve + route combined
- MCP tools: representative sample with new helper
- Sync: cloud projects skipped, local projects synced
- CLI: `set-key`, `create-key`, `set-cloud`, `set-local`

## Key Files

| File | Change |
|------|--------|
| `src/basic_memory/config.py` | `ProjectMode`, `ProjectConfigEntry`, migration, `cloud_api_key` field |
| `src/basic_memory/mcp/async_client.py` | `get_client(project_name=)` per-project routing |
| `src/basic_memory/mcp/project_context.py` | `get_project_client()` helper |
| `src/basic_memory/project_resolver.py` | Remove global `cloud_mode` concern |
| `src/basic_memory/mcp/tools/*.py` | Mechanical swap to `get_project_client()` |
| `src/basic_memory/sync/coordinator.py` | Filter to local-mode projects |
| `src/basic_memory/mcp/container.py` | Update should_sync logic |
| `src/basic_memory/cli/commands/cloud/core_commands.py` | `set-key`, `create-key` commands |
| `src/basic_memory/cli/commands/project.py` | `set-cloud`, `set-local` commands |
| `src/basic_memory/runtime.py` | Drop cloud_mode from global resolution |

## Config Example

```json
{
  "projects": {
    "personal": {"path": "/Users/me/notes", "mode": "local"},
    "research": {"path": "/Users/me/research", "mode": "cloud"}
  },
  "cloud_api_key": "bmc_abc123...",
  "cloud_host": "https://cloud.basicmemory.com",
  "default_project": "personal"
}
```

## Edge Cases

| Case | Handling |
|------|----------|
| No API key + cloud project | `get_client()` raises error: "Run `bm cloud set-key` first" |
| Old config format loaded | `model_validator` auto-migrates `Dict[str,str]` to new format |
| Default project is cloud | Works — resolver returns name, routing uses API key |
| Global `cloud_mode=true` (legacy) | Deprecated fallback still works via OAuth |
| Factory-injected client (cloud app) | Factory takes priority, unaffected |
| `--local` CLI flag on cloud project | Force-local override still works |

## Verification

1. `just fast-check` — lint/format/typecheck + impacted tests
2. `just test` — full suite (SQLite + Postgres)
3. Manual: `bm cloud set-key bmc_...`, `bm project set-cloud test`, run MCP tools against it
4. Manual: verify local projects work unchanged
5. Manual: `bm project list` shows mode column
