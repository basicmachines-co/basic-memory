# Security

## Automated Dependency Installation

On first startup, this plugin checks whether the `bm` (Basic Memory) CLI is available on your system. If it's not found, the plugin runs `uv tool install` to install it automatically.

### What happens

1. The plugin checks if `bm` exists on `PATH`
2. If missing, it executes `uv tool install basic-memory --force`
3. This installs the Basic Memory CLI into uv's managed tool directory (`~/.local/bin/bm` on most systems)

### What this means

- The plugin uses Node.js `child_process.execSync` to run `uv` as a shell command
- This requires `uv` (the Python package manager from Astral) to be installed on your system
- The installation pulls the public `basic-memory` package from PyPI
- If `uv` is not installed, the step is skipped gracefully — no error, no crash

### If you prefer manual installation

You can install Basic Memory yourself before enabling the plugin:

```bash
uv tool install basic-memory
```

Or with pip:

```bash
pip install basic-memory
```

Once `bm` is on your PATH, the plugin will find it and skip the auto-install step entirely.

### Opting out

If you want to control the exact binary the plugin uses, set `bmPath` in your plugin config to an absolute path:

```json5
{
  "openclaw-basic-memory": {
    config: {
      bmPath: "/usr/local/bin/bm"
    }
  }
}
```

The plugin will use that path directly and never attempt auto-installation.

## Data Handling

- All knowledge graph data is stored as plain Markdown files on your local filesystem
- The plugin spawns a persistent `bm mcp` process that indexes files into a local SQLite database
- No data leaves your machine unless you explicitly configure [Basic Memory Cloud](./BASIC_MEMORY.md)
- The plugin does not collect telemetry

## Reporting Issues

If you find a security issue, please email security@basicmemory.com or open a private advisory on GitHub.
