# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.x.x   | :white_check_mark: |

## Reporting a Vulnerability

If you find a vulnerability, please contact hello@basicmachines.co.

Please do not open a public GitHub issue for security vulnerabilities. We aim
to respond within 72 hours and will coordinate a fix and disclosure timeline
with you.

## Threat Model

Basic Memory is a local-first MCP server that reads and writes markdown files
inside configured project directories. It runs on your machine with your user
permissions, so local configuration deserves the same care as any other
developer tool that can access your files.

### What Basic Memory Controls

- Filesystem-touching tools validate paths against the configured project root
  with `validate_project_path()`, resolved paths, and `Path.is_relative_to()`.
  Path traversal attempts such as `../../etc/passwd` are blocked at this layer.
- Scan optimizations in `sync_service.py` call `find` through
  `asyncio.create_subprocess_exec()` with explicit argument lists. Project paths
  are passed as data, not interpolated into shell strings.
- Auto-update code uses hardcoded commands, list-form arguments, and
  `stdin=DEVNULL`. User-controlled strings do not reach a shell there.

### MCP Client-Side Risk

Recent MCP ecosystem research has highlighted a client-side pattern where an
MCP host can be configured to run arbitrary commands as "servers." That risk is
in the host configuration, not in notes or Basic Memory tool input.

The recommended Basic Memory MCP configuration uses a known command with
explicit arguments:

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

Only add MCP server entries from sources you trust. Avoid inline shell scripts
or command strings copied from untrusted sources. Treat third-party MCP server
configuration with the same scrutiny as any locally executed program.

Related ecosystem context:

- OX Security: The Mother of All AI Supply Chains
- CSO Online: RCE by design: MCP architectural choice haunts AI agent ecosystem

### Out Of Scope

- Basic Memory does not execute note content as code. Notes are returned as
  data to the LLM.
- Basic Memory does not open network ports by default. The MCP server uses
  stdio; the optional REST API is intended for localhost use.
- Basic Memory is designed for single-user local knowledge bases and does not
  implement access controls between operating-system users.

## Secure Configuration Checklist

- MCP config `command` points to `uvx` or a trusted binary, not a shell string.
- Project paths in Basic Memory config come from trusted local configuration.
- If exposing the REST API, bind it only to localhost.
- Review any third-party MCP servers before adding them to your host config.
