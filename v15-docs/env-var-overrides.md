# Environment Variable Overrides

**Status**: Fixed in v0.15.0
**PR**: #334 (part of PROJECT_ROOT implementation)

## What Changed

v0.15.0 fixes configuration loading to properly respect environment variable overrides. Environment variables with the `BASIC_MEMORY_` prefix now correctly override values in `config.json`.

## How It Works

### Precedence Order (Highest to Lowest)

1. **Environment Variables** (`BASIC_MEMORY_*`)
2. **Config File** (`~/.basic-memory/config.json`)
3. **Default Values** (Built-in defaults)

### Example

```bash
# config.json contains:
{
  "default_project": "main",
  "log_level": "INFO"
}

# Environment overrides:
export BASIC_MEMORY_DEFAULT_PROJECT=work
export BASIC_MEMORY_LOG_LEVEL=DEBUG

# Result:
# default_project = "work"     ← from env var
# log_level = "DEBUG"           ← from env var
```

## Environment Variable Naming

All environment variables use the prefix `BASIC_MEMORY_` followed by the config field name in UPPERCASE:

| Config Field | Environment Variable | Example |
|--------------|---------------------|---------|
| `default_project` | `BASIC_MEMORY_DEFAULT_PROJECT` | `BASIC_MEMORY_DEFAULT_PROJECT=work` |
| `log_level` | `BASIC_MEMORY_LOG_LEVEL` | `BASIC_MEMORY_LOG_LEVEL=DEBUG` |
| `project_root` | `BASIC_MEMORY_PROJECT_ROOT` | `BASIC_MEMORY_PROJECT_ROOT=/app/data` |
| `api_url` | `BASIC_MEMORY_API_URL` | `BASIC_MEMORY_API_URL=https://api.example.com` |
| `default_project_mode` | `BASIC_MEMORY_DEFAULT_PROJECT_MODE` | `BASIC_MEMORY_DEFAULT_PROJECT_MODE=true` |

## Common Use Cases

### Development vs Production

**Development (.env or shell):**
```bash
export BASIC_MEMORY_LOG_LEVEL=DEBUG
export BASIC_MEMORY_API_URL=http://localhost:8000
```

**Production (systemd/docker):**
```bash
export BASIC_MEMORY_LOG_LEVEL=INFO
export BASIC_MEMORY_API_URL=https://api.basicmemory.cloud
export BASIC_MEMORY_PROJECT_ROOT=/app/data
```

### CI/CD Pipelines

```bash
# GitHub Actions
env:
  BASIC_MEMORY_ENV: test
  BASIC_MEMORY_LOG_LEVEL: DEBUG

# GitLab CI
variables:
  BASIC_MEMORY_ENV: test
  BASIC_MEMORY_PROJECT_ROOT: /builds/project/data
```

### Docker Deployments

```bash
# docker run
docker run \
  -e BASIC_MEMORY_HOME=/app/data/main \
  -e BASIC_MEMORY_PROJECT_ROOT=/app/data \
  -e BASIC_MEMORY_LOG_LEVEL=INFO \
  basic-memory:latest

# docker-compose.yml
services:
  basic-memory:
    environment:
      BASIC_MEMORY_HOME: /app/data/main
      BASIC_MEMORY_PROJECT_ROOT: /app/data
      BASIC_MEMORY_LOG_LEVEL: INFO
```

### Kubernetes

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: basic-memory-env
data:
  BASIC_MEMORY_LOG_LEVEL: "INFO"
  BASIC_MEMORY_PROJECT_ROOT: "/app/data"
---
apiVersion: apps/v1
kind: Deployment
spec:
  template:
    spec:
      containers:
      - name: basic-memory
        envFrom:
        - configMapRef:
            name: basic-memory-env
```

## Available Environment Variables

### Core Configuration

```bash
# Environment mode
export BASIC_MEMORY_ENV=user              # test, dev, user

# Project configuration
export BASIC_MEMORY_DEFAULT_PROJECT=main
export BASIC_MEMORY_DEFAULT_PROJECT_MODE=true

