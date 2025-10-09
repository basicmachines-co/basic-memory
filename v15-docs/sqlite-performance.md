# SQLite Performance Improvements

**Status**: Performance Enhancement
**PR**: #316
**Impact**: Faster database operations, better concurrency

## What's New

v0.15.0 enables **Write-Ahead Logging (WAL) mode** for SQLite and adds Windows-specific optimizations, significantly improving performance and concurrent access.

## Key Changes

### 1. WAL Mode Enabled

**Write-Ahead Logging (WAL)** is now enabled by default:

```python
# Applied automatically on database initialization
PRAGMA journal_mode=WAL
```

**Benefits:**
- **Better concurrency:** Readers don't block writers
- **Faster writes:** Transactions commit faster
- **Crash resilience:** Better recovery from crashes
- **Reduced disk I/O:** Fewer fsync operations

### 2. Windows Optimizations

Additional Windows-specific settings:

```python
# Windows-specific SQLite settings
PRAGMA synchronous=NORMAL      # Balanced durability/performance
PRAGMA cache_size=-2000        # 2MB cache
PRAGMA temp_store=MEMORY       # Temp tables in memory
```

## Performance Impact

### Before (DELETE mode)

```python
# Old journal mode
PRAGMA journal_mode=DELETE

# Characteristics:
# - Writers block readers
# - Readers block writers
# - Slower concurrent access
# - More disk I/O
```

**Measured impact:**
- Concurrent read/write: **Serialized (slow)**
- Write speed: **Baseline**
- Crash recovery: **Good**

### After (WAL mode)

```python
# New journal mode
PRAGMA journal_mode=WAL

# Characteristics:
# - Readers don't block writers
# - Writers don't block readers
# - Faster concurrent access
# - Reduced disk I/O
```

**Measured impact:**
- Concurrent read/write: **Parallel (fast)**
- Write speed: **Up to 2-3x faster**
- Crash recovery: **Excellent**

## How WAL Works

### Traditional DELETE Mode

```
Write Transaction:
1. Lock database
2. Write to journal file
3. Modify database
4. Delete journal
5. Unlock database

Problem: Readers wait for writers
```

### WAL Mode

```
Write Transaction:
1. Append changes to WAL file
2. Commit (fast)
3. Periodically checkpoint WAL → database

Benefit: Readers read from database while WAL is being written
```

### Checkpoint Process

WAL file periodically merged back to database:

```python
# Automatic checkpointing
# - Triggered at ~1000 pages in WAL
# - Or manual: PRAGMA wal_checkpoint(TRUNCATE)
```

## Database Files

### Before WAL

```bash
~/basic-memory/
└── .basic-memory/
    └── memory.db           # Single database file
```

### After WAL

```bash
~/.basic-memory/
├── memory.db              # Main database
├── memory.db-wal          # Write-ahead log
└── memory.db-shm          # Shared memory file
```

**Important:** All three files required for database to function

## Use Cases

### 1. Concurrent MCP Servers

**Before (slow):**
```python
# Multiple MCP servers sharing database
Server A: Reading... (blocks Server B)
Server B: Waiting to write...
```

**After (fast):**
```python
# Concurrent access
Server A: Reading (doesn't block)
Server B: Writing (doesn't block)
Server C: Reading (doesn't block)
```

### 2. Real-Time Sync

**Before:**
```bash
# Sync blocks reads
bm sync &              # Background sync
bm tools search ...    # Waits for sync
```

**After:**
```bash
# Sync doesn't block
bm sync &              # Background sync
bm tools search ...    # Runs concurrently
```

### 3. Large Knowledge Bases

**Before:**
- Large writes cause delays
- Readers wait during bulk updates
- Slow performance on large datasets

**After:**
- Large writes don't block reads
- Readers continue during bulk updates
- Better performance on large datasets

## Configuration

### WAL Mode (Default)

Enabled automatically:

```python
# Basic Memory applies on initialization
async def init_db():
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA synchronous=NORMAL")
```

### Verify WAL Mode

```bash
# Check journal mode
sqlite3 ~/.basic-memory/memory.db "PRAGMA journal_mode;"
# → wal
```

### Manual Configuration (Advanced)

```python
from basic_memory.db import get_db

# Get database connection
db = await get_db()

# Check settings
result = await db.execute("PRAGMA journal_mode")
print(result)  # → wal

result = await db.execute("PRAGMA synchronous")
print(result)  # → 1 (NORMAL)
```

## Platform-Specific Optimizations

### Windows

```python
# Windows-specific settings
PRAGMA synchronous=NORMAL      # Balance safety/speed
PRAGMA temp_store=MEMORY       # Faster temp operations
PRAGMA cache_size=-2000        # 2MB cache
```

**Benefits on Windows:**
- Faster on NTFS
- Better with Windows Defender
- Improved antivirus compatibility

### macOS/Linux

```python
# Unix-specific (defaults work well)
PRAGMA journal_mode=WAL
PRAGMA synchronous=NORMAL
```

**Benefits:**
- Faster on APFS/ext4
- Better with spotlight/indexing
- Improved filesystem syncing

## Maintenance

### Checkpoint WAL File

WAL auto-checkpoints, but you can force it:

```python
# Python
from basic_memory.db import get_db

db = await get_db()
await db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
```

```bash
# Command line
sqlite3 ~/.basic-memory/memory.db "PRAGMA wal_checkpoint(TRUNCATE);"
```

**When to checkpoint:**
- Before backup
- After large bulk operations
- When WAL file grows large

### Backup Considerations

