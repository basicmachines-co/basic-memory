# BASIC_MEMORY_HOME Environment Variable

**Status**: Existing (clarified in v0.15.0)
**Related**: project-root-env-var.md

## What It Is

`BASIC_MEMORY_HOME` specifies the location of your **default "main" project**. This is the primary directory where Basic Memory stores knowledge files when no other project is specified.

## Quick Reference

```bash
# Default (if not set)
~/basic-memory

# Custom location
export BASIC_MEMORY_HOME=/Users/you/Documents/knowledge-base
```

## How It Works

### Default Project Location

When Basic Memory initializes, it creates a "main" project:

```python
# Without BASIC_MEMORY_HOME
projects = {
    "main": "~/basic-memory"  # Default
}

# With BASIC_MEMORY_HOME set
export BASIC_MEMORY_HOME=/Users/you/custom-location
projects = {
    "main": "/Users/you/custom-location"  # Uses env var
}
```

### Only Affects "main" Project

**Important:** `BASIC_MEMORY_HOME` ONLY sets the path for the "main" project. Other projects are unaffected.

```bash
export BASIC_MEMORY_HOME=/Users/you/my-knowledge

# config.json will have:
{
  "projects": {
    "main": "/Users/you/my-knowledge",    # ← From BASIC_MEMORY_HOME
    "work": "/Users/you/work-notes",       # ← Independently configured
    "personal": "/Users/you/personal-kb"   # ← Independently configured
  }
}
```

## Relationship with BASIC_MEMORY_PROJECT_ROOT

These are **separate** environment variables with **different purposes**:

| Variable | Purpose | Scope | Default |
|----------|---------|-------|---------|
| `BASIC_MEMORY_HOME` | Where "main" project lives | Single project | `~/basic-memory` |
| `BASIC_MEMORY_PROJECT_ROOT` | Security boundary for ALL projects | All projects | None (unrestricted) |

### Using Together

```bash
# Common containerized setup
export BASIC_MEMORY_HOME=/app/data/basic-memory          # Main project location
export BASIC_MEMORY_PROJECT_ROOT=/app/data               # All projects must be under here
```

**Result:**
- Main project created at `/app/data/basic-memory`
- All other projects must be under `/app/data/`
- Provides both convenience and security

### Comparison Table

| Scenario | BASIC_MEMORY_HOME | BASIC_MEMORY_PROJECT_ROOT | Result |
|----------|-------------------|---------------------------|---------|
| **Default** | Not set | Not set | Main at `~/basic-memory`, projects anywhere |
| **Custom main** | `/Users/you/kb` | Not set | Main at `/Users/you/kb`, projects anywhere |
| **Containerized** | `/app/data/main` | `/app/data` | Main at `/app/data/main`, all projects under `/app/data/` |
| **Secure SaaS** | `/app/tenant-123/main` | `/app/tenant-123` | Main at `/app/tenant-123/main`, tenant isolated |

## Use Cases

### Personal Setup (Default)

```bash
# Use default location
# BASIC_MEMORY_HOME not set

# Main project created at:
~/basic-memory/
```

### Custom Location

```bash
# Store in Documents folder
export BASIC_MEMORY_HOME=~/Documents/BasicMemory

# Main project created at:
~/Documents/BasicMemory/
```

### Synchronized Cloud Folder

```bash
# Store in Dropbox/iCloud
export BASIC_MEMORY_HOME=~/Dropbox/BasicMemory

# Main project syncs via Dropbox:
~/Dropbox/BasicMemory/
```

### Docker Deployment

```bash
# Mount volume for persistence
docker run \
  -e BASIC_MEMORY_HOME=/app/data/basic-memory \
  -v $(pwd)/data:/app/data \
  basic-memory:latest

# Main project persists at:
./data/basic-memory/  # (host)
/app/data/basic-memory/  # (container)
```

### Multi-User System

```bash
# Per-user isolation
export BASIC_MEMORY_HOME=/home/$USER/basic-memory

# Alice's main project:
/home/alice/basic-memory/

# Bob's main project:
/home/bob/basic-memory/
```

## Configuration Examples

### Basic Setup

```bash
# .bashrc or .zshrc
export BASIC_MEMORY_HOME=~/Documents/knowledge
```

### Docker Compose

