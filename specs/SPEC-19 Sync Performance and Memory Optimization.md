---
title: 'SPEC-19: Sync Performance and Memory Optimization'
type: spec
permalink: specs/spec-17-sync-performance-optimization
tags:
- performance
- memory
- sync
- optimization
- core
status: draft
---

# SPEC-19: Sync Performance and Memory Optimization

## Why

### Problem Statement

Current sync implementation causes Out-of-Memory (OOM) kills and poor performance on production systems:

**Evidence from Production**:
- **Tenant-6d2ff1a3**: OOM killed on 1GB machine
  - Files: 2,621 total (31 PDFs, 80MB binary data)
  - Memory: 1.5-1.7GB peak usage
  - Sync duration: 15+ minutes
  - Error: `Out of memory: Killed process 693 (python)`

**Root Causes**:

1. **Checksum-based scanning loads ALL files into memory**
   - `scan_directory()` computes checksums for ALL 2,624 files upfront
   - Results stored in multiple dicts (`ScanResult.files`, `SyncReport.checksums`)
   - Even unchanged files are fully read and checksummed

2. **Large files read entirely for checksums**
   - 16MB PDF → Full read into memory → Compute checksum
   - No streaming or chunked processing
   - TigrisFS caching compounds memory usage

3. **Unbounded concurrency**
   - All 2,624 files processed simultaneously
   - Each file loads full content into memory
   - No semaphore limiting concurrent operations

4. **Cloud-specific resource leaks**
   - aiohttp session leak in keepalive (not in context manager)
   - Circuit breaker resets every 30s sync cycle (ineffective)
   - Thundering herd: all tenants sync at :00 and :30

### Impact

- **Production stability**: OOM kills are unacceptable
- **User experience**: 15+ minute syncs are too slow
- **Cost**: Forced upgrades from 1GB → 2GB machines ($5-10/mo per tenant)
- **Scalability**: Current approach won't scale to 100+ tenants

### Architectural Decision

**Fix in basic-memory core first, NOT UberSync**

Rationale:
- Root causes are algorithmic, not architectural
- Benefits all users (CLI + Cloud)
- Lower risk than new centralized service
- Known solutions (rsync/rclone use same pattern)
- Can defer UberSync until metrics prove it necessary

## What

### Affected Components

**basic-memory (core)**:
- `src/basic_memory/sync/sync_service.py` - Core sync algorithm (~42KB)
- `src/basic_memory/models.py` - Entity model (add mtime/size columns)
- `src/basic_memory/file_utils.py` - Checksum computation functions
- `src/basic_memory/repository/entity_repository.py` - Database queries
- `alembic/versions/` - Database migration for schema changes

**basic-memory-cloud (wrapper)**:
- `apps/api/src/basic_memory_cloud_api/sync_worker.py` - Cloud sync wrapper
- Circuit breaker implementation
- Sync coordination logic

### Database Schema Changes

Add to Entity model:
```python
mtime: float  # File modification timestamp
size: int     # File size in bytes
```

## How (High Level)

### Phase 1: Core Algorithm Fixes (basic-memory)

**Priority: P0 - Critical**

#### 1.1 mtime-based Scanning (Issue #383)

Replace expensive checksum-based scanning with lightweight stat-based comparison:

```python
async def scan_directory(self, directory: Path) -> ScanResult:
    """Scan using mtime/size instead of checksums"""
    result = ScanResult()

    for root, dirnames, filenames in os.walk(str(directory)):
        for filename in filenames:
            rel_path = path.relative_to(directory).as_posix()
            stat = path.stat()

            # Store lightweight metadata instead of checksum
            result.files[rel_path] = {
                'mtime': stat.st_mtime,
                'size': stat.st_size
            }

    return result

async def scan(self, directory: Path):
    """Compare mtime/size, only compute checksums for changed files"""
    db_state = await self.get_db_file_state()  # Include mtime/size
    scan_result = await self.scan_directory(directory)

    for file_path, metadata in scan_result.files.items():
        db_metadata = db_state.get(file_path)

        # Only compute expensive checksum if mtime/size changed
        if not db_metadata or metadata['mtime'] != db_metadata['mtime']:
            checksum = await self._compute_checksum_streaming(file_path)
            # Process immediately, don't accumulate in memory
```

**Benefits**:
- No file reads during initial scan (just stat calls)
- ~90% reduction in memory usage
- ~10x faster scan phase
- Only checksum files that actually changed

#### 1.2 Streaming Checksum Computation (Issue #382)

For large files (>1MB), use chunked reading to avoid loading entire file:

