# .env File Loading Removed

**Status**: Security Fix
**PR**: #330
**Impact**: Breaking change for users relying on .env files

## What Changed

v0.15.0 **removes automatic .env file loading** from Basic Memory configuration. Environment variables must now be set explicitly through your shell, systemd, Docker, or other standard mechanisms.

### Before v0.15.0

```python
# BasicMemoryConfig automatically loaded .env files
from dotenv import load_dotenv
load_dotenv()  # ← Automatically loaded .env

config = BasicMemoryConfig()  # ← Used .env values
```

### v0.15.0 and Later

```python
# No automatic .env loading
config = BasicMemoryConfig()  # ← Only uses actual environment variables
```

## Why This Changed

### Security Vulnerability

Automatic .env loading created security risks:

1. **Unintended file loading:**
   - Could load `.env` from current directory
   - Could load `.env` from parent directories
   - Risk of loading untrusted `.env` files

2. **Credential leakage:**
   - `.env` files might contain secrets
   - Easy to accidentally commit to git
   - Hard to audit what's loaded

3. **Configuration confusion:**
   - Unclear which values come from `.env` vs environment
   - Debugging difficult with implicit loading

### Best Practice

Modern deployment practices use explicit environment configuration:
- Shell exports
- systemd Environment directives
- Docker environment variables
- Kubernetes ConfigMaps/Secrets
- CI/CD variable injection

## Migration Guide

### If You Used .env Files

**Step 1: Check if you have a .env file**
```bash
ls -la .env
ls -la ~/.basic-memory/.env
```

**Step 2: Review .env contents**
```bash
cat .env
```

**Step 3: Convert to explicit environment variables**

**Option A: Shell exports (development)**
```bash
# Move values from .env to shell config
# .bashrc or .zshrc

export BASIC_MEMORY_PROJECT_ROOT=/app/data
export BASIC_MEMORY_LOG_LEVEL=DEBUG
export BASIC_MEMORY_DEFAULT_PROJECT=main
```

**Option B: direnv (recommended for development)**
```bash
# Install direnv
brew install direnv  # macOS
sudo apt install direnv  # Linux

# Create .envrc (git-ignored)
cat > .envrc <<EOF
export BASIC_MEMORY_PROJECT_ROOT=/app/data
export BASIC_MEMORY_LOG_LEVEL=DEBUG
EOF

# Allow direnv for this directory
direnv allow

# Auto-loads when entering directory
```

**Option C: systemd (production)**
```ini
# /etc/systemd/system/basic-memory.service
[Service]
Environment="BASIC_MEMORY_PROJECT_ROOT=/var/lib/basic-memory"
Environment="BASIC_MEMORY_LOG_LEVEL=INFO"
ExecStart=/usr/local/bin/basic-memory serve
```

**Option D: Docker (containers)**
```yaml
# docker-compose.yml
services:
  basic-memory:
    environment:
      BASIC_MEMORY_PROJECT_ROOT: /app/data
      BASIC_MEMORY_LOG_LEVEL: INFO
```

### If You Didn't Use .env Files

No action needed - your setup already uses explicit environment variables.

## Alternative Solutions

### Development: Use direnv