```yaml
services:
  basic-memory:
    environment:
      BASIC_MEMORY_HOME: /app/data/basic-memory
    volumes:
      - ./data:/app/data
```

### Kubernetes

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: basic-memory-config
data:
  BASIC_MEMORY_HOME: "/app/data/basic-memory"
---
apiVersion: v1
kind: Pod
spec:
  containers:
  - name: basic-memory
    envFrom:
    - configMapRef:
        name: basic-memory-config
```

### systemd Service

```ini
[Service]
Environment="BASIC_MEMORY_HOME=/var/lib/basic-memory"
ExecStart=/usr/local/bin/basic-memory serve
```

## Migration

### Changing BASIC_MEMORY_HOME

If you need to change the location:

**Option 1: Move files**
```bash
# Stop services
bm sync --stop

# Move data
mv ~/basic-memory ~/Documents/knowledge

# Update environment
export BASIC_MEMORY_HOME=~/Documents/knowledge

# Restart
bm sync
```

**Option 2: Copy and sync**
```bash
# Copy to new location
cp -r ~/basic-memory ~/Documents/knowledge

# Update environment
export BASIC_MEMORY_HOME=~/Documents/knowledge

# Verify
bm status

# Remove old location once verified
rm -rf ~/basic-memory
```

### From v0.14.x

No changes needed - `BASIC_MEMORY_HOME` works the same way:

```bash
# v0.14.x and v0.15.0+ both use:
export BASIC_MEMORY_HOME=~/my-knowledge
```

## Common Patterns

### Development vs Production

```bash
# Development (.bashrc)
export BASIC_MEMORY_HOME=~/dev/basic-memory-dev

# Production (systemd/docker)
export BASIC_MEMORY_HOME=/var/lib/basic-memory
```

### Shared Team Setup

```bash
# Shared network drive
export BASIC_MEMORY_HOME=/mnt/shared/team-knowledge

# Note: Use with caution, consider file locking
```

### Backup Strategy

```bash
# Primary location
export BASIC_MEMORY_HOME=~/basic-memory

# Automated backup script
rsync -av ~/basic-memory/ ~/Backups/basic-memory-$(date +%Y%m%d)/
```

## Verification

### Check Current Value

```bash
# View environment variable
echo $BASIC_MEMORY_HOME

# View resolved config
bm project list
# Shows actual path for "main" project
```

### Verify Main Project Location

```python
from basic_memory.config import ConfigManager

config = ConfigManager().config
print(config.projects["main"])
# Shows where "main" project is located
```

## Troubleshooting

### Main Project Not at Expected Location

**Problem:** Files not where you expect

**Check:**
```bash
# What's the environment variable?
echo $BASIC_MEMORY_HOME

# Where is main project actually?
bm project list | grep main
```

**Solution:** Set environment variable and restart

### Permission Errors

**Problem:** Can't write to BASIC_MEMORY_HOME location

```bash
$ bm sync
Error: Permission denied: /var/lib/basic-memory
```

**Solution:**
```bash
# Fix permissions
sudo chown -R $USER:$USER /var/lib/basic-memory

# Or use accessible location
export BASIC_MEMORY_HOME=~/basic-memory
```

### Conflicts with PROJECT_ROOT

**Problem:** BASIC_MEMORY_HOME outside PROJECT_ROOT

```bash
export BASIC_MEMORY_HOME=/Users/you/kb
export BASIC_MEMORY_PROJECT_ROOT=/app/data

# Error: /Users/you/kb not under /app/data
```

**Solution:** Align both variables
```bash
export BASIC_MEMORY_HOME=/app/data/basic-memory
export BASIC_MEMORY_PROJECT_ROOT=/app/data
```

## Best Practices

1. **Use absolute paths:**
   ```bash
   export BASIC_MEMORY_HOME=/Users/you/knowledge  # ✓
   # not: export BASIC_MEMORY_HOME=~/knowledge    # ✗ (may not expand)
   ```

2. **Document the location:**
   - Add comment in shell config
   - Document for team if shared

3. **Backup regularly:**
   - Main project contains your primary knowledge
   - Automate backups of this directory

4. **Consider PROJECT_ROOT for security:**
   - Use both together in production/containers

5. **Test changes:**
   - Verify with `bm project list` after changing

## See Also

- `project-root-env-var.md` - Security constraints for all projects
- `env-var-overrides.md` - Environment variable precedence
- Project management documentation
