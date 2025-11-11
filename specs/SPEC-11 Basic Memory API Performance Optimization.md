---
title: 'SPEC-11: Basic Memory API Performance Optimization'
type: spec
permalink: specs/spec-11-basic-memory-api-performance-optimization
tags:
- performance
- api
- mcp
- database
- cloud
---

# SPEC-11: Basic Memory API Performance Optimization

## Why

The Basic Memory API experiences significant performance issues in cloud environments due to expensive per-request initialization. MCP tools making
HTTP requests to the API suffer from 350ms-2.6s latency overhead **before** any actual operation occurs.

**Root Cause Analysis:**
- GitHub Issue #82 shows repeated initialization sequences in logs (16:29:35 and 16:49:58)
- Each MCP tool call triggers full database initialization + project reconciliation
- `get_engine_factory()` dependency calls `db.get_or_create_db()` on every request
- `reconcile_projects_with_config()` runs expensive sync operations repeatedly

**Performance Impact:**
- Database connection setup: ~50-100ms per request
- Migration checks: ~100-500ms per request
- Project reconciliation: ~200ms-2s per request
- **Total overhead**: ~350ms-2.6s per MCP tool call

This creates compounding effects with tenant auto-start delays and increases timeout risk in cloud deployments.

## What

This optimization affects the **core basic-memory repository** components:

1. **API Lifespan Management** (`src/basic_memory/api/app.py`)
 - Cache database connections in app state during startup
 - Avoid repeated expensive initialization

2. **Dependency Injection** (`src/basic_memory/deps.py`)
 - Modify `get_engine_factory()` to use cached connections
 - Eliminate per-request database setup

3. **Initialization Service** (`src/basic_memory/services/initialization.py`)
 - Add caching/throttling to project reconciliation
 - Skip expensive operations when appropriate

4. **Configuration** (`src/basic_memory/config.py`)
 - Add optional performance flags for cloud environments

**Backwards Compatibility**: All changes must be backwards compatible with existing CLI and non-cloud usage.

## How (High Level)

### Phase 1: Cache Database Connections (Critical - 80% of gains)

**Problem**: `get_engine_factory()` calls `db.get_or_create_db()` per request
**Solution**: Cache database engine/session in app state during lifespan

1. **Modify API Lifespan** (`api/app.py`):
 ```python
 @asynccontextmanager
 async def lifespan(app: FastAPI):
     app_config = ConfigManager().config
     await initialize_app(app_config)

     # Cache database connection in app state
     engine, session_maker = await db.get_or_create_db(app_config.database_path)
     app.state.engine = engine
     app.state.session_maker = session_maker

     # ... rest of startup logic
```

2. Modify Dependency Injection (deps.py):
```python
async def get_engine_factory(
  request: Request
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
  """Get cached engine and session maker from app state."""
  return request.app.state.engine, request.app.state.session_maker
```
Phase 2: Optimize Project Reconciliation (Secondary - 20% of gains)

Problem: reconcile_projects_with_config() runs expensive sync repeatedly
Solution: Add module-level caching with time-based throttling

1. Add Reconciliation Cache (services/initialization.py):
```ptyhon
_project_reconciliation_completed = False
_last_reconciliation_time = 0

async def reconcile_projects_with_config(app_config, force=False):
  # Skip if recently completed (within 60 seconds) unless forced
  if recently_completed and not force:
      return
  # ... existing logic
```
Phase 3: Cloud Environment Flags (Optional)

Problem: Force expensive initialization in production environments
Solution: Add skip flags for cloud/stateless deployments

1. Add Config Flag (config.py):
skip_initialization_sync: bool = Field(default=False)
2. Configure in Cloud (basic-memory-cloud integration):
BASIC_MEMORY_SKIP_INITIALIZATION_SYNC=true

How to Evaluate

Success Criteria

1. Performance Metrics (Primary):
- MCP tool response time reduced by 50%+ (measure before/after)
- Database connection overhead eliminated (0ms vs 50-100ms)
- Migration check overhead eliminated (0ms vs 100-500ms)
- Project reconciliation overhead reduced by 90%+
2. Load Testing:
- Concurrent MCP tool calls maintain performance
- No memory leaks in cached connections
- Database connection pool behaves correctly
3. Functional Correctness:
- All existing API endpoints work identically
- MCP tools maintain full functionality
- CLI operations unaffected
- Database migrations still execute properly
4. Backwards Compatibility:
- No breaking changes to existing APIs
- Config changes are optional with safe defaults
- Non-cloud deployments work unchanged

Testing Strategy

Performance Testing:
# Before optimization
time basic-memory-mcp-tools write_note "test" "content" "folder"
# Measure: ~1-3 seconds

# After optimization  
time basic-memory-mcp-tools write_note "test" "content" "folder"
# Target: <500ms

Load Testing:
# Multiple concurrent MCP tool calls
for i in {1..10}; do
basic-memory-mcp-tools search "test" &
done
wait
# Verify: No degradation, consistent response times

Regression Testing:
# Full basic-memory test suite
just test
# All tests must pass

# Integration tests with cloud deployment
# Verify MCP gateway → API → database flow works

Validation Checklist

- Phase 1 Complete: Database connections cached, dependency injection optimized
- Performance Benchmark: 50%+ improvement in MCP tool response times
- Memory Usage: No leaks in cached connections over 24h+ periods
- Stress Testing: 100+ concurrent requests maintain performance
- Backwards Compatibility: All existing functionality preserved
- Documentation: Performance optimization documented in README
- Cloud Integration: basic-memory-cloud sees performance benefits

Notes

Implementation Priority:
- Phase 1 provides 80% of performance gains and should be implemented first
- Phase 2 provides remaining 20% and addresses edge cases
- Phase 3 is optional for maximum cloud optimization

Risk Mitigation:
- All changes backwards compatible
- Gradual rollout possible (Phase 1 → 2 → 3)
- Easy rollback via configuration flags

Cloud Integration:
- This optimization directly addresses basic-memory-cloud issue #82
- Changes in core basic-memory will benefit all cloud tenants
- No changes needed in basic-memory-cloud itself