```python
async def _compute_checksum_streaming(self, path: Path, chunk_size: int = 65536) -> str:
    """Compute checksum using 64KB chunks for large files"""
    hasher = hashlib.sha256()

    loop = asyncio.get_event_loop()

    def read_chunks():
        with open(path, 'rb') as f:
            while chunk := f.read(chunk_size):
                hasher.update(chunk)

    await loop.run_in_executor(None, read_chunks)
    return hasher.hexdigest()

async def _compute_checksum_async(self, file_path: Path) -> str:
    """Choose appropriate checksum method based on file size"""
    stat = file_path.stat()

    if stat.st_size > 1_048_576:  # 1MB threshold
        return await self._compute_checksum_streaming(file_path)
    else:
        # Small files: existing fast path
        content = await self._read_file_async(file_path)
        return compute_checksum(content)
```

**Benefits**:
- Constant memory usage regardless of file size
- 16MB PDF uses 64KB memory (not 16MB)
- Works well with TigrisFS network I/O

#### 1.3 Bounded Concurrency (Issue #198)

Add semaphore to limit concurrent file operations, or consider using aiofiles and async reads

```python
class SyncService:
    def __init__(self, ...):
        # ... existing code ...
        self._file_semaphore = asyncio.Semaphore(10)  # Max 10 concurrent
        self._max_tracked_failures = 100  # LRU cache limit

    async def _read_file_async(self, file_path: Path) -> str:
        async with self._file_semaphore:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(
                self._thread_pool,
                file_path.read_text,
                "utf-8"
            )

    async def _record_failure(self, path: str, error: str):
        # ... existing code ...

        # Implement LRU eviction
        if len(self._file_failures) > self._max_tracked_failures:
            self._file_failures.popitem(last=False)  # Remove oldest
```

**Benefits**:
- Maximum 10 files in memory at once (vs all 2,624)
- 90%+ reduction in peak memory usage
- Prevents unbounded memory growth on error-prone projects

### Phase 2: Cloud-Specific Fixes (basic-memory-cloud)

**Priority: P1 - High**

#### 2.1 Fix Resource Leaks

```python
# apps/api/src/basic_memory_cloud_api/sync_worker.py

async def send_keepalive():
    """Send keepalive pings using proper session management"""
    # Use context manager to ensure cleanup
    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=5)
    ) as session:
        while True:
            try:
                await session.get(f"https://{fly_app_name}.fly.dev/health")
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                raise  # Exit cleanly
            except Exception as e:
                logger.warning(f"Keepalive failed: {e}")
```

#### 2.2 Improve Circuit Breaker

Track failures across sync cycles instead of resetting every 30s:

```python
# Persistent failure tracking
class SyncWorker:
    def __init__(self):
        self._persistent_failures: Dict[str, int] = {}  # file -> failure_count
        self._failure_window_start = time.time()

    async def should_skip_file(self, file_path: str) -> bool:
        # Skip files that failed >3 times in last hour
        if self._persistent_failures.get(file_path, 0) > 3:
            if time.time() - self._failure_window_start < 3600:
                return True
        return False
```

### Phase 3: Measurement & Decision

**Priority: P2 - Future**

After implementing Phases 1-2, collect metrics for 2 weeks:
- Memory usage per tenant sync
- Sync duration (scan + process)
- Concurrent sync load at peak times
- OOM incidents
- Resource costs

**UberSync Decision Criteria**:

Build centralized sync service ONLY if metrics show:
- ✅ Core fixes insufficient for >100 tenants
- ✅ Resource contention causing problems
- ✅ Need for tenant tier prioritization (paid > free)
- ✅ Cost savings justify complexity

Otherwise, defer UberSync as premature optimization.

## How to Evaluate

### Success Metrics (Phase 1)

**Memory Usage**:
- ✅ Peak memory <500MB for 2,000+ file projects (was 1.5-1.7GB)
- ✅ Memory usage linear with concurrent files (10 max), not total files
- ✅ Large file memory usage: 64KB chunks (not 16MB)

**Performance**:
- ✅ Initial scan <30 seconds (was 5+ minutes)
- ✅ Full sync <5 minutes for 2,000+ files (was 15+ minutes)
- ✅ Subsequent syncs <10 seconds (only changed files)

**Stability**:
- ✅ 2,000+ file projects run on 1GB machines
- ✅ Zero OOM kills in production
- ✅ No degradation with binary files (PDFs, images)

### Success Metrics (Phase 2)

**Resource Management**:
- ✅ Zero aiohttp session leaks (verified via monitoring)
- ✅ Circuit breaker prevents repeated failures (>3 fails = skip for 1 hour)
- ✅ Tenant syncs distributed over 30s window (no thundering herd)

