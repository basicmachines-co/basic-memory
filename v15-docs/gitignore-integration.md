# .gitignore Integration

**Status**: New Feature
**PR**: #314
**Impact**: Improved security and reduced noise

## What's New

v0.15.0 integrates `.gitignore` support into the sync process. Files matching patterns in `.gitignore` are automatically skipped during synchronization, preventing sensitive files and build artifacts from being indexed.

## How It Works

### Ignore Pattern Sources

Basic Memory combines patterns from two sources:

1. **Global user patterns**: `~/.basic-memory/.bmignore`
   - User's personal ignore patterns
   - Applied to all projects
   - Useful for global exclusions (OS files, editor configs)

2. **Project-specific patterns**: `{project}/.gitignore`
   - Project's standard gitignore file
   - Applied to that project only
   - Follows standard gitignore syntax

### Automatic .gitignore Respect

When syncing, Basic Memory:
1. Loads patterns from `~/.basic-memory/.bmignore` (if exists)
2. Loads patterns from `.gitignore` in project root (if exists)
3. Combines both pattern sets
4. Skips files matching any pattern
5. Does not index ignored files

### Pattern Matching

Uses standard gitignore syntax:
```gitignore
# Comments are ignored
*.log                    # Ignore all .log files
build/                   # Ignore build directory
node_modules/           # Ignore node_modules
.env                    # Ignore .env files
!important.log          # Exception: don't ignore this file
```

## Benefits

### 1. Security

**Prevents indexing sensitive files:**
```gitignore
# Sensitive files automatically skipped
.env
.env.*
secrets.json
credentials/
*.key
*.pem
cloud-auth.json
```

**Result:** Secrets never indexed or synced

### 2. Performance

**Skips unnecessary files:**
```gitignore
# Build artifacts and caches
node_modules/
__pycache__/
.pytest_cache/
dist/
build/
*.pyc
```

**Result:** Faster sync, smaller database

### 3. Reduced Noise

**Ignores OS and editor files:**
```gitignore
# macOS
.DS_Store
.AppleDouble

# Linux
*~
.directory

# Windows
Thumbs.db
desktop.ini

# Editors
.vscode/
.idea/
*.swp
```

**Result:** Cleaner knowledge base

## Setup

### Default Behavior

If no `.gitignore` exists, Basic Memory uses built-in patterns:

```gitignore
# Default patterns
.git
.DS_Store
node_modules
__pycache__
.pytest_cache
.env
```

### Global .bmignore (Optional)

Create global ignore patterns for all projects:

```bash
# Create global ignore file
cat > ~/.basic-memory/.bmignore <<'EOF'
# OS files (apply to all projects)
.DS_Store
.AppleDouble
Thumbs.db
desktop.ini
*~

# Editor files (apply to all projects)
.vscode/
.idea/
*.swp
*.swo

# Always ignore these
.env
.env.*
*.secret
EOF
```

**Use cases:**
- Personal preferences (editor configs)
- OS-specific files
- Global security rules

### Project-Specific .gitignore

Create `.gitignore` in project root for project-specific patterns:

```bash
# Create .gitignore
cat > ~/basic-memory/.gitignore <<'EOF'
# Project-specific secrets
credentials.json
*.key

# Project build artifacts
dist/
build/
*.pyc
__pycache__/
node_modules/

# Project-specific temp files
*.tmp
*.cache
EOF
```

**Use cases:**
- Build artifacts
- Dependencies (node_modules, venv)
- Project-specific secrets

### Sync with .gitignore and .bmignore

```bash
# Sync respects both .bmignore and .gitignore
bm sync

# Ignored files are skipped
# → ".DS_Store skipped (global .bmignore)"
# → ".env skipped (gitignored)"
# → "node_modules/ skipped (gitignored)"
```

**Pattern precedence:**
1. Global `.bmignore` patterns checked first
2. Project `.gitignore` patterns checked second
3. If either matches, file is skipped

## Use Cases

### Git Repository as Knowledge Base

