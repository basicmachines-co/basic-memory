# Basic Memory Cloud CLI Guide

The Basic Memory Cloud CLI provides commands for interacting with Basic Memory Cloud instances, including authentication, project management, and file synchronization. This guide covers installation, configuration, and usage of the cloud features.

## Overview

The cloud CLI enables you to:
- Authenticate with Basic Memory Cloud using OAuth
- List and create projects on cloud instances
- Upload local files and directories to cloud projects via WebDAV
- Check the health status of cloud instances
- Automatically filter uploads using gitignore patterns

## Authentication

### Initial Setup

Before using cloud commands, you need to authenticate with Basic Memory Cloud:

```bash
bm cloud login
```

This command will:
1. Open your browser to the Basic Memory Cloud authentication page
2. Prompt you to authorize the CLI application
3. Store your authentication token locally for future use

## Project Management

### Listing Projects

View all projects on a cloud instance:

```bash
bm cloud project list
```

Example output:
```
     Cloud Projects
┏━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Name             ┃ Path                     ┃
┡━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ my-research      │ my-research              │
│ work-notes       │ work-notes               │
└──────────────────┴──────────────────────────┘

Found 2 project(s)
```

### Creating Projects

Create a new project on a cloud instance:

```bash
bm cloud project add my-new-project
```

Create and set as default:
```bash
bm cloud project add my-new-project --default
```

## File Upload

### Basic Upload

Upload files or directories to a cloud project:

```bash
# Upload a directory
bm cloud upload my-project /path/to/local/files

# Upload a single file
bm cloud upload my-project /path/to/file.md
```

### Upload Options

#### Timestamp Preservation

By default, file modification times are preserved during upload:

```bash
# Preserve timestamps (default)
bm cloud upload my-project ./docs

# Don't preserve timestamps
bm cloud upload my-project ./docs --no-preserve-timestamps
```

#### Gitignore Filtering

The CLI automatically respects `.gitignore` patterns and includes smart defaults for development artifacts:

```bash
# Respect .gitignore and defaults (default behavior)
bm cloud upload my-project ./my-repo

# Upload everything, ignore .gitignore
bm cloud upload my-project ./my-repo --no-gitignore
```

**Default ignore patterns include:**
- `.git`, `.venv`, `venv`, `env`, `.env`
- `node_modules`, `__pycache__`, `.pytest_cache`
- `*.pyc`, `*.pyo`, `*.pyd`
- `.DS_Store`, `Thumbs.db`
- `.idea`, `.vscode`
- `build`, `dist`, `.tox`, `.cache`
- `.mypy_cache`, `.ruff_cache`

### Upload Examples

```bash
# Upload a local knowledge base
bm cloud upload my-research ~/Documents/research

# Upload specific documentation, preserving structure
bm cloud upload docs-project ./docs

# Upload without gitignore filtering for a complete backup
bm cloud upload backup-project ./ --no-gitignore
```

### Upload Output

During upload, you'll see progress and filtering information:

```bash
$ bm cloud upload my-project ./my-repo

Ignored 45 file(s) based on .gitignore and default patterns
Uploading 23 file(s) to project 'my-project' on https://cloud.basicmemory.com...
  ✓ README.md
  ✓ src/main.py
  ✓ src/utils.py
  ✓ docs/guide.md
  ...
Successfully uploaded 23 file(s)!
```

## Instance Management

### Health Check

Check if a cloud instance is healthy and get version information:

```bash
bm cloud status
```

Example output:
```
Cloud instance is healthy
  Status: ok
  Version: 0.14.4
  Timestamp: 2024-01-15T10:30:00Z
```

## WebDAV Protocol

File uploads use the WebDAV protocol for efficient, resumable file transfers. The CLI handles:

- Directory structure preservation
- File metadata preservation (timestamps)
- Error handling and retry logic
- Progress reporting

### WebDAV Endpoints

Files are uploaded to: `{host_url}/{project}/webdav/{file_path}`

Example:
- Host: `https://cloud.basicmemory.com`
- Project: `my-research`
- File: `docs/notes.md`
- WebDAV URL: `https://cloud.basicmemory.com/proxy/my-research/webdav/docs/notes.md`

### Authentication Configuration

By default, the CLI uses production authentication settings. For development or custom deployments, you can override these settings.

#### Production vs Development

- **Production** (default): Uses `client_01K4DGBWAZWP83N3H8VVEMRX6W` and `https://eloquent-lotus-05.authkit.app`
- **Development**: Uses `client_01K46RED2BW9YKYE4N7Y9BDN2V` and `https://exciting-aquarium-32-staging.authkit.app`

#### Environment Variables


```bash
# For development environment
export BASIC_MEMORY_CLOUD_HOST="http://development.cloud.basicmemory.com"
export BASIC_MEMORY_CLOUD_CLIENT_ID="client_01K46RED2BW9YKYE4N7Y9BDN2V"
export BASIC_MEMORY_CLOUD_DOMAIN="https://exciting-aquarium-32-staging.authkit.app"

bm cloud login
```

#### Configuration File

You can also set the values in `~/.basic-memory/config.json`:

development
```json
{
  "cloud_host": "http://development.cloud.basicmemory.com", 
  "cloud_client_id": "client_01K46RED2BW9YKYE4N7Y9BDN2V",
  "cloud_domain": "https://exciting-aquarium-32-staging.authkit.app"
}
```

## Troubleshooting

### Authentication Issues

**Problem**: "Not authenticated" errors
**Solution**: Re-run the login command:
```bash
bm cloud login
```

**Problem**: Wrong environment (dev vs prod)
**Solution**: Check and set the correct environment variables or config

### Upload Issues

**Problem**: "No files found to upload"
**Solution**: Check gitignore filtering or use `--no-gitignore`:
```bash
bm cloud upload my-project ./path --no-gitignore
```

**Problem**: Upload timeouts
**Solution**: The CLI uses a 5-minute timeout for large uploads. For very large files, consider breaking them into smaller chunks.

### Connection Issues

**Problem**: "API request failed" errors
**Solution**:
1. Verify the cloud instance is running: `bm cloud status`
2. Check your internet connection


## Security

- All communication uses HTTPS
- OAuth 2.1 with PKCE provides secure authentication
- Tokens automatically refresh when needed
- Tokens are stored locally in `~/.basic-memory/basic-memory-cloud.json`

## Command Reference

```bash
# Authentication
bm cloud login

# Project management
bm cloud project list
bm cloud project add <name> [--default]

# File operations
bm cloud upload <project> <path>  [--no-preserve-timestamps] [--no-gitignore]

# Instance management
bm cloud status 
```

For more information about Basic Memory Cloud, visit the [official documentation](https://memory.basicmachines.co).