**Observability**:
- ✅ Logfire traces show memory usage per sync
- ✅ Clear logging of skipped files and reasons
- ✅ Metrics on sync duration, file counts, failure rates

### Test Plan

**Unit Tests** (basic-memory):
- mtime comparison logic
- Streaming checksum correctness
- Semaphore limiting (mock 100 files, verify max 10 concurrent)
- LRU cache eviction
- Checksum computation: streaming vs non-streaming equivalence

**Integration Tests** (basic-memory):
- Large file handling (create 20MB test file)
- Mixed file types (text + binary)
- Changed file detection via mtime
- Sync with 1,000+ files

**Load Tests** (basic-memory-cloud):
- Test on tenant-6d2ff1a3 (2,621 files, 31 PDFs)
- Monitor memory during full sync with Logfire
- Measure scan and sync duration
- Run on 1GB machine (downgrade from 2GB to verify)
- Simulate 10 concurrent tenant syncs

**Regression Tests**:
- Verify existing sync scenarios still work
- CLI sync behavior unchanged
- File watcher integration unaffected

### Performance Benchmarks

Establish baseline, then compare after each phase:

| Metric | Baseline | Phase 1 Target | Phase 2 Target |
|--------|----------|----------------|----------------|
| Peak Memory (2,600 files) | 1.5-1.7GB | <500MB | <450MB |
| Initial Scan Time | 5+ min | <30 sec | <30 sec |
| Full Sync Time | 15+ min | <5 min | <5 min |
| Subsequent Sync | 2+ min | <10 sec | <10 sec |
| OOM Incidents/Week | 2-3 | 0 | 0 |
| Min RAM Required | 2GB | 1GB | 1GB |

## Implementation Phases

### Phase 0.5: Database Schema & Streaming Foundation

**Priority: P0 - Required for Phase 1**

This phase establishes the foundation for streaming sync with mtime-based change detection.

**Database Schema Changes**:
- [x] Add `mtime` column to Entity model (REAL type for float timestamp)
- [x] Add `size` column to Entity model (INTEGER type for file size in bytes)
- [x] Create Alembic migration for new columns (nullable initially)
- [x] Add indexes on `(file_path, project_id)` for optimistic upsert performance
- [ ] Backfill existing entities with mtime/size from filesystem

**Streaming Architecture**:
- [x] Replace `os.walk()` with `os.scandir()` for cached stat info
- [ ] Eliminate `get_db_file_state()` - no upfront SELECT all entities
- [x] Implement streaming iterator `_scan_directory_streaming()`
- [x] Add `get_by_file_path()` optimized query (single file lookup)
- [x] Add `get_all_file_paths()` for deletion detection (paths only, no entities)

**Benefits**:
- **50% fewer network calls** on Tigris (scandir returns cached stat)
- **No large dicts in memory** (process files one at a time)
- **Indexed lookups** instead of full table scan
- **Foundation for mtime comparison** (Phase 1)

**Code Changes**:
```python
# Before: Load all entities upfront
db_paths = await self.get_db_file_state()  # SELECT * FROM entity WHERE project_id = ?
scan_result = await self.scan_directory()  # os.walk() + stat() per file

# After: Stream and query incrementally
async for file_path, stat_info in self._scan_directory_streaming():  # scandir() with cached stat
    db_entity = await self.entity_repository.get_by_file_path(rel_path)  # Indexed lookup
    # Process immediately, no accumulation
```

**Files Modified**:
- `src/basic_memory/models.py` - Add mtime/size columns
- `alembic/versions/xxx_add_mtime_size.py` - Migration
- `src/basic_memory/sync/sync_service.py` - Streaming implementation
- `src/basic_memory/repository/entity_repository.py` - Add get_all_file_paths()

**Migration Strategy**:
```sql
-- Migration: Add nullable columns
ALTER TABLE entity ADD COLUMN mtime REAL;
ALTER TABLE entity ADD COLUMN size INTEGER;

-- Backfill from filesystem during first sync after upgrade
-- (Handled in sync_service on first scan)
```

### Phase 1: Core Fixes

**mtime-based scanning**:
- [x] Add mtime/size columns to Entity model (completed in Phase 0.5)
- [x] Database migration (alembic) (completed in Phase 0.5)
- [ ] Refactor `scan()` to use streaming architecture with mtime/size comparison
- [ ] Update `_process_file()` to store mtime/size in database on upsert
- [ ] Only compute checksums for changed files (mtime/size differ)
- [ ] Unit tests for mtime comparison logic
- [ ] Integration test with 1,000 files