Perfect synergy when using git for version control:

```bash
# Project structure
~/my-knowledge/
├── .git/              # ← git repo
├── .gitignore         # ← shared ignore rules
├── notes/
│   ├── public.md      # ← synced
│   └── private.md     # ← synced
├── .env               # ← ignored by git AND sync
└── build/             # ← ignored by git AND sync
```

**Benefits:**
- Same ignore rules for git and sync
- Consistent behavior
- No sensitive files in either system

### Sensitive Information

```gitignore
# .gitignore
*.key
*.pem
credentials.json
secrets/
.env*
```

**Result:**
```bash
$ bm sync
Syncing...
→ Skipped: api-key.pem (gitignored)
→ Skipped: .env (gitignored)
→ Skipped: secrets/passwords.txt (gitignored)
✓ Synced 15 files (3 skipped)
```

### Development Environment

```gitignore
# Project-specific
node_modules/
venv/
.venv/
__pycache__/
*.pyc
.pytest_cache/
.coverage
.tox/
dist/
build/
*.egg-info/
```

**Result:** Clean knowledge base without dev noise

## Pattern Examples

### Common Patterns

**Secrets:**
```gitignore
.env
.env.*
*.key
*.pem
*secret*
*password*
credentials.json
auth.json
```

**Build Artifacts:**
```gitignore
dist/
build/
*.o
*.pyc
*.class
*.jar
node_modules/
__pycache__/
```

**OS Files:**
```gitignore
.DS_Store
.AppleDouble
.LSOverride
Thumbs.db
desktop.ini
*~
```

**Editors:**
```gitignore
.vscode/
.idea/
*.swp
*.swo
*~
.project
.settings/
```

### Advanced Patterns

**Exceptions (!):**
```gitignore
# Ignore all logs
*.log

# EXCEPT this one
!important.log
```

**Directory-specific:**
```gitignore
# Ignore only in root
/.env

# Ignore everywhere
**/.env
```

**Wildcards:**
```gitignore
# Multiple extensions
*.{log,tmp,cache}

# Specific patterns
test_*.py
*_backup.*
```

## Integration with Cloud Sync

### .bmignore Files Overview

Basic Memory uses `.bmignore` in two contexts:

1. **Global user patterns**: `~/.basic-memory/.bmignore`
   - Used for **local sync**
   - Standard gitignore syntax
   - Applied to all projects

2. **Cloud bisync filters**: `.bmignore.rclone`
   - Used for **cloud sync**
   - rclone filter format
   - Auto-generated from .gitignore patterns

### Automatic Pattern Conversion

Cloud bisync converts .gitignore to rclone filter format:

```bash
# Source: .gitignore (standard gitignore syntax)
node_modules/
*.log
.env

# Generated: .bmignore.rclone (rclone filter format)
- node_modules/**
- *.log
- .env
```

**Automatic conversion:** Basic Memory handles conversion during cloud sync

### Sync Workflow

1. **Local sync** (respects .bmignore + .gitignore)
   ```bash
   bm sync
   # → Loads ~/.basic-memory/.bmignore (global)
   # → Loads {project}/.gitignore (project-specific)
   # → Skips files matching either
   ```

2. **Cloud bisync** (respects .bmignore.rclone)
   ```bash
   bm cloud bisync
   # → Generates .bmignore.rclone from .gitignore
   # → Uses rclone filters for cloud sync
   # → Skips same files as local sync
   ```

**Result:** Consistent ignore behavior across local and cloud sync

## Verification

### Check What's Ignored

```bash
# Dry-run sync to see what's skipped
bm sync --dry-run

# Output shows:
# → Syncing: notes/ideas.md
# → Skipped: .env (gitignored)
# → Skipped: node_modules/package.json (gitignored)
```

### List Ignore Patterns

```bash
# View .gitignore
cat .gitignore

# View effective patterns
bm sync --show-patterns
```

### Test Pattern Matching