[direnv](https://direnv.net/) automatically loads environment variables when entering a directory:

**Setup:**
```bash
# Install
brew install direnv

# Add to shell (.bashrc or .zshrc)
eval "$(direnv hook bash)"  # or zsh

# Create .envrc in project
cat > .envrc <<EOF
export BASIC_MEMORY_LOG_LEVEL=DEBUG
export BASIC_MEMORY_PROJECT_ROOT=\$PWD/data
EOF

# Git-ignore it
echo ".envrc" >> .gitignore

# Allow it
direnv allow
```

**Usage:**
```bash
# Entering directory auto-loads variables
cd ~/my-project
# → direnv: loading .envrc
# → direnv: export +BASIC_MEMORY_LOG_LEVEL +BASIC_MEMORY_PROJECT_ROOT

# Check variables
env | grep BASIC_MEMORY_
```

### Production: External Configuration

**AWS Systems Manager:**
```bash
# Store in Parameter Store
aws ssm put-parameter \
  --name /basic-memory/project-root \
  --value /app/data \
  --type SecureString

# Retrieve and export
export BASIC_MEMORY_PROJECT_ROOT=$(aws ssm get-parameter \
  --name /basic-memory/project-root \
  --with-decryption \
  --query Parameter.Value \
  --output text)
```

**Kubernetes Secrets:**
```yaml
apiVersion: v1
kind: Secret
metadata:
  name: basic-memory-env
stringData:
  BASIC_MEMORY_PROJECT_ROOT: /app/data
---
apiVersion: v1
kind: Pod
spec:
  containers:
  - name: basic-memory
    envFrom:
    - secretRef:
        name: basic-memory-env
```

**HashiCorp Vault:**
```bash
# Store in Vault
vault kv put secret/basic-memory \
  project_root=/app/data \
  log_level=INFO

# Retrieve and export
export BASIC_MEMORY_PROJECT_ROOT=$(vault kv get -field=project_root secret/basic-memory)
```

## Security Best Practices

### 1. Never Commit Environment Files

**Always git-ignore:**
```bash
# .gitignore
.env
.env.*
.envrc
*.env
cloud-auth.json
```

### 2. Use Secret Management

**For sensitive values:**
- AWS Secrets Manager
- HashiCorp Vault
- Kubernetes Secrets
- Azure Key Vault
- Google Secret Manager

### 3. Scope Secrets Appropriately

**Development:**
```bash
# Development secrets (less sensitive)
export BASIC_MEMORY_LOG_LEVEL=DEBUG
export BASIC_MEMORY_PROJECT_ROOT=~/dev/data
```

**Production:**
```bash
# Production secrets (highly sensitive)
export BASIC_MEMORY_CLOUD_SECRET_KEY=$(fetch-from-vault)
export BASIC_MEMORY_PROJECT_ROOT=/app/data
```

### 4. Audit Environment Variables

**Log non-sensitive vars:**
```python
import os
from loguru import logger

# Safe to log
safe_vars = {
    k: v for k, v in os.environ.items()
    if k.startswith("BASIC_MEMORY_") and "SECRET" not in k
}
logger.info(f"Config loaded with: {safe_vars}")

# Never log
secret_vars = [k for k in os.environ.keys() if "SECRET" in k or "KEY" in k]
logger.debug(f"Secret vars present: {len(secret_vars)}")
```

### 5. Principle of Least Privilege

```bash
# ✓ Good: Minimal permissions
export BASIC_MEMORY_PROJECT_ROOT=/app/data/tenant-123  # Scoped to tenant

# ✗ Bad: Too permissive
export BASIC_MEMORY_PROJECT_ROOT=/  # Entire filesystem
```

## Troubleshooting

### Variables Not Loading

**Problem:** Settings not taking effect after migration

**Check:**
```bash
# Are variables actually exported?
env | grep BASIC_MEMORY_

# Not exported (wrong)
BASIC_MEMORY_LOG_LEVEL=DEBUG  # Missing 'export'

# Exported (correct)
export BASIC_MEMORY_LOG_LEVEL=DEBUG
```

### .env Still Present

**Problem:** Old .env file exists but ignored

**Solution:**
```bash
# Review and remove
cat .env  # Check contents
rm .env   # Remove after migrating

# Ensure git-ignored
echo ".env" >> .gitignore
```

### Different Behavior After Upgrade

**Problem:** Config different after v0.15.0

**Check for .env usage:**
```bash
# Did you have .env?
git log --all --full-history -- .env

# If yes, migrate values to explicit env vars
```

## Configuration Checklist

After removing .env files, verify:

- [ ] All required env vars exported explicitly
- [ ] .env files removed or git-ignored
- [ ] Production uses systemd/Docker/K8s env vars
- [ ] Development uses direnv or shell config
- [ ] Secrets stored in secret manager (not env files)
- [ ] No credentials committed to git
- [ ] Documentation updated with new approach

## Example Configurations

### Local Development

**~/.bashrc or ~/.zshrc:**
```bash
# Basic Memory configuration
export BASIC_MEMORY_LOG_LEVEL=DEBUG
export BASIC_MEMORY_PROJECT_ROOT=~/dev/basic-memory
export BASIC_MEMORY_DEFAULT_PROJECT=main
export BASIC_MEMORY_DEFAULT_PROJECT_MODE=true
```

### Docker Development

**docker-compose.yml:**
```yaml
services:
  basic-memory:
    image: basic-memory:latest
    environment:
      BASIC_MEMORY_LOG_LEVEL: DEBUG
      BASIC_MEMORY_PROJECT_ROOT: /app/data
      BASIC_MEMORY_HOME: /app/data/basic-memory
    volumes:
      - ./data:/app/data
```

### Production Deployment

**systemd service:**
```ini
[Unit]
Description=Basic Memory Service

[Service]
Type=simple
User=basicmemory
Environment="BASIC_MEMORY_ENV=user"
Environment="BASIC_MEMORY_LOG_LEVEL=INFO"
Environment="BASIC_MEMORY_PROJECT_ROOT=/var/lib/basic-memory"
EnvironmentFile=/etc/basic-memory/secrets.env
ExecStart=/usr/local/bin/basic-memory serve

[Install]
WantedBy=multi-user.target
```

**/etc/basic-memory/secrets.env:**
```bash
# Loaded via EnvironmentFile
BASIC_MEMORY_CLOUD_SECRET_KEY=<from-secret-manager>
```

### Kubernetes Production

**ConfigMap (non-secret):**
```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: basic-memory-config
data:
  BASIC_MEMORY_LOG_LEVEL: "INFO"
  BASIC_MEMORY_PROJECT_ROOT: "/app/data"
```

**Secret (sensitive):**
```yaml
apiVersion: v1
kind: Secret
metadata:
  name: basic-memory-secrets
type: Opaque
stringData:
  BASIC_MEMORY_CLOUD_SECRET_KEY: <base64-encoded>
```

**Deployment:**
```yaml
apiVersion: apps/v1
kind: Deployment
spec:
  template:
    spec:
      containers:
      - name: basic-memory
        envFrom:
        - configMapRef:
            name: basic-memory-config
        - secretRef:
            name: basic-memory-secrets
```

## See Also

- `env-var-overrides.md` - How environment variables work
- Security best practices documentation
- Secret management guide
- Configuration reference