**Streaming checksums**:
- [x] Implement `_compute_checksum_streaming()` with chunked reading
- [x] Add file size threshold logic (1MB)
- [x] Test with large files (16MB PDF)
- [x] Verify memory usage stays constant
- [x] Test checksum equivalence (streaming vs non-streaming)

**Bounded concurrency**:
- [x] Add semaphore (10 concurrent) to `_read_file_async()` (already existed)
- [x] Add LRU cache for failures (100 max) (already existed)
- [ ] Review thread pool size configuration
- [ ] Load test with 2,000+ files
- [ ] Verify <500MB peak memory

**Cleanup & Optimization**:
- [ ] Eliminate `get_db_file_state()` - no upfront SELECT all entities
- [ ] Remove sync status service (if unused)
- [ ] Consider aiofiles for non-blocking I/O (future enhancement)

### Phase 2: Cloud Fixes 

**Resource leaks**:
- [ ] Fix aiohttp session context manager
- [ ] Implement persistent circuit breaker
- [ ] Add memory monitoring/alerts
- [ ] Test on production tenant

**Sync coordination**:
- [ ] Implement hash-based staggering
- [ ] Add jitter to sync intervals
- [ ] Load test with 10 concurrent tenants
- [ ] Verify no thundering herd

### Phase 3: Measurement

**Deploy to production**:
- [ ] Deploy Phase 1+2 changes
- [ ] Downgrade tenant-6d2ff1a3 to 1GB
- [ ] Monitor for OOM incidents

**Collect metrics**:
- [ ] Memory usage patterns
- [ ] Sync duration distributions
- [ ] Concurrent sync load
- [ ] Cost analysis

**UberSync decision**:
- [ ] Review metrics against decision criteria
- [ ] Document findings
- [ ] Create SPEC-18 for UberSync if needed

## Related Issues