```bash
# Check if file matches pattern
git check-ignore -v path/to/file

# Example:
git check-ignore -v .env
# → .gitignore:5:.env    .env
```

## Migration

### From v0.14.x

**Before v0.15.0:**
- .gitignore patterns not respected
- All files synced, including ignored ones
- Manual exclude rules needed

**v0.15.0+:**
- .gitignore automatically respected
- Ignored files skipped
- No manual configuration needed

**Action:** Just add/update .gitignore - next sync uses it

### Cleaning Up Already-Indexed Files

If ignored files were previously synced:

```bash
# Option 1: Re-sync (re-indexes from scratch)
bm sync --force-resync

# Option 2: Delete and re-sync specific project
bm project remove old-project
bm project add clean-project ~/basic-memory
bm sync --project clean-project
```

## Troubleshooting

### File Not Being Ignored

**Problem:** File still synced despite being in .gitignore

**Check:**
1. Is .gitignore in project root?
   ```bash
   ls -la ~/basic-memory/.gitignore
   ```

2. Is pattern correct?
   ```bash
   # Test pattern
   git check-ignore -v path/to/file
   ```

3. Is file already indexed?
   ```bash
   # Force resync
   bm sync --force-resync
   ```

### Pattern Not Matching

**Problem:** Pattern doesn't match expected files

**Common issues:**
```gitignore
# ✗ Wrong: Won't match subdirectories
node_modules

# ✓ Correct: Matches recursively
node_modules/
**/node_modules/

# ✗ Wrong: Only matches in root
/.env

# ✓ Correct: Matches everywhere
.env
**/.env
```

### .gitignore Not Found

**Problem:** No .gitignore file exists

**Solution:**
```bash
# Create default .gitignore
cat > ~/basic-memory/.gitignore <<'EOF'
.git
.DS_Store
.env
node_modules/
__pycache__/
EOF

# Re-sync
bm sync
```

## Best Practices

### 1. Use Global .bmignore for Personal Preferences

Set global patterns once, apply to all projects:

```bash
# Create global ignore file
cat > ~/.basic-memory/.bmignore <<'EOF'
# Personal editor/OS preferences
.DS_Store
.vscode/
.idea/
*.swp

# Never sync these anywhere
.env
.env.*
EOF
```

### 2. Use .gitignore for Project-Specific Patterns

Even if not using git, create .gitignore for project-specific sync:

```bash
# Create project .gitignore
cat > .gitignore <<'EOF'
# Project build artifacts
dist/
node_modules/
__pycache__/

# Project secrets
credentials.json
*.key
EOF
```

### 3. Ignore Secrets First

Start with security (both global and project-specific):
```bash
# Global: ~/.basic-memory/.bmignore
.env*
*.key
*.pem

# Project: .gitignore
credentials.json
secrets/
api-keys.txt
```

### 4. Ignore Build Artifacts

Reduce noise in project .gitignore:
```gitignore
# Build outputs
dist/
build/
node_modules/
__pycache__/
*.pyc
```

### 5. Use Standard Templates

Start with community templates for .gitignore:
- [GitHub .gitignore templates](https://github.com/github/gitignore)
- Language-specific ignores (Python, Node, etc.)
- Framework-specific ignores

### 6. Test Your Patterns

```bash
# Verify pattern works
git check-ignore -v file.log

# Test sync
bm sync --dry-run
```

## See Also

- `cloud-bisync.md` - Cloud sync and .bmignore.rclone conversion
- `env-file-removal.md` - Why .env files should be ignored
- gitignore documentation: https://git-scm.com/docs/gitignore
- GitHub gitignore templates: https://github.com/github/gitignore

## Summary

Basic Memory provides flexible ignore patterns through:
- **Global**: `~/.basic-memory/.bmignore` - personal preferences across all projects
- **Project**: `.gitignore` - project-specific patterns
- **Cloud**: `.bmignore.rclone` - auto-generated for cloud sync

Use global .bmignore for OS/editor files, project .gitignore for build artifacts and secrets.
