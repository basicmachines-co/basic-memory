# Bug Fixes and Improvements

**Status**: Bug Fixes
**Version**: v0.15.0
**Impact**: Stability, reliability, platform compatibility

## Overview

v0.15.0 includes 13+ bug fixes addressing entity conflicts, URL handling, file operations, and platform compatibility. These fixes improve stability and eliminate edge cases that could cause errors.

## Key Fixes

### 1. Entity Upsert Conflict Resolution (#328)

**Problem:**
Database-level conflicts when upserting entities with same title/folder caused crashes.

**Fix:**
Simplified entity upsert to use database-level conflict resolution with `ON CONFLICT` clause.

**Before:**
```python
# Manual conflict checking (error-prone)
existing = await get_entity_by_title(title, folder)
if existing:
    await update_entity(existing.id, data)
else:
    await insert_entity(data)
# → Could fail if concurrent insert
```

**After:**
```python
# Database handles conflict
await db.execute("""
    INSERT INTO entities (title, folder, content)
    VALUES (?, ?, ?)
    ON CONFLICT (title, folder) DO UPDATE SET content = excluded.content
""")
# → Always works, even with concurrent access
```

**Benefit:** Eliminates race conditions, more reliable writes

### 2. memory:// URL Underscore Normalization (#329)

**Problem:**
Underscores in memory:// URLs weren't normalized to hyphens, causing lookups to fail.

**Fix:**
Normalize underscores to hyphens when resolving memory:// URLs.

**Before:**
```python
# URL with underscores
url = "memory://my_note"
entity = await resolve_url(url)
# → Not found! (permalink is "my-note")
```

**After:**
```python
# Automatic normalization
url = "memory://my_note"
entity = await resolve_url(url)
# → Found! (my_note → my-note)
```

**Examples:**
- `memory://my_note` → finds entity with permalink `my-note`
- `memory://user_guide` → finds entity with permalink `user-guide`
- `memory://api_docs` → finds entity with permalink `api-docs`

**Benefit:** More forgiving URL matching, fewer lookup failures

### 3. .gitignore File Filtering (#287, #285)

**Problem:**
Sync process didn't respect .gitignore patterns, indexing sensitive files and build artifacts.

**Fix:**
Integrated .gitignore support - files matching patterns are automatically skipped during sync.

**Before:**
```bash
bm sync
# → Indexed .env files
# → Indexed node_modules/
# → Indexed build artifacts
```

**After:**
```bash
# .gitignore
.env
node_modules/
dist/

bm sync
# → Skipped .env (gitignored)
# → Skipped node_modules/ (gitignored)
# → Skipped dist/ (gitignored)
```

**Benefit:** Better security, cleaner knowledge base, faster sync

**See:** `gitignore-integration.md` for full details

### 4. move_note File Extension Handling (#281)

**Problem:**
`move_note` failed when destination path included or omitted `.md` extension inconsistently.

**Fix:**
Automatically handle file extensions - works with or without `.md`.

**Before:**
```python
# Had to match exactly
await move_note("My Note", "new-folder/my-note.md")  # ✓
await move_note("My Note", "new-folder/my-note")     # ✗ Failed
```

**After:**
```python
# Both work
await move_note("My Note", "new-folder/my-note.md")  # ✓ Works
await move_note("My Note", "new-folder/my-note")     # ✓ Works (adds .md)
```

**Automatic handling:**
- Input without `.md` → adds `.md`
- Input with `.md` → uses as-is
- Always creates valid markdown file

**Benefit:** More forgiving API, fewer errors

### 5. .env File Loading Removed (#330)

**Problem:**
Automatic .env file loading created security vulnerability - could load untrusted files.

**Fix:**
Removed automatic .env loading. Environment variables must be set explicitly.

**Impact:** Breaking change for users relying on .env files

**Migration:**
```bash
# Before: Used .env file
# .env
BASIC_MEMORY_LOG_LEVEL=DEBUG

# After: Use explicit export
export BASIC_MEMORY_LOG_LEVEL=DEBUG

# Or use direnv
# .envrc (git-ignored)
export BASIC_MEMORY_LOG_LEVEL=DEBUG
```

**Benefit:** Better security, explicit configuration

**See:** `env-file-removal.md` for migration guide

### 6. Python 3.13 Compatibility

**Problem:**
Code not tested with Python 3.13, potential compatibility issues.

**Fix:**
- Added Python 3.13 to CI test matrix
- Fixed deprecation warnings
- Verified all dependencies compatible
- Updated type hints for 3.13

**Before:**
```yaml
# .github/workflows/test.yml
python-version: ["3.10", "3.11", "3.12"]
```

**After:**
```yaml
# .github/workflows/test.yml
python-version: ["3.10", "3.11", "3.12", "3.13"]
```

