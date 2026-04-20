# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.x.x   | :white_check_mark: |

## Reporting a Vulnerability

If you find a vulnerability, please contact hello@basicmachines.co

Do not open a public GitHub issue for security vulnerabilities. We aim to respond within 72 hours and will coordinate a fix and disclosure timeline with you.

## Threat Model

Basic Memory is a **local-first MCP server** that reads and writes markdown files on your filesystem. Understanding the threat model helps users and enterprise evaluators assess risk appropriately.

### What Basic Memory controls

- **Filesystem access is scoped to configured project directories.** All filesystem-touching MCP tools (`read_note`, `write_note`, `edit_note`, `delete_note`, `read_content`, etc.) validate paths via `validate_project_path()`, which resolves symlinks and checks that the resolved path is relative to the configured project root using `Path.is_relative_to()`. Path traversal attacks (e.g., `../../etc/passwd`) are blocked at this layer.
- **Subprocess invocations use exec, not shell.** File-system optimization helpers (`find` invocations in `sync_service.py`) use `asyncio.create_subprocess_exec` with explicit argument lists, not `create_subprocess_shell` with interpolated strings. This prevents shell injection even if a project path contains shell metacharacters.
- **Auto-update uses hardcoded commands.** `auto_update.py` uses list-form args, `stdin=DEVNULL`, and hardcoded command names — no user-controlled strings reach the shell.

### The MCP client-side threat (does not affect Basic Memory directly)

Recent security research ([OX Security](https://www.ox.security/blog/the-mother-of-all-ai-supply-chains-critical-systemic-vulnerability-at-the-core-of-the-mcp/), [CSO Online](https://www.csoonline.com/article/4159889/rce-by-design-mcp-architectural-choice-haunts-ai-agent-ecosystem.html)) has highlighted a pattern where **MCP clients** (e.g., Claude Desktop, Cursor) can be configured to run arbitrary shell commands as "MCP servers." This is a client-side concern: a malicious `mcp` config entry could execute anything the user's shell can run.

Basic Memory is the **server** side of this relationship. The recommended install pattern:

```json
{
  "mcpServers": {
    "basic-memory": {
      "command": "uvx",
      "args": ["basic-memory", "mcp"]
    }
  }
}
```

...runs a known, pinned package via `uvx`. Users should:
- Only add MCP server entries from sources they trust.
- Avoid config entries where `command` or `args` are arbitrary shell strings sourced from untrusted input.
- Prefer `uvx`-based installs over inline shell scripts in MCP config.

### What is out of scope

- **Prompt injection via LLM input**: Basic Memory does not execute content from notes as code. Notes are data; the MCP tools that read them return strings to the LLM, not to a shell.
- **Network exposure**: Basic Memory does not open network ports by default. The MCP server communicates over stdio. The optional REST API (used internally) binds to localhost only.
- **Multi-user isolation**: Basic Memory is designed for single-user, local use. It does not implement access controls between OS users.

## Secure Configuration Checklist

- [ ] MCP config `command` points to `uvx` or a full path to a trusted binary — not a shell string.
- [ ] Project paths in Basic Memory config do not contain untrusted user input.
- [ ] If exposing the REST API, bind only to localhost (default behavior).
- [ ] Review any third-party MCP servers in your config with the same scrutiny as any locally executed program.
