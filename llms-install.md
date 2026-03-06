# Basic Memory Installation Guide for LLMs

This guide is specifically designed to help AI assistants like Cline install and configure Basic Memory. Follow these
steps in order.

## Installation Steps

### 1. Install Basic Memory Package

Use one of the following package managers to install:

```bash
# Install with uv (recommended)
uv tool install basic-memory

# Or with pip
pip install basic-memory
```

### 2. Configure MCP Server

Add the following to your config:

```json
{
  "mcpServers": {
    "basic-memory": {
      "command": "uvx",
      "args": [
        "basic-memory",
        "mcp"
      ]
    }
  }
}
```

For Claude Desktop, this file is located at:

macOS: ~/Library/Application Support/Claude/claude_desktop_config.json
Windows: %APPDATA%\Claude\claude_desktop_config.json

### 3. Start Synchronization (optional)

To synchronize files in real-time, run:

```bash
basic-memory sync --watch
```

Or for a one-time sync:

```bash
basic-memory sync
```

### 4. Updating Basic Memory

Basic Memory supports automatic updates by default for `uv tool` and Homebrew installs.

For manual checks and upgrades:

```bash
# Check now and install if supported
bm update

# Check only, do not install
bm update --check
```

To disable automatic updates, set `"auto_update": false` in `~/.basic-memory/config.json`.

## Configuration Options

### Custom Directory

To use a directory other than the default `~/basic-memory`:

```bash
basic-memory project add custom-project /path/to/your/directory
basic-memory project default custom-project
```

### Multiple Projects

To manage multiple knowledge bases:

```bash
# List all projects
basic-memory project list

# Add a new project
basic-memory project add work ~/work-basic-memory

# Set default project
basic-memory project default work
```

## Importing Existing Data

### From Claude.ai

```bash
basic-memory import claude conversations path/to/conversations.json
basic-memory import claude projects path/to/projects.json
```

### From ChatGPT

```bash
basic-memory import chatgpt path/to/conversations.json
```

### From MCP Memory Server

```bash
basic-memory import memory-json path/to/memory.json
```

## Troubleshooting

If you encounter issues:

1. Check that Basic Memory is properly installed:
   ```bash
   basic-memory --version
   ```

2. Verify the sync process is running:
   ```bash
   ps aux | grep basic-memory
   ```

3. Check sync output for errors:
   ```bash
   basic-memory sync --verbose
   ```

4. Check log output:
   ```bash
   cat ~/.basic-memory/basic-memory.log
   ```

For more detailed information, refer to the [full documentation](https://memory.basicmachines.co/).