### basic-memory (core)
- [#383](https://github.com/basicmachines-co/basic-memory/issues/383) - Refactor sync to use mtime-based scanning
- [#382](https://github.com/basicmachines-co/basic-memory/issues/382) - Optimize memory for large file syncs
- [#371](https://github.com/basicmachines-co/basic-memory/issues/371) - aiofiles for non-blocking I/O (future)

### basic-memory-cloud
- [#198](https://github.com/basicmachines-co/basic-memory-cloud/issues/198) - Memory optimization for sync worker
- [#189](https://github.com/basicmachines-co/basic-memory-cloud/issues/189) - Circuit breaker for infinite retry loops

## References

**Standard sync tools using mtime**:
- rsync: Uses mtime-based comparison by default, only checksums on `--checksum` flag
- rclone: Default is mtime/size, `--checksum` mode optional
- syncthing: Block-level sync with mtime tracking

**fsnotify polling** (future consideration):
- [fsnotify/fsnotify#9](https://github.com/fsnotify/fsnotify/issues/9) - Polling mode for network filesystems

## Notes

### Why Not UberSync Now?

**Premature Optimization**:
- Current problems are algorithmic, not architectural
- No evidence that multi-tenant coordination is the issue
- Single tenant OOM proves algorithm is the problem

**Benefits of Core-First Approach**:
- ✅ Helps all users (CLI + Cloud)
- ✅ Lower risk (no new service)
- ✅ Clear path (issues specify fixes)
- ✅ Can defer UberSync until proven necessary

**When UberSync Makes Sense**:
- >100 active tenants causing resource contention
- Need for tenant tier prioritization (paid > free)
- Centralized observability requirements
- Cost optimization at scale

### Migration Strategy

**Backward Compatibility**:
- New mtime/size columns nullable initially
- Existing entities sync normally (compute mtime on first scan)
- No breaking changes to MCP API
- CLI behavior unchanged

**Rollout**:
1. Deploy to staging with test tenant
2. Validate memory/performance improvements
3. Deploy to production (blue-green)
4. Monitor for 1 week
5. Downgrade tenant machines if successful

## Further Considerations

### Version Control System (VCS) Integration

**Context:** Users frequently request git versioning, and large projects with PDFs/images pose memory challenges.

#### Git-Based Sync

**Approach:** Use git for change detection instead of custom mtime comparison.

```python
# Git automatically tracks changes
repo = git.Repo(project_path)
repo.git.add(A=True)
diff = repo.index.diff('HEAD')

for change in diff:
    if change.change_type == 'M':  # Modified
        await sync_file(change.b_path)
```

**Pros:**
- ✅ Proven, battle-tested change detection
- ✅ Built-in rename/move detection (similarity index)
- ✅ Efficient for cloud sync (git protocol over HTTP)
- ✅ Could enable version history as bonus feature
- ✅ Users want git integration anyway

**Cons:**
- ❌ User confusion (`.git` folder in knowledge base)
- ❌ Conflicts with existing git repos (submodule complexity)
- ❌ Adds dependency (git binary or dulwich/pygit2)
- ❌ Less control over sync logic
- ❌ Doesn't solve large file problem (PDFs still checksummed)
- ❌ Git LFS adds complexity

#### Jujutsu (jj) Alternative

**Why jj is compelling:**

1. **Working Copy as Source of Truth**
   - Git: Staging area is intermediate state
   - Jujutsu: Working copy IS a commit
   - Aligns with "files are source of truth" philosophy!

2. **Automatic Change Tracking**
   - No manual staging required
   - Working copy changes tracked automatically
   - Better fit for sync operations vs git's commit-centric model

3. **Conflict Handling**
   - User edits + sync changes both preserved
   - Operation log vs linear history
   - Built for operations, not just history

**Cons:**
- ❌ New/immature (2020 vs git's 2005)
- ❌ Not universally available
- ❌ Steeper learning curve for users
- ❌ No LFS equivalent yet
- ❌ Still doesn't solve large file checksumming

#### Git Index Format (Hybrid Approach)

**Best of both worlds:** Use git's index format without full git repo.

```python
from dulwich.index import Index  # Pure Python

# Use git index format for tracking
idx = Index(project_path / '.basic-memory' / 'index')

for file in files:
    stat = file.stat()
    if idx.get(file) and idx[file].mtime == stat.st_mtime:
        continue  # Unchanged (git's proven logic)

    await sync_file(file)
    idx[file] = (stat.st_mtime, stat.st_size, sha)
```

**Pros:**
- ✅ Git's proven change detection logic
- ✅ No user-visible `.git` folder
- ✅ No git dependency (pure Python)
- ✅ Full control over sync

**Cons:**
- ❌ Adds dependency (dulwich)
- ❌ Doesn't solve large files
- ❌ No built-in versioning

### Large File Handling

**Problem:** PDFs/images cause memory issues regardless of VCS choice.

**Solutions (Phase 1+):**

**1. Skip Checksums for Large Files**
```python
if stat.st_size > 10_000_000:  # 10MB threshold
    checksum = None  # Use mtime/size only
    logger.info(f"Skipping checksum for {file_path}")
```

**2. Partial Hashing**
```python
if file.suffix in ['.pdf', '.jpg', '.png']:
    # Hash first/last 64KB instead of entire file
    checksum = hash_partial(file, chunk_size=65536)
```

**3. External Blob Storage**
```python
if stat.st_size > 10_000_000:
    blob_id = await upload_to_tigris_blob(file)
    entity.blob_id = blob_id
    entity.file_path = None  # Not in main sync
```

### Recommendation & Timeline

**Phase 0.5-1 (Now):** Custom streaming + mtime
- ✅ Solves urgent memory issues
- ✅ No dependencies
- ✅ Full control
- ✅ Skip checksums for large files (>10MB)
- ✅ Proven pattern (rsync/rclone)

**Phase 2 (After metrics):** Git index format exploration
```python
# Optional: Use git index for tracking if beneficial
from dulwich.index import Index
# No git repo, just index file format
```

**Future (User feature):** User-facing versioning
```python
# Let users opt into VCS:
basic-memory config set versioning git
basic-memory config set versioning jj
basic-memory config set versioning none  # Current behavior

# Integrate with their chosen workflow
# Not forced upon them
```

**Rationale:**
1. **Don't block on VCS decision** - Memory issues are P0
2. **Learn from deployment** - See actual usage patterns
3. **Keep options open** - Can add git/jj later
4. **Files as source of truth** - Core philosophy preserved
5. **Large files need attention regardless** - VCS won't solve that

**Decision Point:**
- If Phase 0.5/1 achieves memory targets → VCS integration deferred
- If users strongly request versioning → Add as opt-in feature
- If change detection becomes bottleneck → Explore git index format

## Agent Assignment

**Phase 1 Implementation**: `python-developer` agent
- Expertise in FastAPI, async Python, database migrations
- Handles basic-memory core changes

**Phase 2 Implementation**: `python-developer` agent
- Same agent continues with cloud-specific fixes
- Maintains consistency across phases

**Phase 3 Review**: `system-architect` agent
- Analyzes metrics and makes UberSync decision
- Creates SPEC-18 if centralized service needed