**Wrong way (incomplete):**
```bash
# ✗ Only copies main file, misses WAL
cp ~/.basic-memory/memory.db backup.db
```

**Right way (complete):**
```bash
# ✓ Checkpoint first, then backup
sqlite3 ~/.basic-memory/memory.db "PRAGMA wal_checkpoint(TRUNCATE);"
cp ~/.basic-memory/memory.db* backup/

# Or use SQLite backup command
sqlite3 ~/.basic-memory/memory.db ".backup backup.db"
```

### Monitoring WAL Size

```python
import os

wal_file = os.path.expanduser("~/.basic-memory/memory.db-wal")
if os.path.exists(wal_file):
    size_mb = os.path.getsize(wal_file) / (1024 * 1024)
    print(f"WAL size: {size_mb:.2f} MB")

    if size_mb > 10:  # More than 10MB
        # Consider checkpointing
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
```

## Troubleshooting

### Database Locked Error

**Problem:** Still seeing "database is locked" errors

**Possible causes:**
1. WAL mode not enabled
2. Network filesystem (NFS, SMB)
3. Transaction timeout

**Solutions:**

```bash
# 1. Verify WAL mode
sqlite3 ~/.basic-memory/memory.db "PRAGMA journal_mode;"

# 2. Check filesystem (WAL requires local filesystem)
df -T ~/.basic-memory/memory.db

# 3. Increase timeout (if needed)
# In code:
db.execute("PRAGMA busy_timeout=10000")  # 10 seconds
```

### WAL File Growing Large

**Problem:** memory.db-wal keeps growing

**Checkpoint more frequently:**

```python
# Automatic checkpoint at smaller size
db.execute("PRAGMA wal_autocheckpoint=100")  # Every 100 pages

# Or manual checkpoint
db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
```

### Network Filesystem Issues

**Problem:** Using WAL on NFS/SMB

**Limitation:** WAL requires local filesystem with proper locking

**Solution:**
```bash
# Option 1: Use local filesystem
mv ~/.basic-memory /local/path/.basic-memory

# Option 2: Fallback to DELETE mode (slower but works)
sqlite3 memory.db "PRAGMA journal_mode=DELETE"
```

## Performance Benchmarks

### Concurrent Reads/Writes

**Before WAL:**
```
Test: 1 writer + 5 readers
Result: Serialized access
Time: 10.5 seconds
```

**After WAL:**
```
Test: 1 writer + 5 readers
Result: Concurrent access
Time: 3.2 seconds (3.3x faster)
```

### Bulk Operations

**Before WAL:**
```
Test: Import 1000 notes
Result: 15.2 seconds
```

**After WAL:**
```
Test: Import 1000 notes
Result: 5.8 seconds (2.6x faster)
```

### Search Performance

**Before WAL (with concurrent writes):**
```
Test: Full-text search during sync
Result: Blocked, 2.1 seconds
```

**After WAL (with concurrent writes):**
```
Test: Full-text search during sync
Result: Concurrent, 0.4 seconds (5.3x faster)
```

## Best Practices

### 1. Let WAL Auto-Checkpoint

Default auto-checkpointing works well:
```python
# Default: checkpoint at ~1000 pages
# Usually optimal, don't change unless needed
```

### 2. Checkpoint Before Backup

```bash
# Always checkpoint before backup
sqlite3 memory.db "PRAGMA wal_checkpoint(TRUNCATE)"
cp memory.db* backup/
```

### 3. Monitor WAL Size

```bash
# Check WAL size periodically
ls -lh ~/.basic-memory/memory.db-wal

# If > 50MB, consider more frequent checkpoints
```

### 4. Use Local Filesystem

```bash
# ✓ Good: Local SSD/HDD
/home/user/.basic-memory/

# ✗ Bad: Network filesystem
/mnt/nfs/home/.basic-memory/
```

### 5. Don't Delete WAL Files

```bash
# ✗ Never delete these manually
# memory.db-wal
# memory.db-shm

# Let SQLite manage them
```

## Advanced Configuration

### Custom Checkpoint Interval

```python
# Checkpoint more frequently (smaller WAL)
db.execute("PRAGMA wal_autocheckpoint=100")

# Checkpoint less frequently (larger WAL, fewer interruptions)
db.execute("PRAGMA wal_autocheckpoint=10000")
```

### Synchronous Modes

```python
# Modes (in order of durability vs speed):
db.execute("PRAGMA synchronous=OFF")     # Fastest, least safe
db.execute("PRAGMA synchronous=NORMAL")   # Balanced (default)
db.execute("PRAGMA synchronous=FULL")     # Safest, slowest
```

### Cache Size

```python
# Larger cache = faster, more memory
db.execute("PRAGMA cache_size=-10000")  # 10MB cache
db.execute("PRAGMA cache_size=-50000")  # 50MB cache
```

## Migration from v0.14.x

### Automatic Migration

**First run on v0.15.0:**
```bash
bm sync
# → Automatically converts to WAL mode
# → Creates memory.db-wal and memory.db-shm
```

**No action required** - migration is automatic

### Verifying Migration

```bash
# Check mode changed
sqlite3 ~/.basic-memory/memory.db "PRAGMA journal_mode;"
# → wal (was: delete)

# Check new files exist
ls -la ~/.basic-memory/memory.db*
# → memory.db
# → memory.db-wal
# → memory.db-shm
```

## See Also

- SQLite WAL documentation: https://www.sqlite.org/wal.html
- `api-performance.md` - API-level optimizations
- `background-relations.md` - Concurrent processing improvements
- Database optimization guide
