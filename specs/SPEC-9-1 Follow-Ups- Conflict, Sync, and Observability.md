---
title: 'SPEC-9-1 Follow-Ups: Conflict, Sync, and Observability'
type: tasklist
permalink: specs/spec-9-follow-ups-conflict-sync-and-observability
related: specs/spec-9-multi-project-bisync
status: revised
revision_date: 2025-10-03
---

# SPEC-9-1 Follow-Ups: Conflict, Sync, and Observability

**REVISED 2025-10-03:** Simplified to leverage rclone built-ins instead of custom conflict handling.

**Context:** SPEC-9 delivered multi-project bidirectional sync and a unified CLI. This follow-up focuses on **observability and safety** using rclone's built-in capabilities rather than reinventing conflict handling.

**Design Philosophy: "Be Dumb Like Git"**
- Let rclone bisync handle conflict detection (it already does this)
- Make conflicts visible and recoverable, don't prevent them
- Cloud is always the winner on conflict (cloud-primary model)
- Users who want version history can just use Git locally in their sync directory

**What Changed from Original Version:**
- **Replaced:** Custom `.bmmeta` sidecars → Use rclone's `.bisync/` state tracking
- **Replaced:** Custom conflict detection → Use rclone bisync 3-way merge
- **Replaced:** Tombstone files → rclone delete tracking handles this
- **Replaced:** Distributed lease → Local process lock only (document multi-device warning)
- **Replaced:** S3 versioning service → Users just use Git locally if they want history
- **Deferred:** SPEC-14 Git integration → Postponed to teams/multi-user features

## ✅ Now 
- [ ] **Local process lock**: Prevent concurrent bisync runs on same device (`~/.basic-memory/sync.lock`)
- [ ] **Structured sync reports**: Parse rclone bisync output into JSON reports (creates/updates/deletes/conflicts, bytes, duration); `bm sync --report`
- [ ] **Multi-device warning**: Document that users should not run `--watch` on multiple devices simultaneously
- [ ] **Version control guidance**: Document pattern for users to use Git locally in their sync directory if they want version history
- [ ] **Docs polish**: cloud-mode toggle, mount↔bisync directory isolation, conflict semantics, quick start, migration guide, short demo clip/GIF

## 🔜 Next
- [ ] **Observability commands**: `bm conflicts list`, `bm sync history` to view sync reports and conflicts
- [ ] **Conflict resolution UI**: `bm conflicts resolve <file>` to interactively pick winner from conflict files
- [ ] **Selective sync**: allow include/exclude by project; per-project profile (safe/balanced/fast)

## 🧭 Later
- [ ] **Near real-time sync**: File watcher → targeted `rclone copy` for individual files (keep bisync as backstop)
- [ ] **Sharing / scoped tokens**: cross-tenant/project access
- [ ] **Bandwidth controls & backpressure**: policy for large repos
- [ ] **Client-side encryption (optional)**: with clear trade-offs

## 📏 Acceptance criteria (for "Now" items)
- [ ] Local process lock prevents concurrent bisync runs on same device
- [ ] rclone bisync conflict files visible and documented (`file.conflict1.md`, `file.conflict2.md`)
- [ ] `bm sync --report` generates parsable JSON with sync statistics
- [ ] Documentation clearly warns about multi-device `--watch` mode
- [ ] Documentation shows users how to use Git locally for version history

## What We're NOT Building (Deferred to rclone)
- ❌ Custom `.bmmeta` sidecars (rclone tracks state in `.bisync/` workdir)
- ❌ Custom conflict detection (rclone bisync already does 3-way merge detection)
- ❌ Tombstone files (S3 versioning + rclone delete tracking handles this)
- ❌ Distributed lease (low probability issue, rclone detects state divergence)
- ❌ Rename/move tracking (rclone has size+modtime heuristics built-in)

## Implementation Summary

**Current State (SPEC-9):**
- ✅ rclone bisync with 3 profiles (safe/balanced/fast)
- ✅ `--max-delete` safety limits (10/25/50 files)
- ✅ `--conflict-resolve=newer` for auto-resolution
- ✅ Watch mode: `bm sync --watch` (60s intervals)
- ✅ Integrity checking: `bm cloud check`
- ✅ Mount vs bisync directory isolation

**What's Needed (This Spec):**
1. **Process lock** - Simple file-based lock in `~/.basic-memory/sync.lock`
2. **Sync reports** - Parse rclone output, save to `~/.basic-memory/sync-history/`
3. **Documentation** - Multi-device warnings, conflict resolution workflow, Git usage pattern

**User Model:**
- Cloud is always the winner on conflict (cloud-primary)
- rclone creates `.conflict` files for divergent edits
- Users who want version history just use Git in their local sync directory
- Users warned: don't run `--watch` on multiple devices