# Path constraints
export BASIC_MEMORY_HOME=/path/to/main
export BASIC_MEMORY_PROJECT_ROOT=/path/to/root
```

### Sync Configuration

```bash
# Sync behavior
export BASIC_MEMORY_SYNC_CHANGES=true
export BASIC_MEMORY_SYNC_DELAY=1000
export BASIC_MEMORY_SYNC_THREAD_POOL_SIZE=4

# Watch service
export BASIC_MEMORY_WATCH_PROJECT_RELOAD_INTERVAL=30
```

### Feature Flags

```bash
# Permalinks
export BASIC_MEMORY_UPDATE_PERMALINKS_ON_MOVE=false
export BASIC_MEMORY_DISABLE_PERMALINKS=false
export BASIC_MEMORY_KEBAB_FILENAMES=false

# Performance
export BASIC_MEMORY_SKIP_INITIALIZATION_SYNC=false
```

### API Configuration

```bash
# Remote API
export BASIC_MEMORY_API_URL=https://api.basicmemory.cloud

# Cloud configuration
export BASIC_MEMORY_CLOUD_CLIENT_ID=client_abc123
export BASIC_MEMORY_CLOUD_DOMAIN=https://auth.example.com
export BASIC_MEMORY_CLOUD_HOST=https://api.example.com
```

### Logging

```bash
# Log level
export BASIC_MEMORY_LOG_LEVEL=DEBUG       # DEBUG, INFO, WARNING, ERROR
```

## Override Examples

### Temporarily Override for Testing

```bash
# One-off override
BASIC_MEMORY_LOG_LEVEL=DEBUG bm sync

# Session override
export BASIC_MEMORY_DEFAULT_PROJECT=test-project
bm tools search --query "test"
unset BASIC_MEMORY_DEFAULT_PROJECT
```

### Override in Scripts

```bash
#!/bin/bash

# Override for this script execution
export BASIC_MEMORY_LOG_LEVEL=DEBUG
export BASIC_MEMORY_API_URL=http://localhost:8000

# Run commands
bm sync
bm tools search --query "development"
```

### Per-Environment Config

**~/.bashrc (development):**
```bash
export BASIC_MEMORY_ENV=dev
export BASIC_MEMORY_LOG_LEVEL=DEBUG
export BASIC_MEMORY_HOME=~/dev/basic-memory-dev
```

**Production systemd:**
```ini
[Service]
Environment="BASIC_MEMORY_ENV=user"
Environment="BASIC_MEMORY_LOG_LEVEL=INFO"
Environment="BASIC_MEMORY_HOME=/var/lib/basic-memory"
Environment="BASIC_MEMORY_PROJECT_ROOT=/var/lib"
```

## Verification

### Check Current Values

```bash
# View all BASIC_MEMORY_ env vars
env | grep BASIC_MEMORY_

# Check specific value
echo $BASIC_MEMORY_PROJECT_ROOT
```

### Verify Override Working

```python
from basic_memory.config import ConfigManager

# Load config
config = ConfigManager().config

# Check values
print(f"Project root: {config.project_root}")
print(f"Log level: {config.log_level}")
print(f"Default project: {config.default_project}")
```

### Debug Configuration Loading

```python
import os
from basic_memory.config import ConfigManager

# Check what env vars are set
env_vars = {k: v for k, v in os.environ.items() if k.startswith("BASIC_MEMORY_")}
print("Environment variables:", env_vars)

# Load config and see what won
config = ConfigManager().config
print("Resolved config:", config.model_dump())
```

## Migration from v0.14.x

### Previous Behavior (Bug)

In v0.14.x, environment variables were sometimes ignored:

```bash
# v0.14.x bug
export BASIC_MEMORY_PROJECT_ROOT=/app/data
# → config.json value used instead (wrong!)
```

### Fixed Behavior (v0.15.0+)

```bash
# v0.15.0+ correct
export BASIC_MEMORY_PROJECT_ROOT=/app/data
# → Environment variable properly overrides config.json
```

**No action needed** - Just verify env vars are working as expected.

## Configuration Loading Details

### Loading Process

1. **Load defaults** from Pydantic model
2. **Load config.json** if it exists
3. **Apply environment overrides** (BASIC_MEMORY_* variables)
4. **Validate and return** merged configuration

### Implementation

```python
class BasicMemoryConfig(BaseSettings):
    # Fields with defaults
    default_project: str = Field(default="main")
    log_level: str = "INFO"

    model_config = SettingsConfigDict(
        env_prefix="BASIC_MEMORY_",  # Maps env vars
        extra="ignore",
    )

