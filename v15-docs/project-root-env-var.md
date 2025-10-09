# BASIC_MEMORY_PROJECT_ROOT Environment Variable

**Status**: New Feature
**PR**: #334
**Use Case**: Security, containerization, path constraints

## What's New

v0.15.0 introduces the `BASIC_MEMORY_PROJECT_ROOT` environment variable to constrain all project paths to a specific directory. This provides security and enables safe multi-tenant deployments.

## Quick Examples

### Containerized Deployment

```bash
# Docker/containerized environment
export BASIC_MEMORY_PROJECT_ROOT=/app/data
export BASIC_MEMORY_HOME=/app/data/basic-memory

# All projects must be under /app/data
bm project add my-project /app/data/my-project    # ✓ Allowed
bm project add my-project /tmp/unsafe             # ✗ Blocked
```

### Development Environment

```bash
# Local development - no constraint (default)
# BASIC_MEMORY_PROJECT_ROOT not set

# Projects can be anywhere
bm project add work ~/Documents/work-notes    # ✓ Allowed
bm project add personal ~/personal-kb         # ✓ Allowed
```

## How It Works

### Path Validation

When `BASIC_MEMORY_PROJECT_ROOT` is set:

1. **All project paths are validated** against the root
2. **Paths are sanitized** to prevent directory traversal
3. **Symbolic links are resolved** and verified
4. **Escape attempts are blocked** (e.g., `../../../etc`)

### Path Sanitization

```python
# Example internal validation
project_root = "/app/data"
user_path = "/app/data/../../../etc"

# Sanitized and validated
resolved_path = Path(user_path).resolve()
# → "/etc"

# Check if under project_root
if not str(resolved_path).startswith(project_root):
    raise ValueError("Path must be under /app/data")
```

## Configuration

### Set via Environment Variable

```bash
# In shell or .bashrc/.zshrc
export BASIC_MEMORY_PROJECT_ROOT=/app/data

# Or in Docker
docker run -e BASIC_MEMORY_PROJECT_ROOT=/app/data ...
```

### Docker Deployment

**Dockerfile:**
```dockerfile
# Set project root for path constraints
ENV BASIC_MEMORY_HOME=/app/data/basic-memory \
    BASIC_MEMORY_PROJECT_ROOT=/app/data
```

**docker-compose.yml:**
```yaml
services:
  basic-memory:
    environment:
      BASIC_MEMORY_HOME: /app/data/basic-memory
      BASIC_MEMORY_PROJECT_ROOT: /app/data
    volumes:
      - ./data:/app/data
```

### Kubernetes Deployment

```yaml
apiVersion: v1
kind: Pod
spec:
  containers:
  - name: basic-memory
    env:
    - name: BASIC_MEMORY_PROJECT_ROOT
      value: "/app/data"
    - name: BASIC_MEMORY_HOME
      value: "/app/data/basic-memory"
    volumeMounts:
    - name: data-volume
      mountPath: /app/data
```

## Use Cases

### 1. Container Security

**Problem:** Containers shouldn't create projects outside mounted volumes

**Solution:**
```bash
# Set project root to volume mount
export BASIC_MEMORY_PROJECT_ROOT=/app/data

# Projects confined to volume
bm project add notes /app/data/notes        # ✓
bm project add evil /etc/passwd             # ✗ Blocked
```

### 2. Multi-Tenant SaaS

**Problem:** Tenant A shouldn't access Tenant B's files

**Solution:**
```bash
# Per-tenant isolation
export BASIC_MEMORY_PROJECT_ROOT=/app/data/tenant-${TENANT_ID}

# Tenant can only create projects under their directory
bm project add my-notes /app/data/tenant-123/notes    # ✓
bm project add sneaky /app/data/tenant-456/notes      # ✗ Blocked
```

### 3. Shared Hosting

**Problem:** Users need isolated project spaces

**Solution:**
```bash
# Per-user isolation
export BASIC_MEMORY_PROJECT_ROOT=/home/${USER}/basic-memory

# User confined to their home directory
bm project add personal /home/alice/basic-memory/personal    # ✓
bm project add other /home/bob/basic-memory/data             # ✗ Blocked
```

## Relationship with BASIC_MEMORY_HOME

`BASIC_MEMORY_HOME` and `BASIC_MEMORY_PROJECT_ROOT` serve **different purposes**:

| Variable | Purpose | Default | Example |
|----------|---------|---------|---------|
| `BASIC_MEMORY_HOME` | Default project location | `~/basic-memory` | Where "main" project lives |
| `BASIC_MEMORY_PROJECT_ROOT` | Path constraint boundary | None (unrestricted) | Security boundary |

### Using Both Together

```bash
# Typical containerized setup
export BASIC_MEMORY_PROJECT_ROOT=/app/data          # Constraint: all under /app/data
export BASIC_MEMORY_HOME=/app/data/basic-memory     # Default: main project location

# This creates main project at /app/data/basic-memory
# And ensures all other projects are also under /app/data
```

### Key Differences

**BASIC_MEMORY_HOME:**
- Sets default project path
- Used for "main" project
- Does NOT enforce constraints
- Optional - defaults to `~/basic-memory`

**BASIC_MEMORY_PROJECT_ROOT:**
- Enforces path constraints
- Validates ALL project paths
- Prevents path traversal
- Optional - if not set, no constraints

## Validation Examples

### Valid Paths (with PROJECT_ROOT=/app/data)