## Decision Rationale & Trade-offs

### Why Trust rclone Instead of Custom Conflict Handling?

**rclone bisync already provides:**
- 3-way merge detection (compares local, remote, and last-known state)
- File state tracking in `.bisync/` workdir (hashes, modtimes)
- Automatic conflict file creation: `file.conflict1.md`, `file.conflict2.md`
- Rename detection via size+modtime heuristics
- Delete tracking (prevents resurrection of deleted files)
- Battle-tested with extensive edge case handling

**What we'd have to build with custom approach:**
- Per-file metadata tracking (`.bmmeta` sidecars)
- 3-way diff algorithm
- Conflict detection logic
- Tombstone files for deletes
- Rename/move detection
- Testing for all edge cases

**Decision:** Use what rclone already does well. Don't reinvent the wheel.

### Why Let Users Use Git Locally Instead of Building Versioning?

**The simplest solution: Just use Git**

Users who want version history can literally just use Git in their sync directory:

```bash
cd ~/basic-memory-cloud-sync/
git init
git add .
git commit -m "backup"

# Push to their own GitHub if they want
git remote add origin git@github.com:user/my-knowledge.git
git push
```

**Why this is perfect:**
- ✅ We build nothing
- ✅ Users who want Git... just use Git
- ✅ Users who don't care... don't need to
- ✅ rclone bisync already handles sync conflicts
- ✅ Users own their data, they can version it however they want (Git, Time Machine, etc.)

**What we'd have to build for S3 versioning:**
- API to enable versioning on Tigris buckets
  - **Problem**: Tigris doesn't support S3 bucket versioning
- Restore commands: `bm cloud restore --version-id`
- Version listing: `bm cloud versions <path>`
- Lifecycle policies for version retention
- Documentation and user education

**What we'd have to build for SPEC-14 Git integration:**
- Committer service (daemon watching `/app/data/`)
- Puller service (webhook handler for GitHub pushes)
- Git LFS for large files
- Loop prevention between Git ↔ bisync ↔ local
- Merge conflict handling at TWO layers (rclone + Git)
- Webhook infrastructure and monitoring

**Decision:** Don't build version control. Document the pattern. "The easiest problem to solve is the one you avoid."

**When to revisit:** Teams/multi-user features where server-side version control becomes necessary for collaboration.

### Why No Distributed Lease?

**Low probability issue:**
- Requires user to manually run `bm sync` on multiple devices at exact same time
- Most users run `--watch` on one primary device
- rclone bisync detects state divergence and fails safely

**Safety nets in place:**
- Local process lock prevents concurrent runs on same device
- rclone bisync aborts if bucket state changed during sync
- S3 versioning recovers from any overwrites
- Documentation warns against multi-device `--watch`

**Failure mode:**
```bash
# Device A and B sync simultaneously
Device A: bm sync → succeeds
Device B: bm sync → "Error: path has changed, run --resync"

# User fixes with resync
Device B: bm sync --resync → establishes new baseline
```

**Decision:** Document the issue, add local lock, defer distributed coordination until users report actual problems.

### Cloud-Primary Conflict Model

**User mental model:**
- Cloud is the source of truth (like Dropbox/iCloud)
- Local is working copy
- On conflict: cloud wins, local edits → `.conflict` file
- User manually picks winner

**Why this works:**
- Simpler than bidirectional merge (no automatic resolution risk)
- Matches user expectations from Dropbox
- S3 versioning provides safety net for overwrites
- Clear recovery path: restore from S3 version if needed

**Example workflow:**
```bash
# Edit file on Device A and Device B while offline
# Both devices come online and sync

Device A: bm sync
# → Pushes to cloud first, becomes canonical version

Device B: bm sync
# → Detects conflict
# → Cloud version: work/notes.md
# → Local version: work/notes.md.conflict1
# → User manually merges or picks winner

# Restore if needed
bm cloud restore work/notes.md --version-id abc123
```

## Implementation Details

### 1. Local Process Lock

```python
# ~/.basic-memory/sync.lock
import os
import psutil
from pathlib import Path

class SyncLock:
    def __init__(self):
        self.lock_file = Path.home() / '.basic-memory' / 'sync.lock'

    def acquire(self):
        if self.lock_file.exists():
            pid = int(self.lock_file.read_text())
            if psutil.pid_exists(pid):
                raise BisyncError(
                    f"Sync already running (PID {pid}). "
                    f"Wait for completion or kill stale process."
                )
            # Stale lock, remove it
            self.lock_file.unlink()

        self.lock_file.write_text(str(os.getpid()))

    def release(self):
        if self.lock_file.exists():
            self.lock_file.unlink()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *args):
        self.release()

# Usage
with SyncLock():
    run_rclone_bisync()
```