# Loading logic (simplified)
class ConfigManager:
    def load_config(self) -> BasicMemoryConfig:
        # 1. Load file data
        file_data = json.loads(config_file.read_text())

        # 2. Load env data
        env_dict = BasicMemoryConfig().model_dump()

        # 3. Merge (env takes precedence)
        merged_data = file_data.copy()
        for field_name in BasicMemoryConfig.model_fields.keys():
            env_var_name = f"BASIC_MEMORY_{field_name.upper()}"
            if env_var_name in os.environ:
                merged_data[field_name] = env_dict[field_name]

        return BasicMemoryConfig(**merged_data)
```

## Troubleshooting

### Environment Variable Not Taking Effect

**Problem:** Set env var but config.json value still used

**Check:**
```bash
# Is the variable exported?
env | grep BASIC_MEMORY_PROJECT_ROOT

# Exact name (case-sensitive)?
export BASIC_MEMORY_PROJECT_ROOT=/app/data  # ✓
export basic_memory_project_root=/app/data  # ✗ (wrong case)
```

**Solution:** Ensure variable is exported and named correctly

### Config.json Overwriting Env Vars

**Problem:** Changing config.json overrides env vars

**v0.14.x:** This was a bug - config.json would override env vars

**v0.15.0+:** Fixed - env vars always win

**Verify:**
```python
import os
os.environ["BASIC_MEMORY_LOG_LEVEL"] = "DEBUG"

from basic_memory.config import ConfigManager
config = ConfigManager().config
print(config.log_level)  # Should be "DEBUG"
```

### Cache Issues

**Problem:** Changes not reflected after config update

**Solution:** Clear config cache
```python
from basic_memory import config as config_module
config_module._config = None  # Clear cache

# Reload
config = ConfigManager().config
```

## Best Practices

1. **Use env vars for environment-specific settings:**
   - Different values for dev/staging/prod
   - Secrets and credentials
   - Deployment-specific paths

2. **Use config.json for stable settings:**
   - User preferences
   - Project definitions (can be overridden by env)
   - Feature flags that rarely change

3. **Document required env vars:**
   - List in README or deployment docs
   - Provide .env.example file

4. **Validate in scripts:**
   ```bash
   if [ -z "$BASIC_MEMORY_PROJECT_ROOT" ]; then
     echo "Error: BASIC_MEMORY_PROJECT_ROOT not set"
     exit 1
   fi
   ```

5. **Use consistent naming:**
   - Always use BASIC_MEMORY_ prefix
   - Match config.json field names (uppercase)

## Security Considerations

1. **Never commit env vars with secrets:**
   ```bash
   # .env (not committed)
   BASIC_MEMORY_CLOUD_SECRET_KEY=secret123

   # .gitignore
   .env
   ```

2. **Use secret management for production:**
   ```bash
   # Kubernetes secrets
   kubectl create secret generic basic-memory-secrets \
     --from-literal=api-key=$API_KEY

   # Reference in deployment
   env:
   - name: BASIC_MEMORY_API_KEY
     valueFrom:
       secretKeyRef:
         name: basic-memory-secrets
         key: api-key
   ```

3. **Audit environment in logs:**
   ```python
   # Don't log secret values
   env_vars = {
       k: "***" if "SECRET" in k else v
       for k, v in os.environ.items()
       if k.startswith("BASIC_MEMORY_")
   }
   logger.info(f"Config loaded with env: {env_vars}")
   ```

## See Also

- `project-root-env-var.md` - BASIC_MEMORY_PROJECT_ROOT usage
- `basic-memory-home.md` - BASIC_MEMORY_HOME usage
- Configuration reference documentation