**Benefit:** Full Python 3.13 support, future-proof

## Additional Fixes

### Minimum Timeframe Enforcement (#318)

**Problem:**
`recent_activity` with very short timeframes caused timezone issues.

**Fix:**
Enforce minimum 1-day timeframe to handle timezone edge cases.

```python
# Before: Could use any timeframe
await recent_activity(timeframe="1h")  # Timezone issues

# After: Minimum 1 day
await recent_activity(timeframe="1h")  # → Auto-adjusted to "1d"
```

### Permalink Collision Prevention

**Problem:**
Strict link resolution could create duplicate permalinks.

**Fix:**
Enhanced permalink uniqueness checking to prevent collisions.

### DateTime JSON Schema (#312)

**Problem:**
MCP validation failed on DateTime fields - missing proper JSON schema format.

**Fix:**
Added proper `format: "date-time"` annotations for MCP compatibility.

```python
# Before: No format
created_at: datetime

# After: With format
created_at: datetime = Field(json_schema_extra={"format": "date-time"})
```

## Testing Coverage

### Automated Tests

All fixes include comprehensive tests:

```bash
# Entity upsert conflict
tests/services/test_entity_upsert.py

# URL normalization
tests/mcp/test_build_context_validation.py

# File extension handling
tests/mcp/test_tool_move_note.py

# gitignore integration
tests/sync/test_gitignore.py
```

### Manual Testing Checklist

- [x] Entity upsert with concurrent access
- [x] memory:// URLs with underscores
- [x] .gitignore file filtering
- [x] move_note with/without .md extension
- [x] .env file not auto-loaded
- [x] Python 3.13 compatibility

## Migration Guide

### If You're Affected by These Bugs

**Entity Conflicts:**
- No action needed - automatically fixed

**memory:// URLs:**
- No action needed - URLs now more forgiving
- Previously broken URLs should work now

**.gitignore Integration:**
- Create `.gitignore` if you don't have one
- Add patterns for files to skip

**move_note:**
- No action needed - both formats now work
- Can simplify code that manually added `.md`

**.env Files:**
- See `env-file-removal.md` for full migration
- Use explicit environment variables or direnv

**Python 3.13:**
- Upgrade if desired: `pip install --upgrade basic-memory`
- Or stay on 3.10-3.12 (still supported)

## Verification

### Check Entity Upserts Work

```python
# Should not conflict
await write_note("Test", "Content", "folder")
await write_note("Test", "Updated", "folder")  # Updates, not errors
```

### Check URL Normalization

```python
# Both should work
context1 = await build_context("memory://my_note")
context2 = await build_context("memory://my-note")
# Both resolve to same entity
```

### Check .gitignore Respected

```bash
echo ".env" >> .gitignore
echo "SECRET=test" > .env
bm sync
# .env should be skipped
```

### Check move_note Extension

```python
# Both work
await move_note("Note", "folder/note.md")   # ✓
await move_note("Note", "folder/note")      # ✓
```

### Check .env Not Loaded

```bash
echo "BASIC_MEMORY_LOG_LEVEL=DEBUG" > .env
bm sync
# LOG_LEVEL not set (not auto-loaded)

export BASIC_MEMORY_LOG_LEVEL=DEBUG
bm sync
# LOG_LEVEL now set (explicit)
```

### Check Python 3.13

```bash
python3.13 --version
python3.13 -m pip install basic-memory
python3.13 -m basic_memory --version
```

## Known Issues (Fixed)

### Previously Reported, Now Fixed

1. ✅ Entity upsert conflicts (#328)
2. ✅ memory:// URL underscore handling (#329)
3. ✅ .gitignore not respected (#287, #285)
4. ✅ move_note extension issues (#281)
5. ✅ .env security vulnerability (#330)
6. ✅ Minimum timeframe issues (#318)
7. ✅ DateTime JSON schema (#312)
8. ✅ Permalink collisions
9. ✅ Python 3.13 compatibility

## Upgrade Notes

### From v0.14.x

All bug fixes apply automatically:

```bash
# Upgrade
pip install --upgrade basic-memory

# Restart MCP server
# Bug fixes active immediately
```

### Breaking Changes

Only one breaking change:

- ✅ .env file auto-loading removed (#330)
  - See `env-file-removal.md` for migration

All other fixes are backward compatible.

## Reporting New Issues

If you encounter issues:

1. Check this list to see if already fixed
2. Verify you're on v0.15.0+: `bm --version`
3. Report at: https://github.com/basicmachines-co/basic-memory/issues

## See Also

- `gitignore-integration.md` - .gitignore support details
- `env-file-removal.md` - .env migration guide
- GitHub issues for each fix
- v0.15.0 changelog