### 3. Sync Report Parsing

```python
# Parse rclone bisync output
import json
from datetime import datetime
from pathlib import Path

def parse_sync_report(rclone_output: str, duration: float, exit_code: int) -> dict:
    """Parse rclone bisync output into structured report."""

    # rclone bisync outputs lines like:
    # "Synching Path1 /local/path with Path2 remote:bucket"
    # "- Path1    File was copied to Path2"
    # "Bisync successful"

    report = {
        "timestamp": datetime.now().isoformat(),
        "duration_seconds": duration,
        "exit_code": exit_code,
        "success": exit_code == 0,
        "files_created": 0,
        "files_updated": 0,
        "files_deleted": 0,
        "conflicts": [],
        "errors": []
    }

    for line in rclone_output.split('\n'):
        if 'was copied to' in line:
            report['files_created'] += 1
        elif 'was updated in' in line:
            report['files_updated'] += 1
        elif 'was deleted from' in line:
            report['files_deleted'] += 1
        elif '.conflict' in line:
            report['conflicts'].append(line.strip())
        elif 'ERROR' in line:
            report['errors'].append(line.strip())

    return report

def save_sync_report(report: dict):
    """Save sync report to history."""
    history_dir = Path.home() / '.basic-memory' / 'sync-history'
    history_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    report_file = history_dir / f'{timestamp}.json'

    report_file.write_text(json.dumps(report, indent=2))

# Usage in run_bisync()
start_time = time.time()
result = subprocess.run(bisync_cmd, capture_output=True, text=True)
duration = time.time() - start_time

report = parse_sync_report(result.stdout, duration, result.returncode)
save_sync_report(report)

if report['conflicts']:
    console.print(f"[yellow]⚠ {len(report['conflicts'])} conflict(s) detected[/yellow]")
    console.print("[dim]Run 'bm conflicts list' to view[/dim]")
```

### 4. User Commands

```bash
# View sync history
bm sync history
# → Lists recent syncs from ~/.basic-memory/sync-history/*.json
# → Shows: timestamp, duration, files changed, conflicts, errors

# View current conflicts
bm conflicts list
# → Scans sync directory for *.conflict* files
# → Shows: file path, conflict versions, timestamps

# Restore from S3 version
bm cloud restore work/notes.md --version-id abc123
# → Uses aws s3api get-object with version-id
# → Downloads to original path

bm cloud restore work/notes.md --timestamp "2025-10-03 14:30"
# → Lists versions, finds closest to timestamp
# → Downloads that version

# List file versions
bm cloud versions work/notes.md
# → Uses aws s3api list-object-versions
# → Shows: version-id, timestamp, size, author

# Interactive conflict resolution
bm conflicts resolve work/notes.md
# → Shows both versions side-by-side
# → Prompts: Keep local, keep cloud, merge manually, restore from S3 version
# → Cleans up .conflict files after resolution
```

## Success Metrics & Monitoring

**Phase 1 (v1) - Basic Safety:**
- [ ] Conflict detection rate < 5% of syncs (measure in telemetry)
- [ ] User can resolve conflicts within 5 minutes (UX testing)
- [ ] Documentation prevents 90% of multi-device issues

**Phase 2 (v2) - Observability:**
- [ ] 80% of users check `bm sync history` when troubleshooting
- [ ] Average time to restore from S3 version < 2 minutes
- 
- [ ] Conflict resolution success rate > 95%

**What to measure:**
```python
# Telemetry in sync reports
{
    "conflict_rate": conflicts / total_syncs,
    "multi_device_collisions": count_state_divergence_errors,
    "version_restores": count_restore_operations,
    "avg_sync_duration": sum(durations) / count,
    "max_delete_trips": count_max_delete_aborts
}
```

**When to add distributed lease:**
- Multi-device collision rate > 5% of syncs
- User complaints about state divergence errors
- Evidence that local lock isn't sufficient

**When to revisit Git (SPEC-14):**
- Teams feature launches (multi-user collaboration)
- Users request commit messages / audit trail
- PR-based review workflow becomes valuable

## Links
- SPEC-9: `specs/spec-9-multi-project-bisync`
- SPEC-14: `specs/spec-14-cloud-git-versioning` (deferred in favor of S3 versioning)
- rclone bisync docs: https://rclone.org/bisync/
- Tigris S3 versioning: https://www.tigrisdata.com/docs/buckets/versioning/

---
**Owner:** <assign>  |  **Review cadence:** weekly in standup  |  **Last updated:** 2025-10-03
