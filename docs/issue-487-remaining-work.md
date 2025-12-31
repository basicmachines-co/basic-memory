# Issue #487 Remaining Work

This document outlines the remaining work for Issue #487 after the quick wins have been addressed.

## ✅ Completed (Quick Wins)

1. **Fixed mutable default args** in `search_notes()` MCP tool
2. **Fixed Pydantic defaults** in `WatchServiceState` classes
3. **Fixed list mutation during iteration** in `WatchService.handle_changes()`
4. **Added regression tests** for all three bugs

## 🔄 Remaining Work

### 1. Coverage Baseline and Restoration

**Goal**: Restore test coverage to ~100% (practical: minimal, justified excludes)

**Tasks**:
- [ ] Run `just coverage` to establish current baseline
- [ ] Identify files/functions with missing coverage
- [ ] Add tests for uncovered code paths
- [ ] Document any justified coverage excludes (e.g., CLI entry points, debug code)

**Priority**: High - This is the main goal of the issue

### 2. Reduce Mock Usage

**Goal**: Replace mocks with integration tests where practical

**Tasks**:
- [ ] **High-value targets** (most mocked files):
  - `tests/test_rclone_commands.py` (56 mocks) - Consider using real rclone or temp dirs
  - `tests/sync/test_sync_service.py` (11 mocks) - Many can use real filesystem
  - `tests/services/test_search_service.py` (8 mocks) - Already mostly integration-style

- [ ] **Cloud/external service mocks**:
  - `tests/cli/test_cloud_authentication.py` - Consider mock OAuth server or test fixtures
  - `tests/cli/test_upload.py` - Consider using MinIO (local S3-compatible storage)
  - `tests/test_telemetry.py` - Mock external telemetry endpoint is acceptable

- [ ] **Move appropriate tests to `test-int/`**:
  - Tests that use real DB + real filesystem + real in-process API client belong in `test-int/`
  - Keep unit tests in `tests/` for isolated component testing

**Priority**: Medium - Important for test reliability but not critical

### 3. Architecture/Quality Follow-ups

**Tasks**:
- [ ] **Cloud-mode DB initialization**: Ensure cloud mode runs required DB init/migrations reliably
  - Even if project reconciliation is skipped
  - Add integration test for cloud mode startup

- [ ] **Telemetry credential optics**: Review client secret in repo
  - Currently marked as "write-only" but still in version control
  - Consider environment variable or config file approach
  - Document security model if keeping in repo

**Priority**: Medium - Quality improvements, not bugs

### 4. Documentation

**Tasks**:
- [ ] Document coverage expectations in CLAUDE.md or CONTRIBUTING.md
- [ ] Document when to use mocks vs integration tests
- [ ] Add examples of good integration tests to test guidelines

**Priority**: Low - Nice to have

## Testing Strategy

### Unit Tests (`tests/`)
- **Use when**: Testing isolated components with clear boundaries
- **Mock**: External services, network calls, filesystem (when appropriate)
- **Examples**: Schema validation, utility functions, parsers

### Integration Tests (`test-int/`)
- **Use when**: Testing user flows, API endpoints, sync operations
- **Real components**: Database (SQLite/Postgres), filesystem (tmp_path), in-process API client
- **Examples**: MCP tool workflows, sync operations, search functionality

### Coverage Goals
- **Target**: ~100% practical coverage
- **Acceptable excludes**: CLI entry points, debug code, unreachable error paths
- **Document**: All coverage excludes with justification

## Next Steps

1. **Run coverage baseline**: `just coverage` (requires approval)
2. **Triage uncovered code**: Identify what needs tests vs what can be excluded
3. **Prioritize high-value tests**: Focus on user-facing functionality first
4. **Reduce strategic mocks**: Start with `test_rclone_commands.py`
5. **Document patterns**: Add testing guidelines to help future contributions

## References

- Issue: #487
- Quick wins commits: [cdc28ee](https://github.com/basicmachines-co/basic-memory/commit/cdc28ee), [bb364dc](https://github.com/basicmachines-co/basic-memory/commit/bb364dc)
- Test structure: `tests/` (unit), `test-int/` (integration)
- Coverage tool: `just coverage` → `coverage/index.html`