```bash
export BASIC_MEMORY_PROJECT_ROOT=/app/data

# Direct child
bm project add notes /app/data/notes              # ✓

# Nested child
bm project add work /app/data/projects/work       # ✓

# Relative path (resolves to /app/data/relative)
bm project add rel /app/data/relative             # ✓

# Symlink (resolves under /app/data)
ln -s /app/data/real /app/data/link
bm project add linked /app/data/link              # ✓
```

### Invalid Paths (with PROJECT_ROOT=/app/data)

```bash
export BASIC_MEMORY_PROJECT_ROOT=/app/data

# Path traversal attempt
bm project add evil /app/data/../../../etc
# ✗ Error: Path must be under /app/data

# Absolute path outside root
bm project add outside /tmp/data
# ✗ Error: Path must be under /app/data

# Symlink escaping root
ln -s /etc/passwd /app/data/evil
bm project add bad /app/data/evil
# ✗ Error: Path must be under /app/data

# Relative path escaping
bm project add sneaky /app/data/../../sneaky
# ✗ Error: Path must be under /app/data
```

## Error Messages

### Path Outside Root

```bash
$ bm project add test /tmp/test
Error: BASIC_MEMORY_PROJECT_ROOT is set to /app/data.
All projects must be created under this directory.
Invalid path: /tmp/test
```

### Escape Attempt Blocked

```bash
$ bm project add evil /app/data/../../../etc
Error: BASIC_MEMORY_PROJECT_ROOT is set to /app/data.
All projects must be created under this directory.
Invalid path: /etc
```

## Migration Guide

### Enabling PROJECT_ROOT on Existing Setup

If you have existing projects outside the desired root:

1. **Choose project root location**
   ```bash
   export BASIC_MEMORY_PROJECT_ROOT=/app/data
   ```

2. **Move existing projects**
   ```bash
   # Backup first
   cp -r ~/old-project /app/data/old-project
   ```

3. **Update config.json**
   ```bash
   # Edit ~/.basic-memory/config.json
   {
     "projects": {
       "main": "/app/data/basic-memory",
       "old-project": "/app/data/old-project"
     }
   }
   ```

4. **Verify paths**
   ```bash
   bm project list
   # All paths should be under /app/data
   ```

### Disabling PROJECT_ROOT

To remove constraints:

```bash
# Unset environment variable
unset BASIC_MEMORY_PROJECT_ROOT

# Or remove from Docker/config
# Now projects can be created anywhere again
```

## Testing Path Constraints

### Verify Configuration

```bash
# Check if PROJECT_ROOT is set
env | grep BASIC_MEMORY_PROJECT_ROOT

# Try creating project outside root (should fail)
bm project add test /tmp/test
```

### Docker Testing

```bash
# Run with constraint
docker run \
  -e BASIC_MEMORY_PROJECT_ROOT=/app/data \
  -v $(pwd)/data:/app/data \
  basic-memory:latest \
  bm project add notes /app/data/notes

# Verify in container
docker exec -it container_id env | grep PROJECT_ROOT
```

## Security Best Practices

1. **Always set in production**: Use PROJECT_ROOT in deployed environments
2. **Minimal permissions**: Set directory permissions to 700 or 750
3. **Audit project creation**: Log all project add/remove operations
4. **Regular validation**: Periodically check project paths haven't escaped
5. **Volume mounts**: Ensure PROJECT_ROOT matches Docker volume mounts

## Troubleshooting

### Projects Not Creating

**Problem:** Can't create projects with PROJECT_ROOT set

```bash
$ bm project add test /app/data/test
Error: Path must be under /app/data
```

**Solution:** Verify PROJECT_ROOT is correct
```bash
echo $BASIC_MEMORY_PROJECT_ROOT
# Should match expected path
```

### Paths Resolving Incorrectly

**Problem:** Symlinks not working as expected

**Solution:** Check symlink target
```bash
ls -la /app/data/link
# → /app/data/link -> /some/target

# Ensure target is under PROJECT_ROOT
realpath /app/data/link
```

### Docker Volume Issues

**Problem:** PROJECT_ROOT doesn't match volume mount

**Solution:** Align environment and volume
```yaml
# docker-compose.yml
environment:
  BASIC_MEMORY_PROJECT_ROOT: /app/data  # ← Must match volume mount
volumes:
  - ./data:/app/data                     # ← Mount point
```

## Implementation Details

### Path Sanitization Algorithm

```python
def sanitize_and_validate_path(path: str, project_root: str) -> str:
    """Sanitize path and validate against project root."""
    # Convert to absolute path
    base_path = Path(project_root).resolve()
    target_path = Path(path).resolve()

    # Get as POSIX string for comparison
    resolved_path = target_path.as_posix()
    base_posix = base_path.as_posix()

    # Verify resolved path is under project_root
    if not resolved_path.startswith(base_posix):
        raise ValueError(
            f"BASIC_MEMORY_PROJECT_ROOT is set to {project_root}. "
            f"All projects must be created under this directory. "
            f"Invalid path: {path}"
        )

    return resolved_path
```

### Config Loading

```python
class BasicMemoryConfig(BaseSettings):
    project_root: Optional[str] = Field(
        default=None,
        description="If set, all projects must be created underneath this directory"
    )

    model_config = SettingsConfigDict(
        env_prefix="BASIC_MEMORY_",  # Maps BASIC_MEMORY_PROJECT_ROOT
        extra="ignore",
    )
```

## See Also

- `basic-memory-home.md` - Default project location
- `env-var-overrides.md` - Environment variable precedence
- Docker deployment guide
- Security best practices
